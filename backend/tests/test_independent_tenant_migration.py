"""Exercise the real shared-account -> independent-account migration in its own DB."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.modules.ops.service import hash_password,verify_password
from app.modules.ops.api_keys import generate_key

BACKEND=Path(__file__).resolve().parents[1]

def rid(tenant,key):return 'role-'+hashlib.sha256((tenant+':'+key).encode()).hexdigest()[:40]

def test_real_migration_preserves_actors_business_data_and_effective_grants(engine,monkeypatch):
    source=engine.url.database
    assert source and re.fullmatch(r'[a-z][a-z0-9_]{0,40}',source)
    name=source+'_identity_'+uuid4().hex[:8]
    assert len(name)<=63 and name.startswith(source+'_identity_')
    admin=sa.create_engine(engine.url.set(database='postgres'),isolation_level='AUTOCOMMIT')
    target_url=engine.url.set(database=name)
    created=False
    target=None
    try:
        with admin.connect() as db:
            db.execute(sa.text('CREATE DATABASE "'+name+'"'))
            created=True
        cfg=Config(str(BACKEND/'alembic.ini'))
        cfg.set_main_option('script_location',str(BACKEND/'alembic'))
        cfg.set_main_option('sqlalchemy.url',target_url.render_as_string(hide_password=False).replace('%','%%'))
        with monkeypatch.context() as local:
            local.setenv('ALEMBIC_DATABASE_URL',target_url.render_as_string(hide_password=False))
            command.upgrade(cfg,'b2e6c9148a70')
            target=sa.create_engine(target_url)
            admin_hash=hash_password('legacy-platform-pass')
            staff_hash=hash_password('legacy-staff-pass')
            with target.begin() as db:
                db.execute(sa.text("INSERT INTO tenants(id,name,kind,status) VALUES ('migration-other','Other migration project','native','active')"))
                custom_id='migration-custom-role'
                db.execute(sa.text("INSERT INTO tenant_roles(id,tenant_id,name,builtin_key,permissions,version) VALUES (:id,'migration-other','Metadata operator',NULL,CAST(:permissions AS jsonb),1)"),dict(id=custom_id,permissions=json.dumps(['dramas.view','dramas.edit'])))
                finance_id='migration-finance-role'
                db.execute(sa.text("INSERT INTO tenant_roles(id,tenant_id,name,builtin_key,permissions,version) VALUES (:id,'jjtv','Legacy narrowed finance',NULL,CAST(:permissions AS jsonb),1)"),dict(id=finance_id,permissions=json.dumps(['dramas.view','dramas.edit','users.view','users.manage','staff.view','staff.manage'])))
                def person(username,role,status='active',password=staff_hash):
                    return db.execute(sa.text("INSERT INTO admin_users(username,password_hash,display_name,role,status) VALUES (:username,:password,:username,:role,:status) RETURNING id"),dict(username=username,password=password,role=role,status=status)).scalar_one()
                platform_id=person('migration-root','admin',password=admin_hash)
                shared_id=person('migration-shared','ops')
                disabled_id=person('migration-disabled','ops','disabled')
                finance_user=person('migration-finance','finance')
                assignments=[('jjtv',platform_id,'admin',rid('jjtv','admin')),('jjtv',shared_id,'ops',rid('jjtv','ops')),('migration-other',shared_id,'ops',custom_id),('india-short-drama',shared_id,'finance',rid('india-short-drama','finance')),('migration-other',disabled_id,'ops',custom_id),('jjtv',finance_user,'finance',finance_id)]
                for tenant,user,legacy,role_id in assignments:
                    db.execute(sa.text('INSERT INTO tenant_members(tenant_id,user_id,role,role_id) VALUES (:tenant,:user,:legacy,:role)'),dict(tenant=tenant,user=user,legacy=legacy,role=role_id))
                old_keys=[]
                for uid in (platform_id,shared_id):
                    _,prefix,digest=generate_key()
                    key_id=db.execute(sa.text("INSERT INTO admin_api_keys(admin_user_id,name,key_prefix,key_hash,scopes,status,expires_at) VALUES (:user,'Migration key',:prefix,:digest,CAST(:scopes AS jsonb),'active',:expires) RETURNING id"),dict(user=uid,prefix=prefix,digest=digest,scopes=json.dumps(['read','write']),expires=datetime.now(timezone.utc)+timedelta(days=30))).scalar_one()
                    old_keys.append((key_id,uid))
                _,old_prefix,old_digest=generate_key()
                preserved_key=db.execute(sa.text("INSERT INTO admin_api_keys(admin_user_id,name,key_prefix,key_hash,scopes,status,expires_at,revoked_at,revoke_reason) VALUES (:user,'Previously revoked key',:prefix,:digest,CAST(:scopes AS jsonb),'revoked',:expires,:revoked,'Legacy manual removal') RETURNING id"),dict(user=shared_id,prefix=old_prefix,digest=old_digest,scopes=json.dumps(['read']),expires=datetime.now(timezone.utc)+timedelta(days=30),revoked=datetime.now(timezone.utc))).scalar_one()
                db.execute(sa.text("INSERT INTO india_connections(tenant_id,user_id,session_key,secret) VALUES ('india-short-drama',:user,:key,'old-opaque-cipher')"),dict(user=shared_id,key='a'*64))
                historical_id=db.execute(sa.text("INSERT INTO audit_logs(actor_type,actor_id,action,target_type,target_id,payload) VALUES ('admin',:actor,'old.content.operation','admin_user',:actor,CAST(:payload AS jsonb)) RETURNING id"),dict(actor=shared_id,payload=json.dumps({'tenant_id':'india-short-drama','old_marker':'preserve'}))).scalar_one()
            with target.begin() as db:
                # Reflect the actual b2e6 schema: current ORM projections also
                # contain later consumer ownership columns and must not be used
                # to write a historical database during this migration test.
                old_schema=sa.MetaData()
                users=sa.Table('users',old_schema,autoload_with=db)
                wallets=sa.Table('wallet_accounts',old_schema,autoload_with=db)
                assert 'tenant_id' not in users.c and 'tenant_id' not in wallets.c
                viewer_id=db.execute(users.insert().values(
                    email='migration-consumer@example.com',display_name='Preserved viewer',
                    avatar_url='',gender='unspecified',bio='',region='',locale='en',
                    following_count=0,follower_count=0,received_likes=0,reminder_opt_in=False,
                ).returning(users.c.id)).scalar_one()
                db.execute(wallets.insert().values(user_id=viewer_id,balance=123))
                # Seed the actual historical projection, before desktop columns exist.
                drama_id=db.execute(sa.text("INSERT INTO dramas(tenant_id,title,status,synopsis,cover_url,free_episode_count,coin_price_per_episode,country_allowlist,play_count,review_state) VALUES ('jjtv','Preserved migration content','draft','','',0,0,'',0,'none') RETURNING id")).scalar_one()
            command.upgrade(cfg,'head')
            with target.connect() as db:
                rows={r['id']:dict(r) for r in db.execute(sa.text('SELECT * FROM admin_users')).mappings()}
                assert rows[platform_id]['account_scope']=='platform' and rows[platform_id]['tenant_id'] is None
                assert rows[platform_id]['password_hash']==admin_hash
                assert rows[shared_id]['account_scope']=='tenant' and rows[shared_id]['tenant_id']=='jjtv'
                assert rows[shared_id]['password_hash']==staff_hash
                assert db.execute(sa.text('SELECT count(*) FROM tenant_members WHERE user_id=:id'),dict(id=platform_id)).scalar_one()==0
                clones=[r for r in rows.values() if r['username']=='migration-shared' and r['id']!=shared_id]
                assert {r['tenant_id'] for r in clones}=={'migration-other','india-short-drama'}
                assert len({r['id'] for r in clones})==2
                assert all(r['must_change_password'] and not verify_password('legacy-staff-pass',r['password_hash']) for r in clones)
                disabled=[r for r in rows.values() if r['username']=='migration-disabled' and r['tenant_id']=='migration-other']
                assert len(disabled)==1 and disabled[0]['status']=='disabled' and disabled[0]['must_change_password']
                for clone in clones:
                    keys=db.execute(sa.text('SELECT r.permissions FROM tenant_members m JOIN tenant_roles r ON r.id=m.role_id WHERE m.user_id=:id'),dict(id=clone['id'])).scalar_one()
                    assert set(keys)==({'dramas.view','dramas.edit'} if clone['tenant_id']=='migration-other' else {'dramas.view'})
                    mapped=db.execute(sa.text("SELECT payload FROM audit_logs WHERE action='tenant.identity.migrated' AND target_id=:id"),dict(id=clone['id'])).scalar_one()
                    assert mapped['source_admin_user_id']==shared_id and mapped['migrated_admin_user_id']==clone['id']
                narrowed=db.execute(sa.text('SELECT r.permissions FROM tenant_members m JOIN tenant_roles r ON r.id=m.role_id WHERE m.user_id=:id'),dict(id=finance_user)).scalar_one()
                assert set(narrowed)=={'dramas.view','dramas.edit','users.view'}
                assert db.execute(sa.text('SELECT count(*) FROM india_connections')).scalar_one()==0
                for key_id,uid in old_keys:
                    row=db.execute(sa.text('SELECT account_scope,tenant_id,admin_user_id,status,revoke_reason FROM admin_api_keys WHERE id=:id'),dict(id=key_id)).mappings().one()
                    assert row['admin_user_id']==uid
                    assert row['status']=='revoked' and row['revoke_reason']
                    revocation=db.execute(sa.text("SELECT actor_type,actor_scope FROM audit_logs WHERE action='api_key.identity_migration_revoked' AND target_id=:id"),dict(id=key_id)).mappings().one()
                    assert revocation['actor_type']=='system' and revocation['actor_scope']=='system'
                    assert (row['account_scope'],row['tenant_id'])==(('platform',None) if uid==platform_id else ('tenant','jjtv'))
                retained=db.execute(sa.text('SELECT status,revoke_reason FROM admin_api_keys WHERE id=:id'),dict(id=preserved_key)).mappings().one()
                assert dict(retained)=={'status':'revoked','revoke_reason':'Legacy manual removal'}
                assert db.execute(sa.text("SELECT count(*) FROM audit_logs WHERE action='api_key.identity_migration_revoked' AND target_id=:id"),dict(id=preserved_key)).scalar_one()==0
                for tenant in ('jjtv','india-short-drama','migration-other'):
                    count=db.execute(sa.text("SELECT count(*) FROM tenant_members m JOIN tenant_roles r ON r.id=m.role_id JOIN admin_users u ON u.id=m.user_id WHERE m.tenant_id=:tenant AND r.kind='tenant_admin' AND u.status='active' AND u.account_scope='tenant' AND u.tenant_id=:tenant"),dict(tenant=tenant)).scalar_one()
                    assert count>=1
                audit=db.execute(sa.text('SELECT actor_id,actor_scope,tenant_id,payload FROM audit_logs WHERE id=:id'),dict(id=historical_id)).mappings().one()
                assert audit['actor_id']==shared_id and audit['actor_scope']=='legacy' and audit['tenant_id']=='india-short-drama'
                assert audit['payload']['old_marker']=='preserve'
                assert db.execute(sa.text('SELECT balance FROM wallet_accounts WHERE user_id=:id'),dict(id=viewer_id)).scalar_one()==123
                assert db.execute(sa.text('SELECT email FROM users WHERE id=:id'),dict(id=viewer_id)).scalar_one()=='migration-consumer@example.com'
                assert db.execute(sa.text('SELECT tenant_id FROM users WHERE id=:id'),dict(id=viewer_id)).scalar_one()=='jjtv'
                assert db.execute(sa.text('SELECT tenant_id FROM dramas WHERE id=:id'),dict(id=drama_id)).scalar_one()=='jjtv'
                original_modules={'dashboard','users','rewards','comments','moderation','finance','settlements','plans','suppliers','home_ops'}
                bindings=set(db.execute(sa.text("SELECT module_key FROM legacy_business_bindings WHERE tenant_id='jjtv'")).scalars())
                assert original_modules <= bindings
                assert bindings-original_modules=={'login_settings','stripe','coin_packs','coins','attribution','support'}
                assert db.execute(sa.text("SELECT count(*) FROM legacy_business_bindings WHERE tenant_id<>'jjtv'")).scalar_one()==0
            # The newest irreversible ownership migration now refuses first.
            # Keep the original identity migration's refusal assertion too.
            with pytest.raises(RuntimeError,match='cannot be merged safely'):
                command.downgrade(cfg,'b2e6c9148a70')
            identity_migration=ScriptDirectory.from_config(cfg).get_revision('c4d8e0a1b295').module
            with pytest.raises(RuntimeError,match='cannot be recombined'):
                identity_migration.downgrade()
    finally:
        if target is not None:target.dispose()
        if created:
            with admin.connect() as db:
                row=db.execute(sa.text('SELECT pg_get_userbyid(datdba),current_user FROM pg_database WHERE datname=:name'),dict(name=name)).first()
                if row is not None:
                    assert row[0]==row[1] and name.startswith(source+'_identity_')
                    db.execute(sa.text('DROP DATABASE "'+name+'" WITH (FORCE)'))
        admin.dispose()
