"""Production's actual f9 -> independent tenant upgrade and recoverable bootstrap."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.deploy_tenant_upgrade import run_upgrade, verify_preserved, BOOTSTRAP_ACTION
from app.modules.ops.service import hash_password, verify_password

BACKEND = Path(__file__).resolve().parents[1]


def test_f9_upgrade_preserves_real_accounts_and_bootstraps_only_new_accounts(engine,monkeypatch,tmp_path):
    source = engine.url.database
    assert source and re.fullmatch(r'[a-z][a-z0-9_]{0,40}',source)
    name = source+'_upgrade_'+uuid4().hex[:8]
    assert len(name) <= 63 and name.startswith(source+'_upgrade_')
    admin = sa.create_engine(engine.url.set(database='postgres'),isolation_level='AUTOCOMMIT')
    target_url = engine.url.set(database=name)
    target = None
    created = False
    try:
        with admin.connect() as db:
            db.execute(sa.text('CREATE DATABASE "'+name+'"'))
            created = True
        config = Config(str(BACKEND/'alembic.ini'))
        config.set_main_option('script_location',str(BACKEND/'alembic'))
        config.set_main_option('sqlalchemy.url',target_url.render_as_string(hide_password=False).replace('%','%%'))
        with monkeypatch.context() as local:
            local.setenv('ALEMBIC_DATABASE_URL',target_url.render_as_string(hide_password=False))
            command.upgrade(config,'f9c2a71d8b30')
            target = sa.create_engine(target_url)
            original_password = 'legacy-password-stays-valid'
            original_hash = hash_password(original_password)
            with target.begin() as db:
                db.execute(sa.text("INSERT INTO tenants(id,name,kind,status) VALUES ('existing-project','Existing project','native','active')"))
                ids = []
                for username,role in [('old-admin','admin'),('bridge-admin','admin'),('third-admin','admin'),('shared-operator','ops')]:
                    uid = db.execute(sa.text("INSERT INTO admin_users(username,password_hash,display_name,role,status) VALUES (:name,:password,:name,:role,'active') RETURNING id"),dict(name=username,password=original_hash,role=role)).scalar_one()
                    ids.append(uid)
                    db.execute(sa.text("INSERT INTO tenant_members(tenant_id,user_id,role) VALUES ('jjtv',:uid,:role)"),dict(uid=uid,role=role))
                db.execute(sa.text("INSERT INTO tenant_members(tenant_id,user_id,role) VALUES ('india-short-drama',:uid,'ops'),('existing-project',:uid,'ops')"),dict(uid=ids[-1]))
                db.execute(sa.text("INSERT INTO india_connections(tenant_id,user_id,session_key,secret) VALUES ('india-short-drama',:uid,:session,'old-encrypted-connection')"),dict(uid=ids[-1],session='d'*64))
                history = db.execute(sa.text("INSERT INTO audit_logs(actor_type,actor_id,action,target_type,target_id,payload) VALUES ('admin',:uid,'existing.operation','admin_user',:uid,CAST(:payload AS jsonb)) RETURNING id"),dict(uid=ids[1],payload=json.dumps({'preserve':'yes'}))).scalar_one()
                drama = db.execute(sa.text("INSERT INTO dramas(title,status,tenant_id,synopsis,cover_url,free_episode_count,coin_price_per_episode,country_allowlist,play_count,review_state) VALUES ('Existing real content','draft','jjtv','','',0,0,'',0,'none') RETURNING id")).scalar_one()
            first = tmp_path/'first'
            first.mkdir()
            receipt = run_upgrade(target,first,lambda: command.upgrade(config,'head'))
            assert receipt['before_revisions'] == ['f9c2a71d8b30']
            assert receipt['after_revisions'] == [ScriptDirectory.from_config(config).get_current_head()]
            assert receipt['original_accounts_preserved'] == 4
            # Three tenant administrators plus two independent clones.
            assert receipt['initialized_accounts'] == 5
            before = json.loads((first/'identity-before.json').read_text(encoding='utf-8'))
            assert before['roles'] == []  # No b2 table existed before snapshot.
            assert len(before['effective_business_grants']) >= 3
            private = json.loads((first/'tenant-credentials.json').read_text(encoding='utf-8'))
            assert len(private['accounts']) == 5
            assert not set(ids) & {item['user_id'] for item in private['accounts']}
            with target.connect() as db:
                for uid in ids:
                    row = db.execute(sa.text('SELECT username,password_hash,role,account_scope,tenant_id FROM admin_users WHERE id=:id'),dict(id=uid)).mappings().one()
                    assert row['password_hash'] == original_hash
                    assert row['account_scope'] == ('tenant' if uid == ids[-1] else 'platform')
                    assert row['tenant_id'] == ('jjtv' if uid == ids[-1] else None)
                for item in private['accounts']:
                    row = db.execute(sa.text('SELECT password_hash,must_change_password,auth_version FROM admin_users WHERE id=:id'),dict(id=item['user_id'])).mappings().one()
                    assert verify_password(item['temporary_password'],row['password_hash'])
                    assert not verify_password(original_password,row['password_hash'])
                    assert row['must_change_password'] and row['auth_version'] == 2
                assert db.scalar(sa.text('SELECT count(*) FROM india_connections')) == 0
                assert db.scalar(sa.text('SELECT count(*) FROM tags')) == 50
                assert db.scalar(sa.text('SELECT count(*) FROM home_groups')) == 12
                assert db.scalar(sa.text('SELECT count(*) FROM channel_tabs')) == 6
                assert db.scalar(sa.text('SELECT count(*) FROM dramas')) == 1
                assert db.scalar(sa.text('SELECT id FROM dramas')) == drama
                assert db.scalar(sa.text('SELECT actor_id FROM audit_logs WHERE id=:id'),dict(id=history)) == ids[1]
                assert db.scalar(sa.text('SELECT count(*) FROM audit_logs WHERE action=:action'),dict(action=BOOTSTRAP_ACTION)) == 5
            for name in ('identity-before.json','identity-after-migration.json','identity-after.json','upgrade-receipt.json'):
                content = (first/name).read_text(encoding='utf-8')
                assert 'password_hash' not in content and original_hash not in content
                assert 'old-encrypted-connection' not in content
                assert all(item['temporary_password'] not in content for item in private['accounts'])
                if os.name == 'posix':
                    assert (first/name).stat().st_mode & 0o777 == 0o600
            if os.name == 'posix':
                assert (first/'tenant-credentials.json').stat().st_mode & 0o777 == 0o600
                assert first.stat().st_mode & 0o777 == 0o700
            # A normal/retried deployment does not rotate passwords or restore
            # intentionally removed defaults once the one-time backfill is done.
            second = tmp_path/'second'
            second.mkdir()
            retry = run_upgrade(target,second,lambda: command.upgrade(config,'head'))
            assert retry['initialized_accounts'] == 0 and retry['content_defaults'] is None
            assert json.loads((second/'tenant-credentials.json').read_text())['accounts'] == []
    finally:
        if target is not None:
            target.dispose()
        if created:
            with admin.connect() as db:
                owner = db.execute(sa.text('SELECT pg_get_userbyid(datdba),current_user FROM pg_database WHERE datname=:name'),dict(name=name)).first()
                if owner is not None:
                    assert owner[0] == owner[1] and name.startswith(source+'_upgrade_')
                    db.execute(sa.text('DROP DATABASE "'+name+'" WITH (FORCE)'))
        admin.dispose()


@pytest.mark.parametrize('change', ['password','audit','business_count'])
def test_upgrade_verification_refuses_data_loss(change):
    before = ({'table_counts':{'admin_users':1,'users':2}}, {1:b'original-account'}, {4:b'original-audit'})
    after = ({'table_counts':{'admin_users':3,'users':2}}, {1:b'original-account'}, {4:b'original-audit'})
    if change == 'password':
        after[1][1] = b'changed-account'
    elif change == 'audit':
        after[2].clear()
    else:
        after[0]['table_counts']['users'] = 1
    with pytest.raises(RuntimeError,match='remains stopped'):
        verify_preserved(before,after)
