"""Independent tenant identities, explicit module entitlements and platform support.

Authorization snapshots are intentionally static. Historical actors keep their
IDs; cloned tenant identities receive mapping audit events and unknown temporary
passwords. No upstream India logout or data migration is performed.
"""
from __future__ import annotations
import hashlib
import json
import secrets
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = 'c4d8e0a1b295'
down_revision = 'b2e6c9148a70'
branch_labels = None
depends_on = None

CONTENT = frozenset({'dramas.view','dramas.edit','dramas.upload','dramas.publish','dramas.delete','dramas.pricing','episodes.delete','reviews.view','reviews.decide','tags.view','tags.manage','groups.view','groups.manage'})
INDIA = frozenset({'dramas.view','dramas.edit','dramas.upload','dramas.publish','dramas.pricing','reviews.view','reviews.decide'})
GLOBAL = frozenset({'dashboard.view','users.view','users.manage','home_ops.view','home_ops.manage','rewards.view','rewards.manage','comments.view','comments.manage','moderation.view','moderation.manage','finance.view','settlements.view','settlements.manage','plans.view','plans.manage','staff.view','staff.manage','api_keys.view','api_keys.manage','suppliers.view','suppliers.manage'})
OLD_READ = frozenset(key for key in GLOBAL if key.endswith('.view')) - {'staff.view','api_keys.view','settlements.view'}
OLD_OPS = frozenset({'dashboard.view','home_ops.view','home_ops.manage','rewards.view','rewards.manage','comments.view','comments.manage','moderation.view'})
OLD_FINANCE = frozenset({'dashboard.view','users.view','finance.view','settlements.view','settlements.manage','plans.view'})
SYSTEM = frozenset({'staff.view','staff.manage','api_keys.view','api_keys.manage','roles.view','roles.manage'})
OLD_IAM = frozenset({'staff.view','staff.manage','api_keys.view','api_keys.manage'})
LEGACY_MODULES = ('dashboard','users','home_ops','rewards','comments','moderation','finance','settlements','plans','suppliers')

def role_id(tenant_id, key):
    return 'role-'+hashlib.sha256((tenant_id+':'+key).encode()).hexdigest()[:40]

def unknown_password_hash():
    # The random plaintext is deliberately neither retained nor logged.
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', secrets.token_urlsafe(48).encode(), salt.encode(), 120_000)
    return 'pbkdf2_sha256$120000$'+salt+'$'+digest.hex()

def old_capabilities(tenant):
    return INDIA if tenant['kind'] == 'india_legacy' else CONTENT | GLOBAL if tenant['id'] == 'jjtv' else CONTENT

def business_capabilities(tenant):
    return set(old_capabilities(tenant)) - OLD_IAM

def snapshot(tenant, user, role):
    permissions = role['permissions']
    if not isinstance(permissions,list) or not all(isinstance(item,str) for item in permissions):
        raise RuntimeError('Cannot migrate malformed tenant role permissions')
    keys = set(permissions) & old_capabilities(tenant)
    if user['role'] == 'admin': return set(old_capabilities(tenant)) - OLD_IAM
    if user['role'] not in {'ops','finance'}: return set()
    if tenant['id'] == 'jjtv':
        ceiling = OLD_OPS if user['role'] == 'ops' else OLD_FINANCE
        keys &= CONTENT | OLD_READ | ceiling
    return keys - OLD_IAM

def audit_mapping(bind, tenant_id, new_id, old_id, old_role_id, new_role_id):
    bind.execute(sa.text("""INSERT INTO audit_logs(actor_type,actor_id,actor_scope,tenant_id,action,target_type,target_id,payload)
        VALUES ('system',NULL,'system',:tenant,'tenant.identity.migrated','admin_user',:target,CAST(:payload AS jsonb))"""),
        dict(tenant=tenant_id,target=new_id,payload=json.dumps(dict(source_admin_user_id=old_id,migrated_admin_user_id=new_id,source_role_id=old_role_id,migrated_role_id=new_role_id))))

def upgrade():
    bind = op.get_bind()
    # Freeze the authorization graph before taking the effective-grant snapshot.
    bind.execute(sa.text('LOCK TABLE tenants,admin_users,tenant_roles,tenant_members,admin_api_keys,audit_logs,india_connections IN ACCESS EXCLUSIVE MODE'))
    tenants = {row['id']:dict(row) for row in bind.execute(sa.text('SELECT id,name,kind FROM tenants')).mappings()}
    if 'jjtv' not in tenants: raise RuntimeError('The existing JJTV business tenant is required for an explicit legacy binding')
    users = {row['id']:dict(row) for row in bind.execute(sa.text('SELECT id,username,password_hash,display_name,role,status,supplier_id FROM admin_users')).mappings()}
    roles = {row['id']:dict(row) for row in bind.execute(sa.text('SELECT id,tenant_id,name,builtin_key,permissions,version FROM tenant_roles')).mappings()}
    members = [dict(row) for row in bind.execute(sa.text('SELECT tenant_id,user_id,role,role_id FROM tenant_members')).mappings()]
    sequence = bind.scalar(sa.text("SELECT pg_get_serial_sequence('admin_users','id')"))
    if sequence and users:
        previous = bind.scalar(sa.text('SELECT pg_sequence_last_value(CAST(:sequence AS regclass))'),dict(sequence=sequence)) or 0
        bind.execute(sa.text('SELECT setval(CAST(:sequence AS regclass),:value,true)'),dict(sequence=sequence,value=max(max(users),previous)))
    member_snapshots = [(member, snapshot(tenants[member['tenant_id']], users[member['user_id']], roles[member['role_id']])) for member in members]

    op.add_column('tenants', sa.Column('code',sa.String(64),nullable=True))
    op.add_column('tenants', sa.Column('version',sa.Integer(),nullable=False,server_default='1'))
    bind.execute(sa.text('UPDATE tenants SET code=id'))
    op.alter_column('tenants','code',existing_type=sa.String(64),nullable=False)
    op.create_unique_constraint('uq_tenants_code','tenants',['code'])
    op.add_column('tenant_roles',sa.Column('kind',sa.String(16),nullable=False,server_default='business'))
    op.drop_constraint('ck_tenant_roles_builtin_key','tenant_roles',type_='check')
    op.create_check_constraint('ck_tenant_roles_builtin_key','tenant_roles',"builtin_key IS NULL OR builtin_key IN ('admin','ops','finance','tenant_admin')")
    op.create_check_constraint('ck_tenant_roles_kind','tenant_roles',"kind IN ('business','tenant_admin')")
    op.create_check_constraint('ck_tenant_roles_system_kind','tenant_roles',"(kind = 'tenant_admin' AND builtin_key IS NOT NULL AND builtin_key = 'tenant_admin') OR (kind = 'business' AND (builtin_key IS NULL OR builtin_key <> 'tenant_admin'))")

    op.add_column('admin_users',sa.Column('account_scope',sa.String(16),nullable=True,server_default='tenant'))
    op.add_column('admin_users',sa.Column('tenant_id',sa.String(64),nullable=True))
    op.add_column('admin_users',sa.Column('auth_version',sa.Integer(),nullable=False,server_default='1'))
    op.add_column('admin_users',sa.Column('must_change_password',sa.Boolean(),nullable=False,server_default='false'))
    bind.execute(sa.text("UPDATE admin_users SET account_scope=CASE WHEN role='admin' THEN 'platform' ELSE 'tenant' END, tenant_id=CASE WHEN role='admin' THEN NULL ELSE 'jjtv' END"))
    for constraint in sa.inspect(bind).get_unique_constraints('admin_users'):
        if constraint['column_names'] == ['username']:
            op.drop_constraint(constraint['name'],'admin_users',type_='unique')

    op.add_column('audit_logs',sa.Column('tenant_id',sa.String(64),nullable=True))
    op.add_column('audit_logs',sa.Column('actor_scope',sa.String(16),nullable=True))
    bind.execute(sa.text("""UPDATE audit_logs AS a SET actor_scope='legacy', tenant_id=CASE
        WHEN EXISTS(SELECT 1 FROM tenants WHERE id=a.payload->>'tenant_id') THEN a.payload->>'tenant_id'
        WHEN a.target_type='admin_user' AND a.target_id IS NOT NULL THEN (SELECT tenant_id FROM admin_users WHERE id=a.target_id)
        WHEN a.target_type='api_key' AND a.target_id IS NOT NULL THEN (SELECT u.tenant_id FROM admin_api_keys k JOIN admin_users u ON u.id=k.admin_user_id WHERE k.id=a.target_id)
        WHEN a.action LIKE 'platform.%' OR (a.target_id IS NULL AND (a.action ~ '^(auth[.]|login|logout|staff_login|admin_login)' OR a.target_type IN ('platform','session','login'))) THEN NULL
        ELSE 'jjtv' END"""))
    op.create_foreign_key('fk_audit_logs_tenant','audit_logs','tenants',['tenant_id'],['id'],ondelete='RESTRICT')
    op.create_index('ix_audit_logs_tenant_created','audit_logs',['tenant_id','created_at'])

    op.create_table('tenant_modules',
        sa.Column('tenant_id',sa.String(64),sa.ForeignKey('tenants.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('module_key',sa.String(32),primary_key=True),
        sa.Column('enabled',sa.Boolean(),nullable=False,server_default='false'))
    op.create_table('legacy_business_bindings',
        sa.Column('module_key',sa.String(32),primary_key=True),
        sa.Column('tenant_id',sa.String(64),sa.ForeignKey('tenants.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_table('tenant_domains',
        sa.Column('id',sa.String(64),primary_key=True),
        sa.Column('tenant_id',sa.String(64),sa.ForeignKey('tenants.id',ondelete='CASCADE'),nullable=False),
        sa.Column('hostname',sa.String(253),nullable=False),
        sa.Column('kind',sa.String(16),nullable=False),
        sa.Column('status',sa.String(16),nullable=False,server_default='pending'),
        sa.Column('verified_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('admin','site')",name='ck_tenant_domains_kind'),
        sa.CheckConstraint("status IN ('pending','active')",name='ck_tenant_domains_status'),
        sa.CheckConstraint("status <> 'active' OR verified_at IS NOT NULL",name='ck_tenant_domains_verified_active'))
    op.create_index('uq_tenant_domains_hostname_lower','tenant_domains',[sa.text('lower(hostname)')],unique=True)

    for tenant in tenants.values():
        modules = ['dramas','reviews'] if tenant['kind'] == 'india_legacy' else ['dramas','reviews','tags','groups']
        if tenant['id'] == 'jjtv': modules += list(LEGACY_MODULES)
        for module in modules:
            bind.execute(sa.text('INSERT INTO tenant_modules(tenant_id,module_key,enabled) VALUES (:tenant,:module,true)'),dict(tenant=tenant['id'],module=module))
    for module in LEGACY_MODULES:
        bind.execute(sa.text("INSERT INTO legacy_business_bindings(module_key,tenant_id) VALUES (:module,'jjtv')"),dict(module=module))

    # Business roles lose old platform-IAM bits. Per-member variants below retain
    # the exact old effective grant when a shared role had a global-role ceiling.
    for role in roles.values():
        new_keys = set(role['permissions']) & business_capabilities(tenants[role['tenant_id']])
        bind.execute(sa.text('UPDATE tenant_roles SET permissions=CAST(:keys AS jsonb) WHERE id=:id'),dict(keys=json.dumps(sorted(new_keys)),id=role['id']))
        role['new_permissions'] = new_keys
    bind.execute(sa.text('DELETE FROM india_connections'))
    bind.execute(sa.text('DELETE FROM tenant_members'))
    compatibility_roles = {}
    for member, keys in member_snapshots:
        tenant_id, old_user_id = member['tenant_id'],member['user_id']
        user, role = users[old_user_id],roles[member['role_id']]
        if user['role'] == 'admin': continue  # Platform users enter an audited support context.
        if user['role'] == 'partner' and tenant_id != 'jjtv':
            raise RuntimeError('A legacy partner membership outside JJTV needs an explicit supplier migration')
        effective_role_id = role['id']
        if role['new_permissions'] != keys:
            signature = (tenant_id,role['id'],tuple(sorted(keys)))
            if signature not in compatibility_roles:
                suffix = hashlib.sha256(json.dumps(signature,ensure_ascii=False).encode()).hexdigest()[:16]
                effective_role_id = 'role-migration-'+suffix
                name = role['name'][:50]+' · 迁移兼容 '+suffix[:8]
                bind.execute(sa.text("INSERT INTO tenant_roles(id,tenant_id,name,builtin_key,permissions,version,kind) VALUES (:id,:tenant,:name,NULL,CAST(:keys AS jsonb),1,'business')"),dict(id=effective_role_id,tenant=tenant_id,name=name,keys=json.dumps(sorted(keys))))
                compatibility_roles[signature] = effective_role_id
            effective_role_id = compatibility_roles[signature]
        new_user_id = old_user_id
        if tenant_id != 'jjtv':
            new_user_id = bind.execute(sa.text("""INSERT INTO admin_users(username,password_hash,display_name,role,status,supplier_id,account_scope,tenant_id,auth_version,must_change_password)
                VALUES (:username,:password,:display_name,:role,:status,NULL,'tenant',:tenant,1,true) RETURNING id"""),
                dict(username=user['username'],password=unknown_password_hash(),display_name=user['display_name'],role=user['role'],status=user['status'],tenant=tenant_id)).scalar_one()
        bind.execute(sa.text('INSERT INTO tenant_members(tenant_id,user_id,role,role_id) VALUES (:tenant,:user,:legacy_role,:role_id)'),dict(tenant=tenant_id,user=new_user_id,legacy_role=member['role'],role_id=effective_role_id))
        if new_user_id != old_user_id or effective_role_id != role['id']:
            audit_mapping(bind,tenant_id,new_user_id,old_user_id,role['id'],effective_role_id)

    for tenant in tenants.values():
        admin_role = role_id(tenant['id'],'tenant_admin')
        existing_names = set(bind.execute(sa.text('SELECT name FROM tenant_roles WHERE tenant_id=:tenant'),dict(tenant=tenant['id'])).scalars())
        admin_name, suffix = '租户管理员',1
        while admin_name in existing_names:
            admin_name = '租户管理员（系统'+str(suffix)+'）'; suffix += 1
        bind.execute(sa.text("INSERT INTO tenant_roles(id,tenant_id,name,builtin_key,permissions,version,kind) VALUES (:id,:tenant,:name,'tenant_admin',CAST(:keys AS jsonb),1,'tenant_admin')"),dict(id=admin_role,tenant=tenant['id'],name=admin_name,keys=json.dumps(sorted(business_capabilities(tenant)|SYSTEM))))
        usernames = set(bind.execute(sa.text("SELECT username FROM admin_users WHERE account_scope='tenant' AND tenant_id=:tenant"),dict(tenant=tenant['id'])).scalars())
        username, index = 'tenant-admin',1
        while username in usernames:
            username='tenant-admin-'+str(index);index+=1
        new_user_id = bind.execute(sa.text("""INSERT INTO admin_users(username,password_hash,display_name,role,status,supplier_id,account_scope,tenant_id,auth_version,must_change_password)
            VALUES (:username,:password,'租户管理员','ops','active',NULL,'tenant',:tenant,1,true) RETURNING id"""),dict(username=username,password=unknown_password_hash(),tenant=tenant['id'])).scalar_one()
        bind.execute(sa.text("INSERT INTO tenant_members(tenant_id,user_id,role,role_id) VALUES (:tenant,:user,'admin',:role_id)"),dict(tenant=tenant['id'],user=new_user_id,role_id=admin_role))
        audit_mapping(bind,tenant['id'],new_user_id,None,None,admin_role)

    op.alter_column('admin_users','account_scope',existing_type=sa.String(16),nullable=False)
    op.create_foreign_key('fk_admin_users_tenant','admin_users','tenants',['tenant_id'],['id'],ondelete='RESTRICT')
    op.create_check_constraint('ck_admin_users_account_scope','admin_users',"account_scope IN ('platform','tenant')")
    op.create_check_constraint('ck_admin_users_account_tenant','admin_users',"(account_scope='platform' AND tenant_id IS NULL) OR (account_scope='tenant' AND tenant_id IS NOT NULL)")
    op.create_check_constraint('ck_admin_users_auth_version','admin_users','auth_version >= 1')
    op.create_unique_constraint('uq_admin_users_tenant_id','admin_users',['tenant_id','id'])
    op.create_unique_constraint('uq_admin_users_id_scope','admin_users',['id','account_scope'])
    op.create_index('uq_admin_users_platform_username','admin_users',['username'],unique=True,postgresql_where=sa.text("account_scope='platform'"))
    op.create_index('uq_admin_users_tenant_username','admin_users',['tenant_id','username'],unique=True,postgresql_where=sa.text("account_scope='tenant'"))
    op.create_foreign_key('fk_tenant_members_account_tenant','tenant_members','admin_users',['tenant_id','user_id'],['tenant_id','id'],ondelete='CASCADE')

    op.add_column('admin_api_keys',sa.Column('account_scope',sa.String(16),nullable=True,server_default='tenant'))
    op.add_column('admin_api_keys',sa.Column('tenant_id',sa.String(64),nullable=True))
    bind.execute(sa.text('UPDATE admin_api_keys AS k SET account_scope=u.account_scope,tenant_id=u.tenant_id FROM admin_users AS u WHERE u.id=k.admin_user_id'))
    # Legacy owner/scopes/name do not establish an approved purpose or tenant
    # binding. Reissue every old active credential in its explicit namespace.
    # Keep already-revoked history and never copy credentials into audit data.
    bind.execute(sa.text("""WITH revoked AS (
        UPDATE admin_api_keys SET status='revoked',revoked_at=now(),revoked_by=NULL,
            revoke_reason='独立租户迁移：旧密钥用途与租户归属未经确认，请重新签发'
        WHERE status='active'
        RETURNING id,admin_user_id,account_scope,tenant_id
    ) INSERT INTO audit_logs(actor_type,actor_id,actor_scope,tenant_id,action,target_type,target_id,payload)
        SELECT 'system',NULL,'system',tenant_id,'api_key.identity_migration_revoked','api_key',id,
            jsonb_build_object('reason','legacy_api_key_purpose_unverified',
                'admin_user_id',admin_user_id,'account_scope',account_scope)
        FROM revoked"""))
    op.alter_column('admin_api_keys','account_scope',existing_type=sa.String(16),nullable=False)
    op.create_foreign_key('fk_admin_api_keys_tenant','admin_api_keys','tenants',['tenant_id'],['id'],ondelete='RESTRICT')
    op.create_check_constraint('ck_admin_api_keys_account_scope','admin_api_keys',"account_scope IN ('platform','tenant')")
    op.create_check_constraint('ck_admin_api_keys_account_tenant','admin_api_keys',"(account_scope='platform' AND tenant_id IS NULL) OR (account_scope='tenant' AND tenant_id IS NOT NULL)")
    op.create_foreign_key('fk_admin_api_keys_owner_scope','admin_api_keys','admin_users',['admin_user_id','account_scope'],['id','account_scope'])
    op.create_foreign_key('fk_admin_api_keys_owner_tenant','admin_api_keys','admin_users',['tenant_id','admin_user_id'],['tenant_id','id'])


def downgrade():
    raise RuntimeError('Independent tenant identities cannot be recombined safely; restore a compatible backup instead of discarding authorization and audit mappings')
