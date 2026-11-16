"""Exercise the deployment guard on the exact existing e6 -> head path."""
from pathlib import Path
from uuid import uuid4
import json
import re

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import sqlalchemy as sa

from app.deploy_tenant_upgrade import run_upgrade
from app.modules.ops.service import hash_password


BACKEND = Path(__file__).resolve().parents[1]
CAPABILITIES = {'login_settings','stripe','coin_packs','coins','attribution','support'}


def test_existing_e6_deployment_allows_only_capability_appends_and_retries(engine,monkeypatch,tmp_path):
    source = engine.url.database
    assert source and re.fullmatch(r'[a-z][a-z0-9_]{0,40}',source)
    name = source+'_capguard_'+uuid4().hex[:8]
    assert len(name)<=63 and name.startswith(source+'_capguard_')
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
            local.setenv('SITE_LEGACY_HOSTS','jjtv.8jj.net,jjtv-h5.8jj.net,jjtv-api.8jj.net')
            local.setenv('SITE_LEGACY_TENANT','jjtv')
            local.setenv('SITE_API_HOSTS','jjtv-api.8jj.net')
            command.upgrade(config,'e6a1c3d9b742')
            target = sa.create_engine(target_url)
            password_hash = hash_password('isolated-test-password')
            with target.begin() as db:
                # This existing e6 installation already completed its original
                # identity bootstrap, as production had before this release.
                db.execute(sa.text("UPDATE admin_users SET auth_version=2,must_change_password=false"))
                db.execute(sa.text("INSERT INTO tenants(id,code,name,kind,status) VALUES ('guard-other','guard-other','Existing other tenant','native','active')"))
                account = db.execute(sa.text("INSERT INTO admin_users(username,password_hash,display_name,role,status,account_scope,auth_version,must_change_password) VALUES ('guard-original',:password,'Original operator','admin','active','platform',1,false) RETURNING id"),dict(password=password_hash)).scalar_one()
                audit = db.execute(sa.text("INSERT INTO audit_logs(actor_type,actor_id,action,target_type,target_id,payload) VALUES ('admin',:id,'existing.guard.test','admin_user',:id,CAST(:payload AS jsonb)) RETURNING id"),dict(id=account,payload=json.dumps({'preserve':'original'}))).scalar_one()
                # Preserve existing disabled settings, including a capability
                # already configured before this migration began.
                db.execute(sa.text("UPDATE tenant_modules SET enabled=false WHERE tenant_id='jjtv' AND module_key='plans'"))
                db.execute(sa.text("INSERT INTO tenant_modules(tenant_id,module_key,enabled) VALUES ('jjtv','support',false),('guard-other','plans',false)"))
            first = tmp_path/'first'
            first.mkdir()
            receipt = run_upgrade(target,first,lambda: command.upgrade(config,'head'))
            assert receipt['before_revisions']==['e6a1c3d9b742']
            assert receipt['after_revisions']==[ScriptDirectory.from_config(config).get_current_head()]
            assert receipt['site_urls']=={'tenant_id':'jjtv','changed_fields':['web_url','h5_url'],'web_url':'https://jjtv.8jj.net','h5_url':'https://jjtv-h5.8jj.net'}
            assert receipt['initialized_accounts']==0
            before = json.loads((first/'identity-before.json').read_text())
            after = json.loads((first/'identity-after-migration.json').read_text())
            assert receipt['original_accounts_preserved']==len(before['users'])
            assert receipt['original_audit_rows_preserved']==before['table_counts']['audit_logs']
            for field,keys in (('legacy_bindings',('module_key',)),('modules',('tenant_id','module_key'))):
                previous = {tuple(row[key] for key in keys):row for row in before[field]}
                current = {tuple(row[key] for key in keys):row for row in after[field]}
                assert all(current[key]==row for key,row in previous.items())
                if field=='legacy_bindings':
                    expected = {(key,) for key in CAPABILITIES}-previous.keys()
                    assert all(current[key]['tenant_id']=='jjtv' for key in expected)
                else:
                    expected = {(tenant['id'],key) for tenant in before['tenants'] for key in CAPABILITIES}-previous.keys()
                    assert all(current[key]['enabled'] is (key[0]=='jjtv') for key in expected)
                assert current.keys()-previous.keys()==expected
            for table,count in before['table_counts'].items():
                if table not in {'legacy_business_bindings','tenant_modules'}:
                    assert after['table_counts'][table]==count
            assert before['users']==after['users'] and before['tenants']==after['tenants']
            with target.connect() as db:
                assert db.scalar(sa.text('SELECT password_hash FROM admin_users WHERE id=:id'),dict(id=account))==password_hash
                assert db.scalar(sa.text('SELECT payload FROM audit_logs WHERE id=:id'),dict(id=audit))=={'preserve':'original'}
                assert db.scalar(sa.text("SELECT enabled FROM tenant_modules WHERE tenant_id='jjtv' AND module_key='support'")) is False
            # A head-to-head deployment accepts no new capability rows and does
            # not repeat credential initialization or seed provisioning.
            second = tmp_path/'second'
            second.mkdir()
            retry = run_upgrade(target,second,lambda: command.upgrade(config,'head'))
            assert retry['site_urls'] is None
            assert retry['initialized_accounts']==0 and retry['content_defaults'] is None
            assert retry['before_revisions']==retry['after_revisions']
    finally:
        if target is not None:
            target.dispose()
        if created:
            with admin.connect() as db:
                owner = db.execute(sa.text('SELECT pg_get_userbyid(datdba),current_user FROM pg_database WHERE datname=:name'),dict(name=name)).first()
                assert owner is not None and owner[0]==owner[1] and name.startswith(source+'_capguard_')
                db.execute(sa.text('DROP DATABASE "'+name+'" WITH (FORCE)'))
        admin.dispose()
