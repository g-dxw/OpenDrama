"""Fail-closed production schema upgrade, private evidence and first tenant credentials.

The release runner must stop the old API and verify a full PostgreSQL backup
before invoking this module in the NEW image. It never starts or rolls back an
old image. Passwords exist only in the private output file, never in logs/audit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import traceback

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, select, text
from sqlalchemy.orm import Session

from app.db import get_engine
from app.tenant_identity_snapshot import export_snapshot

BOOTSTRAP_ACTION = 'tenant.identity.credentials_initialized'
DEFAULTS_ACTION = 'tenant.content_defaults.initial_backfill'
CHANGING_TABLES = {'admin_users','tenant_roles','tenant_members','audit_logs','india_connections'}


def private_json(path, value):
    """Exclusive, durable output: retries never overwrite a credential/evidence file."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2,
                  default=lambda item: item.isoformat())
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def read_evidence(engine):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            metadata = MetaData()
            metadata.reflect(bind=connection)
            if 'admin_users' not in metadata.tables:
                return {'format':'jjtv-tenant-identity-snapshot-v1','model':'empty',
                        'revisions':[], 'table_counts':{}}, {}, {}
            snapshot = export_snapshot(connection)
            # These digests stay in process memory only. No hashes or secret
            # fingerprints are exported, including the legacy password hashes.
            users = connection.execute(text('SELECT id,username,password_hash,display_name,role,status,supplier_id FROM admin_users ORDER BY id')).mappings()
            accounts = {row['id']: digest(dict(row)) for row in users}
            audit = connection.execute(text('SELECT id,actor_type,actor_id,action,target_type,target_id,payload,created_at FROM audit_logs ORDER BY id')).mappings()
            history = {row['id']: digest(dict(row)) for row in audit}
            return snapshot, accounts, history


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).digest()


# These are the immutable additions made by revision a1114permissions, not the
# current runtime module registry. Existing ownership and disabled modules must
# never be rewritten merely because a capability was added to the application.
CAPABILITY_REVISION = 'a1114permissions'
CAPABILITY_KEYS = frozenset({'login_settings','stripe','coin_packs','coins','attribution','support'})


def _snapshot_rows(snapshot, field, keys, table=None):
    rows = snapshot.get(field)
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        raise RuntimeError('Incomplete capability preservation evidence; API remains stopped')
    try:
        indexed = {tuple(row[key] for key in keys):row for row in rows}
    except (KeyError,TypeError):
        raise RuntimeError('Invalid capability preservation evidence; API remains stopped') from None
    if len(indexed)!=len(rows) or (table is not None and snapshot['table_counts'].get(table)!=len(rows)):
        raise RuntimeError('Incomplete capability preservation evidence; API remains stopped')
    return indexed


def _verify_capability_append(old, new):
    tables = {'legacy_business_bindings','tenant_modules'}.intersection(old['table_counts'])
    if not tables:
        return set()  # These tables did not exist before the identity migration.
    tenants = _snapshot_rows(old,'tenants',('id',))
    current_tenants = _snapshot_rows(new,'tenants',('id',))
    if tenants!=current_tenants or tenants.get(('jjtv',),{}).get('kind')!='native':
        raise RuntimeError('Tenant identities changed during capability migration; API remains stopped')
    # Permit the known additions only when this upgrade actually crosses their
    # migration. A normal head-to-head deployment cannot silently add modules.
    revisions = (old.get('revisions'),new.get('revisions'))
    if any(not isinstance(value,list) or len(value)!=1 for value in revisions):
        raise RuntimeError('Cannot verify capability migration revisions; API remains stopped')
    from alembic.script import ScriptDirectory
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend/'alembic.ini'))
    config.set_main_option('script_location',str(backend/'alembic'))
    try:
        crossed = CAPABILITY_REVISION in {revision.revision for revision in
                    ScriptDirectory.from_config(config).iterate_revisions(revisions[1][0],revisions[0][0])}
    except Exception:
        raise RuntimeError('Cannot verify capability migration revisions; API remains stopped') from None
    for table,field,keys in (('legacy_business_bindings','legacy_bindings',('module_key',)),
                             ('tenant_modules','modules',('tenant_id','module_key'))):
        if table not in tables:
            continue
        previous = _snapshot_rows(old,field,keys,table)
        current = _snapshot_rows(new,field,keys,table)
        if any(current.get(key)!=row for key,row in previous.items()):
            raise RuntimeError('Existing '+table+' rows changed; API remains stopped')
        if table=='legacy_business_bindings':
            expected = {(key,) for key in CAPABILITY_KEYS} - previous.keys() if crossed else set()
        else:
            expected = {(tenant[0],key) for tenant in tenants for key in CAPABILITY_KEYS} - previous.keys() if crossed else set()
        if current.keys()-previous.keys()!=expected:
            raise RuntimeError('Unexpected '+table+' additions; API remains stopped')
        for key in expected:
            row = current[key]
            if table=='legacy_business_bindings':
                valid = (set(row)=={'module_key','tenant_id','created_at'}
                         and row['tenant_id']=='jjtv' and row['created_at'] is not None)
            else:
                valid = (set(row)=={'tenant_id','module_key','enabled'}
                         and row['enabled'] is (key[0]=='jjtv'))
            if not valid:
                raise RuntimeError('Invalid '+table+' capability ownership; API remains stopped')
    return tables


def verify_preserved(before, after):
    old, accounts, history = before
    new, new_accounts, new_history = after
    if any(new_accounts.get(key) != value for key,value in accounts.items()):
        raise RuntimeError('Original account identity, password or status changed; API remains stopped')
    if any(new_history.get(key) != value for key,value in history.items()):
        raise RuntimeError('Historical audit data changed; API remains stopped')
    capability_tables = _verify_capability_append(old,new)
    for table,count in old['table_counts'].items():
        if table not in CHANGING_TABLES and table not in capability_tables and new['table_counts'].get(table) != count:
            raise RuntimeError('Unexpected business row count change in '+table+'; API remains stopped')
    if new['table_counts'].get('admin_users',0) < len(accounts):
        raise RuntimeError('Original account count decreased; API remains stopped')


def provision(engine, backup_dir):
    from app.modules.common.models import AuditLog
    from app.modules.ops.models import AdminUser
    from app.modules.ops.service import hash_password
    from app.modules.tenancy.models import Tenant
    from app.modules.tenancy.content_defaults import seed_content_defaults
    credentials = []
    defaults = None
    with Session(engine) as db:
        mappings = db.execute(text("SELECT target_id,payload FROM audit_logs WHERE action='tenant.identity.migrated' ORDER BY id")).mappings().all()
        for mapping in mappings:
            target = mapping['target_id']
            source = mapping['payload'].get('source_admin_user_id')
            if source == target:  # Existing JJTV staff never have their password reset.
                continue
            initialized = db.scalar(select(AuditLog.id).where(AuditLog.action == BOOTSTRAP_ACTION,AuditLog.target_id == target).limit(1))
            if initialized:
                continue
            user = db.get(AdminUser,target)
            if (user is None or user.account_scope != 'tenant' or not user.must_change_password
                    or user.auth_version != 1):
                continue  # A real operator already changed this migrated account.
            temporary = secrets.token_urlsafe(24)
            user.password_hash = hash_password(temporary)
            user.auth_version += 1
            credentials.append(dict(tenant_code=db.get(Tenant,user.tenant_id).code,
                                    user_id=user.id,username=user.username,status=user.status,
                                    temporary_password=temporary,must_change_password=True))
            db.add(AuditLog(actor_type='system',actor_scope='system',tenant_id=user.tenant_id,
                            action=BOOTSTRAP_ACTION,target_type='admin_user',target_id=user.id,
                            payload={'source_admin_user_id':source,'migration_initialization':True}))
        jjtv = db.scalar(select(Tenant).where(Tenant.code == 'jjtv').with_for_update())
        seeded = db.scalar(select(AuditLog.id).where(AuditLog.action == DEFAULTS_ACTION,AuditLog.tenant_id == jjtv.id).limit(1)) if jjtv else None
        if jjtv is not None and not seeded:
            defaults = seed_content_defaults(db,jjtv,populate_content=True)
            db.add(AuditLog(actor_type='system',actor_scope='system',tenant_id=jjtv.id,
                            action=DEFAULTS_ACTION,target_type='tenant',target_id=None,
                            payload={'tenant_code':jjtv.code,'content_defaults':defaults,'populate_content':True}))
        # Persist recoverable credentials before committing the corresponding
        # hashes. A success receipt below distinguishes committed credentials.
        private_json(backup_dir/'tenant-credentials.json',
                     {'format':'jjtv-migration-credentials-v1','accounts':credentials})
        db.commit()
    return {entry['user_id'] for entry in credentials}, defaults


def run_upgrade(engine, backup_dir, upgrade):
    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir():
        raise RuntimeError('Private verified backup directory is required')
    os.chmod(backup_dir,0o700)
    before = read_evidence(engine)
    private_json(backup_dir/'identity-before.json',before[0])
    upgrade()
    migrated = read_evidence(engine)
    verify_preserved(before,migrated)
    # Every existing row, including content, is verified before additive seeds.
    private_json(backup_dir/'identity-after-migration.json',migrated[0])
    initialized,defaults = provision(engine,backup_dir)
    final = read_evidence(engine)
    # Provisioning changes only NEW migration-created accounts and adds an audit
    # entry/defaults. Original accounts and old audit events remain byte-equal.
    if any(final[1].get(key) != value for key,value in before[1].items() if key not in initialized):
        raise RuntimeError('Original account changed during provisioning')
    if any(final[2].get(key) != value for key,value in before[2].items()):
        raise RuntimeError('Original audit changed during provisioning')
    private_json(backup_dir/'identity-after.json',final[0])
    receipt = dict(format='jjtv-tenant-upgrade-receipt-v1',
                   completed_at=datetime.now(timezone.utc).isoformat(),
                   before_revisions=before[0]['revisions'],after_revisions=final[0]['revisions'],
                   original_accounts_preserved=len(set(before[1])-initialized),original_audit_rows_preserved=len(before[2]),
                   initialized_accounts=len(initialized),content_defaults=defaults,
                   credential_file='tenant-credentials.json',old_api_auto_restart=False)
    private_json(backup_dir/'upgrade-receipt.json',receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-dir',type=Path,required=True)
    args = parser.parse_args()
    config = Config('alembic.ini')
    try:
        receipt = run_upgrade(get_engine(),args.backup_dir,lambda: command.upgrade(config,'head'))
    except Exception as exc:
        # Full SQL errors may include bound secrets. Keep diagnostics in a
        # private0600 file only; never send them to the shared release log.
        try:
            private_json(args.backup_dir/'upgrade-error.json',
                         {'exception_type':type(exc).__name__,'traceback':traceback.format_exc()})
        except Exception:
            pass  # A broken output filesystem must not expose the original error.
        raise SystemExit('JJTV upgrade/provisioning failed; old API stays stopped. Inspect private migration evidence; never auto-revert the schema.') from None
    print(json.dumps({'upgrade_complete':True,'schema':receipt['after_revisions'],
                      'preserved_accounts':receipt['original_accounts_preserved'],
                      'initialized_accounts':receipt['initialized_accounts'],
                      'private_evidence_directory':str(args.backup_dir)},ensure_ascii=False))


if __name__ == '__main__':
    main()
