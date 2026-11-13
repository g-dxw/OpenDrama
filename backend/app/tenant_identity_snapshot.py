"""Read-only account/authorization evidence before or after tenant migration.

No password hashes, tokens, API-key hashes or India connection secrets are
exported. The output is still an account directory: keep it with the DB backup.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from sqlalchemy import MetaData, select, func, text
from sqlalchemy.orm import Session
from app.db import get_engine


def export_snapshot(connection):
    metadata = MetaData()
    metadata.reflect(bind=connection)
    tables = metadata.tables
    def rows(name, exclude=()):
        table = tables[name]
        fields = [c for c in table.c if c.name not in exclude]
        order = [table.c.id] if 'id' in table.c else list(table.primary_key.columns)
        return [dict(r) for r in connection.execute(select(*fields).order_by(*order)).mappings()]
    users = rows('admin_users', {'password_hash'})
    tenants = rows('tenants')
    roles = rows('tenant_roles')
    memberships = rows('tenant_members')
    independent = 'account_scope' in tables['admin_users'].c
    grants = []
    if independent:
        from app.modules.ops.models import AdminUser
        from app.modules.tenancy.models import Tenant, TenantMember
        from app.modules.tenancy.identity import SessionPrincipal
        from app.modules.tenancy.permissions import effective_grants
        with Session(bind=connection) as db:
            for user in db.scalars(select(AdminUser).order_by(AdminUser.id)):
                if user.account_scope != 'tenant' or user.status != 'active':
                    continue
                tenant = db.get(Tenant, user.tenant_id)
                if tenant is None or tenant.status != 'active':
                    continue
                principal = SessionPrincipal(user,'tenant',user.tenant_id,'normal',user.auth_version)
                member = db.get(TenantMember,(tenant.id,user.id))
                keys = effective_grants(db,user,tenant,principal=principal)['permissions'] if member is not None and user.role!='partner' else []
                grants.append(dict(user_id=user.id,tenant_id=tenant.id,permissions=sorted(keys)))
    else:
        # Freeze the exact migration implementation rather than reinterpreting
        # a role name while exporting the pre-migration effective permissions.
        path = Path(__file__).resolve().parents[1] / 'alembic/versions/c4d8e0a1b295_independent_business_tenants.py'
        spec = importlib.util.spec_from_file_location('tenant_migration_snapshot',path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        by_user = {u['id']:u for u in users}
        by_tenant = {t['id']:t for t in tenants}
        by_role = {r['id']:r for r in roles}
        for member in memberships:
            user = by_user[member['user_id']]
            if user['role']=='admin':
                continue
            permissions = migration.snapshot(by_tenant[member['tenant_id']],user,by_role[member['role_id']])
            grants.append(dict(user_id=user['id'],tenant_id=member['tenant_id'],permissions=sorted(permissions) if user['status']=='active' and by_tenant[member['tenant_id']]['status']=='active' else []))
        for user in users:
            if user['role']=='admin':
                for tenant in tenants:
                    permissions = migration.snapshot(tenant,user,{'permissions':[]})
                    grants.append(dict(user_id=user['id'],tenant_id=tenant['id'],permissions=sorted(permissions) if user['status']=='active' and tenant['status']=='active' else [],legacy_platform_access=True))
    key_fields = {'id','admin_user_id','name','scopes','status','expires_at','revoked_at','revoke_reason','created_at','created_by','account_scope','tenant_id'}
    keys = []
    if 'admin_api_keys' in tables:
        table = tables['admin_api_keys']
        keys = [dict(r) for r in connection.execute(select(*(c for c in table.c if c.name in key_fields)).order_by(table.c.id)).mappings()]
    counts = {name:connection.scalar(select(func.count()).select_from(table)) for name,table in sorted(tables.items()) if name != 'alembic_version'}
    return dict(format='jjtv-tenant-identity-snapshot-v1',exported_at=datetime.now(timezone.utc).isoformat(),
                model='independent' if independent else 'legacy',
                revisions=list(connection.execute(text('SELECT version_num FROM alembic_version')).scalars()),
                users=users,tenants=tenants,roles=roles,memberships=memberships,
                effective_business_grants=grants,api_keys=keys,
                modules=rows('tenant_modules') if 'tenant_modules' in tables else [],
                legacy_bindings=rows('legacy_business_bindings') if 'legacy_business_bindings' in tables else [],
                domains=rows('tenant_domains') if 'tenant_domains' in tables else [],table_counts=counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='New private JSON file; existing files are never overwritten')
    args = parser.parse_args()
    with get_engine().connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            snapshot = export_snapshot(connection)
    with args.output.open('x',encoding='utf-8') as target:
        json.dump(snapshot,target,ensure_ascii=False,indent=2,default=lambda value:value.isoformat())
        target.write('\n')
    print('Exported read-only identity snapshot: '+str(args.output))

if __name__ == '__main__':
    main()
