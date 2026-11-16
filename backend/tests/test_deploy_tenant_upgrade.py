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

from app.deploy_tenant_upgrade import run_upgrade, verify_preserved, BOOTSTRAP_ACTION, SITE_URLS_ACTION
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
            local.setenv('SITE_URL','https://jjtv.8jj.net')
            local.setenv('H5_PUBLIC_BASE_URL','https://jjtv-h5.8jj.net')
            local.setenv('SITE_LEGACY_TENANT','jjtv')
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
            assert receipt['site_urls']['changed_fields'] == ['web_url','h5_url']
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


@pytest.fixture
def legacy_site_upgrade(engine,monkeypatch):
    """An isolated, real production-predecessor schema; no Redis is used."""
    source = engine.url.database
    assert source and re.fullmatch(r'[a-z][a-z0-9_]{0,40}',source)
    name = source+'_urls_'+uuid4().hex[:8]
    assert len(name)<=63 and name.startswith(source+'_urls_')
    admin = sa.create_engine(engine.url.set(database='postgres'),isolation_level='AUTOCOMMIT')
    target = None
    created = False
    try:
        with admin.connect() as db:
            db.execute(sa.text('CREATE DATABASE "'+name+'"'))
            created = True
        url = engine.url.set(database=name)
        config = Config(str(BACKEND/'alembic.ini'))
        config.set_main_option('script_location',str(BACKEND/'alembic'))
        config.set_main_option('sqlalchemy.url',url.render_as_string(hide_password=False).replace('%','%%'))
        monkeypatch.setenv('ALEMBIC_DATABASE_URL',url.render_as_string(hide_password=False))
        monkeypatch.setenv('SITE_URL','https://jjtv.8jj.net/')
        monkeypatch.setenv('H5_PUBLIC_BASE_URL','https://jjtv-h5.8jj.net/')
        monkeypatch.setenv('SITE_LEGACY_HOSTS','jjtv.8jj.net,jjtv-h5.8jj.net,jjtv-api.8jj.net')
        monkeypatch.setenv('SITE_LEGACY_TENANT','jjtv')
        monkeypatch.setenv('SITE_API_HOSTS','jjtv-api.8jj.net')
        command.upgrade(config,'a1114cancel_intent')
        target = sa.create_engine(url)
        yield target,config
    finally:
        if target is not None:
            target.dispose()
        if created:
            with admin.connect() as db:
                owner = db.execute(sa.text('SELECT pg_get_userbyid(datdba),current_user FROM pg_database WHERE datname=:name'),dict(name=name)).first()
                if owner is not None:
                    assert owner[0]==owner[1] and name.startswith(source+'_urls_')
                    db.execute(sa.text('DROP DATABASE "'+name+'" WITH (FORCE)'))
        admin.dispose()


def _backup(tmp_path,name='first'):
    directory = tmp_path/name
    directory.mkdir()
    return directory


def _urls(target):
    with target.connect() as db:
        return dict(db.execute(sa.text("SELECT web_url,h5_url,version FROM site_settings WHERE tenant_id='jjtv'")).mappings().one())


def _site_url_audits(target):
    with target.connect() as db:
        return db.execute(sa.text('SELECT payload FROM audit_logs WHERE action=:action ORDER BY id'),dict(action=SITE_URLS_ACTION)).scalars().all()


def _domain(target,host,*,owner='jjtv',status='active',verified=True):
    with target.begin() as db:
        db.execute(sa.text("""INSERT INTO tenant_domains(id,tenant_id,hostname,kind,status,verified_at)
            VALUES (:id,:owner,:host,'site',:status,CASE WHEN :verified THEN now() ELSE NULL END)"""),
            dict(id='domain-'+uuid4().hex,owner=owner,host=host,status=status,verified=verified))


def test_first_site_upgrade_backfills_only_verified_legacy_runtime_urls(legacy_site_upgrade,tmp_path):
    target,config = legacy_site_upgrade
    backup = _backup(tmp_path)
    receipt = run_upgrade(target,backup,lambda:command.upgrade(config,'head'))
    assert _urls(target)==dict(web_url='https://jjtv.8jj.net',h5_url='https://jjtv-h5.8jj.net',version=2)
    assert receipt['site_urls']==dict(tenant_id='jjtv',changed_fields=['web_url','h5_url'],
        web_url='https://jjtv.8jj.net',h5_url='https://jjtv-h5.8jj.net')
    assert _site_url_audits(target)==[dict(tenant_code='jjtv',fields=['web_url','h5_url'],source='verified_legacy_runtime')]
    stored = json.loads((backup/'upgrade-receipt.json').read_text())
    assert stored['site_urls']==receipt['site_urls']
    if os.name=='posix':
        assert (backup/'upgrade-receipt.json').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('preserve_h5',[False,True])
def test_first_site_upgrade_preserves_existing_migrated_values(legacy_site_upgrade,tmp_path,preserve_h5):
    target,config = legacy_site_upgrade
    def migrate():
        command.upgrade(config,'head')
        with target.begin() as db:
            db.execute(sa.text("UPDATE site_settings SET web_url='https://jjtv.8jj.net/existing',h5_url=:h5,version=17 WHERE tenant_id='jjtv'"),
                       dict(h5='https://jjtv-h5.8jj.net/existing' if preserve_h5 else ''))
    receipt = run_upgrade(target,_backup(tmp_path),migrate)
    assert _urls(target)==dict(web_url='https://jjtv.8jj.net/existing',
        h5_url='https://jjtv-h5.8jj.net/existing' if preserve_h5 else 'https://jjtv-h5.8jj.net',
        version=17 if preserve_h5 else 18)
    assert receipt['site_urls']['changed_fields']==([] if preserve_h5 else ['h5_url'])
    assert len(_site_url_audits(target))==(0 if preserve_h5 else 1)


def test_first_site_upgrade_accepts_active_verified_jjtv_domains_without_legacy_list(legacy_site_upgrade,monkeypatch,tmp_path):
    target,config = legacy_site_upgrade
    for host in ('web.jjtv.example.test','h5.jjtv.example.test'):
        _domain(target,host)
    monkeypatch.delenv('SITE_LEGACY_HOSTS')
    monkeypatch.setenv('SITE_URL','https://web.jjtv.example.test/site/')
    monkeypatch.setenv('H5_PUBLIC_BASE_URL','https://h5.jjtv.example.test/mobile/')
    run_upgrade(target,_backup(tmp_path),lambda:command.upgrade(config,'head'))
    assert _urls(target)['web_url']=='https://web.jjtv.example.test/site'
    assert _urls(target)['h5_url']=='https://h5.jjtv.example.test/mobile'


@pytest.mark.parametrize('variable,value',[
    ('SITE_URL',''),('H5_PUBLIC_BASE_URL',' '),('SITE_URL','http://jjtv.8jj.net'),
    ('SITE_URL','https://foreign.example.test'),('SITE_URL','https://jjtv-api.8jj.net'),
    ('SITE_URL','https://user:private-password@jjtv.8jj.net'),
    ('SITE_URL','https://jjtv.8jj.net?return=elsewhere'),('SITE_URL','https://jjtv.8jj.net#fragment'),
    ('SITE_URL','https://jjtv.8jj.net:444'),('SITE_URL','https://jjtv.8jj.net\\@foreign.example.test'),
])
def test_first_site_upgrade_refuses_missing_or_unsafe_urls_before_schema_change(legacy_site_upgrade,monkeypatch,tmp_path,variable,value):
    target,config = legacy_site_upgrade
    monkeypatch.setenv(variable,value)
    with pytest.raises(RuntimeError,match=variable+'.*remains stopped') as failure:
        run_upgrade(target,_backup(tmp_path),lambda:command.upgrade(config,'head'))
    assert 'private-password' not in str(failure.value)
    with target.connect() as db:
        assert db.scalar(sa.text('SELECT version_num FROM alembic_version'))=='a1114cancel_intent'
        assert db.scalar(sa.text("SELECT to_regclass('public.site_settings')")) is None


@pytest.mark.parametrize('invalid_configuration',['missing_allowlist','foreign_legacy_owner','foreign_binding','pending_binding'])
def test_first_site_upgrade_does_not_trust_legacy_defaults_or_conflicting_bindings(legacy_site_upgrade,monkeypatch,tmp_path,invalid_configuration):
    target,config = legacy_site_upgrade
    if invalid_configuration=='missing_allowlist':
        monkeypatch.delenv('SITE_LEGACY_HOSTS')
    elif invalid_configuration=='foreign_legacy_owner':
        monkeypatch.setenv('SITE_LEGACY_TENANT','india-short-drama')
    elif invalid_configuration=='foreign_binding':
        _domain(target,'jjtv.8jj.net',owner='india-short-drama')
    else:
        _domain(target,'jjtv.8jj.net',status='pending',verified=False)
    with pytest.raises(RuntimeError,match='SITE_URL.*remains stopped'):
        run_upgrade(target,_backup(tmp_path),lambda:command.upgrade(config,'head'))
    with target.connect() as db:
        assert db.scalar(sa.text('SELECT version_num FROM alembic_version'))=='a1114cancel_intent'


def test_same_schema_release_keeps_administrator_urls_and_ignores_runtime_defaults(legacy_site_upgrade,monkeypatch,tmp_path):
    target,config = legacy_site_upgrade
    run_upgrade(target,_backup(tmp_path),lambda:command.upgrade(config,'head'))
    with target.begin() as db:
        db.execute(sa.text("UPDATE site_settings SET web_url='https://jjtv.8jj.net/custom',h5_url='https://jjtv-h5.8jj.net/custom',version=29 WHERE tenant_id='jjtv'"))
    monkeypatch.delenv('SITE_URL')
    monkeypatch.delenv('H5_PUBLIC_BASE_URL')
    previous = _urls(target)
    audits = _site_url_audits(target)
    receipt = run_upgrade(target,_backup(tmp_path,'again'),lambda:command.upgrade(config,'head'))
    assert receipt['site_urls'] is None
    assert _urls(target)==previous
    assert _site_url_audits(target)==audits


@pytest.mark.parametrize('removed',['urls','row'])
def test_same_schema_release_refuses_missing_settings_without_restoring_them(legacy_site_upgrade,tmp_path,removed):
    target,config = legacy_site_upgrade
    run_upgrade(target,_backup(tmp_path),lambda:command.upgrade(config,'head'))
    with target.begin() as db:
        db.execute(sa.text("DELETE FROM site_settings WHERE tenant_id='jjtv'" if removed=='row' else
                           "UPDATE site_settings SET web_url='',h5_url='',version=29 WHERE tenant_id='jjtv'"))
    called = []
    with pytest.raises(RuntimeError,match='remains stopped'):
        run_upgrade(target,_backup(tmp_path,'again'),lambda:called.append(True))
    assert called==[]
    with target.connect() as db:
        if removed=='row':
            assert db.scalar(sa.text("SELECT count(*) FROM site_settings WHERE tenant_id='jjtv'"))==0
        else:
            assert _urls(target)==dict(web_url='',h5_url='',version=29)
    assert len(_site_url_audits(target))==1
