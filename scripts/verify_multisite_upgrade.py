#!/usr/bin/env python3
"""Local-only legacy upgrade and pg_dump/pg_restore preservation probe.

Uses only two explicitly reserved probe databases. No current application ORM
is used against the old schema. DATABASE_URL must name a local PostgreSQL host.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from alembic import command
from alembic.config import Config
import sqlalchemy as sa
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
LEGACY_REVISION = 'a1114cancel_intent'
PROBE_DATABASE = 'minitv_multisite_upgrade_probe'
RESTORE_DATABASE = 'minitv_multisite_upgrade_probe_restored'
SAMPLE_TABLES = ('users', 'wallet_accounts', 'coin_ledger', 'plans', 'plan_versions',
                 'subscriptions', 'orders', 'dramas', 'episodes', 'entitlements')


def connect_url(base, database):
    return base.set(database=database)


def migrate(url, revision):
    cfg = Config(str(ROOT / 'backend' / 'alembic.ini'))
    cfg.set_main_option('script_location', str(ROOT / 'backend' / 'alembic'))
    raw = url.render_as_string(hide_password=False)
    cfg.set_main_option('sqlalchemy.url', raw.replace('%', '%%'))
    previous = os.environ.get('ALEMBIC_DATABASE_URL')
    os.environ['ALEMBIC_DATABASE_URL'] = raw
    try:
        command.upgrade(cfg, revision)
    finally:
        if previous is None:
            os.environ.pop('ALEMBIC_DATABASE_URL', None)
        else:
            os.environ['ALEMBIC_DATABASE_URL'] = previous


def sample_data(engine):
    statements = [
        """INSERT INTO users(id,email,display_name,avatar_url,gender,bio,region,locale,following_count,follower_count,received_likes,reminder_opt_in)
           VALUES (4101,'upgrade-probe@example.test','Upgrade Probe','','unspecified','','US','en',0,0,0,false)""",
        """INSERT INTO plans(id,code,name_en,name_zh,status)
           VALUES (4201,'upgrade-probe-pass','Probe Pass','探针套餐','active')""",
        """INSERT INTO plan_versions(id,plan_id,version,price_cents,currency,benefits,note)
           VALUES (4301,4201,1,1999,'USD',jsonb_build_object('days',30),'legacy immutable sample')""",
        """INSERT INTO dramas(id,title,synopsis,cover_url,free_episode_count,coin_price_per_episode,status,country_allowlist,tenant_id,play_count)
           VALUES (4401,'Legacy upgrade probe','Existing private JJTV content','',0,23,'published','','jjtv',19)""",
        """INSERT INTO episodes(id,drama_id,episode_number,title,duration_seconds,bunny_video_id,status,subtitle_url,width,height,size_bytes,mime)
           VALUES (4501,4401,1,'Legacy episode',75,'upgrade-probe-existing-video','published','',720,1280,1024,'video/mp4')""",
        """INSERT INTO wallet_accounts(user_id,balance) VALUES (4101,127)""",
        """INSERT INTO coin_ledger(id,user_id,amount,balance_after,biz_type,biz_key,episode_id,note)
           VALUES (4601,4101,150,150,'adjustment','upgrade-probe-credit',NULL,'historical credit'),
                  (4602,4101,-23,127,'unlock','upgrade-probe-unlock',4501,'existing purchase')""",
        """INSERT INTO entitlements(id,user_id,episode_id,entitlement_type)
           VALUES (4701,4101,4501,'coin')""",
        """INSERT INTO subscriptions(id,user_id,plan_id,plan_version_id,status,current_period_start,current_period_end)
           VALUES (4801,4101,4201,4301,'active',TIMESTAMPTZ '2026-11-01 00:00:00+00',TIMESTAMPTZ '2026-12-01 00:00:00+00')""",
        """INSERT INTO orders(id,order_no,user_id,plan_version_id,amount_cents,currency,status,external_txn_id,paid_at,source_drama_id)
           VALUES (4901,'UPGRADE-PROBE-PAID',4101,4301,1999,'USD','paid','probe-txn-existing',TIMESTAMPTZ '2026-11-01 00:00:00+00',4401)""",
    ]
    with engine.begin() as db:
        for sql in statements:
            db.execute(sa.text(sql))
        for table in SAMPLE_TABLES:
            if table == 'wallet_accounts':
                continue
            db.execute(sa.text(f"SELECT setval(pg_get_serial_sequence('{table}','id'),(SELECT max(id) FROM {table}),true)"))


def capture(engine, *, column_map=None, table_names=None):
    inspector = sa.inspect(engine)
    column_map = column_map or {name: [col['name'] for col in inspector.get_columns(name)] for name in SAMPLE_TABLES}
    table_names = table_names or [name for name in inspector.get_table_names() if name != 'alembic_version']
    samples, counts, sequences = {}, {}, {}
    with engine.connect() as db:
        for table, columns in column_map.items():
            pk = inspector.get_pk_constraint(table)['constrained_columns']
            sql = 'SELECT ' + ','.join('"' + col + '"' for col in columns) + ' FROM "' + table + '" ORDER BY ' + ','.join('"' + col + '"' for col in pk)
            samples[table] = [dict(row) for row in db.execute(sa.text(sql)).mappings()]
            if 'id' in columns:
                name = db.scalar(sa.text(f"SELECT pg_get_serial_sequence('{table}','id')"))
                if name:
                    sequences[table] = list(db.execute(sa.text('SELECT last_value,is_called FROM ' + name)).one())
        for table in table_names:
            counts[table] = db.scalar(sa.text('SELECT count(*) FROM "' + table + '"'))
    return dict(samples=samples, counts=counts, sequences=sequences), column_map, table_names


def digest(value):
    raw = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def run_probe(*, database_url, output_dir):
    base = make_url(database_url)
    if base.host not in {'127.0.0.1', 'localhost', '::1'} or not base.drivername.startswith('postgresql'):
        raise RuntimeError('This verification is restricted to an explicitly configured local PostgreSQL instance')
    if base.database not in {'postgres', PROBE_DATABASE}:
        raise RuntimeError('Use the local postgres administrative database; no application database is accepted')
    for binary in ('pg_dump', 'pg_restore'):
        if shutil.which(binary) is None:
            raise RuntimeError(binary + ' must be installed for the restore verification')
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    old_dump, upgraded_dump = output_dir / 'legacy-before-upgrade.dump', output_dir / 'upgraded-after-migration.dump'
    admin = sa.create_engine(connect_url(base, 'postgres'), isolation_level='AUTOCOMMIT')
    engines = []
    with admin.connect() as db:
        for name in (PROBE_DATABASE, RESTORE_DATABASE):
            # These are fixed literal probe names; no caller-supplied database is
            # ever dropped, and a production/application URL is rejected above.
            db.execute(sa.text('DROP DATABASE IF EXISTS "' + name + '" WITH (FORCE)'))
            db.execute(sa.text('CREATE DATABASE "' + name + '"'))
    probe_url, restore_url = connect_url(base, PROBE_DATABASE), connect_url(base, RESTORE_DATABASE)
    migrate(probe_url, LEGACY_REVISION)
    probe = sa.create_engine(probe_url)
    engines.append(probe)
    sample_data(probe)
    before, columns, tables = capture(probe)
    pg_environment = dict(os.environ, PGPASSWORD=base.password or '')
    connection_args = ['--host', base.host, '--port', str(base.port or 5432), '--username', base.username or 'postgres']

    def dump(database, path):
        subprocess.run(['pg_dump', *connection_args, '--dbname', database, '--format=custom', '--no-owner', '--no-acl', '--file', str(path)],
                       env=pg_environment, check=True, capture_output=True, text=True)

    def restore(path):
        subprocess.run(['pg_restore', *connection_args, '--dbname', RESTORE_DATABASE, '--no-owner', '--no-acl', '--exit-on-error', str(path)],
                       env=pg_environment, check=True, capture_output=True, text=True)

    dump(PROBE_DATABASE, old_dump)
    migrate(probe_url, 'head')
    after, _, _ = capture(probe, column_map=columns, table_names=tables)
    assert before == after, 'Historical values, IDs, table counts or sequences changed during migration'
    with probe.connect() as db:
        assert db.scalar(sa.text('SELECT count(*) FROM users WHERE tenant_id <> \'jjtv\'')) == 0
        assert db.scalar(sa.text('SELECT count(*) FROM episodes WHERE tenant_id <> \'jjtv\'')) == 0
        assert db.scalar(sa.text('SELECT count(*) FROM dramas WHERE library_title_id IS NOT NULL')) == 0
        assert db.scalar(sa.text('SELECT count(*) FROM library_titles')) == 0
        upgraded_revision = db.scalar(sa.text('SELECT version_num FROM alembic_version'))

    # Restore the actual pre-upgrade backup into a second database and compare
    # its old-schema data before upgrading the restored copy independently.
    restore(old_dump)
    restored = sa.create_engine(restore_url)
    engines.append(restored)
    restored_before, _, _ = capture(restored, column_map=columns, table_names=tables)
    assert restored_before == before, 'pg_restore did not recreate the old historical data exactly'
    migrate(restore_url, 'head')
    restored_after, _, _ = capture(restored, column_map=columns, table_names=tables)
    assert restored_after == after, 'Restored backup upgrade differs from the first upgrade'

    # Also prove that the resulting multi-site schema is itself restorable.
    dump(PROBE_DATABASE, upgraded_dump)
    head_inspector = sa.inspect(probe)
    head_tables = [name for name in head_inspector.get_table_names() if name != 'alembic_version']
    head_columns = {name: [column['name'] for column in head_inspector.get_columns(name)] for name in head_tables}
    full_head, _, _ = capture(probe, column_map=head_columns, table_names=head_tables)
    for engine in engines:
        engine.dispose()
    with admin.connect() as db:
        db.execute(sa.text('DROP DATABASE "' + RESTORE_DATABASE + '" WITH (FORCE)'))
        db.execute(sa.text('CREATE DATABASE "' + RESTORE_DATABASE + '"'))
    restore(upgraded_dump)
    restored = sa.create_engine(restore_url)
    restored_head, _, _ = capture(restored, column_map=columns, table_names=tables)
    assert restored_head == after, 'Upgraded database dump/restore differs from its source'
    full_restored, _, _ = capture(restored, column_map=head_columns, table_names=head_tables)
    assert full_restored == full_head, 'Full upgraded table rows, counts or sequences differ after pg_restore'
    report = dict(checked_at=datetime.now(timezone.utc).isoformat(), legacy_revision=LEGACY_REVISION,
                  upgraded_revision=upgraded_revision, historical_table_count=len(tables), sample_tables=list(SAMPLE_TABLES),
                  preservation_sha256=digest(before), upgraded_sha256=digest(after), restored_sha256=digest(restored_head),
                  old_backup_sha256=hashlib.sha256(old_dump.read_bytes()).hexdigest(),
                  upgraded_backup_sha256=hashlib.sha256(upgraded_dump.read_bytes()).hexdigest(),
                  upgraded_full_table_count=len(head_tables), upgraded_full_sha256=digest(full_head), restored_full_sha256=digest(full_restored),
                  wallet_balance=127, user_id=4101, drama_id=4401, episode_id=4501, entitlement_id=4701, order_id=4901,
                  checks=dict(legacy_values_preserved=True, old_backup_restore_exact=True,
                              restored_backup_upgrades_exactly=True, upgraded_backup_restore_exact=True,
                              historical_content_stays_private=True, new_ownership_is_jjtv=True))
    (output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    restored.dispose()
    admin.dispose()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'outputs' / 'multisite-upgrade-probe')
    args = parser.parse_args()
    url = os.environ.get('DATABASE_URL', '')
    if not url:
        raise SystemExit('Set DATABASE_URL to the local PostgreSQL administrative database')
    print(json.dumps(run_probe(database_url=url, output_dir=args.output_dir), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
