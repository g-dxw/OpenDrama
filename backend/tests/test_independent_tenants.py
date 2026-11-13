"""Independent business tenant identity, control-plane and module boundaries."""
from __future__ import annotations
from datetime import datetime,timezone
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.modules.ops.models import AdminUser
from app.modules.ops import service as auth
from app.modules.tenancy.models import Tenant,TenantRole,TenantMember
from app.modules.tenancy.permissions import preset_role_id
from app.modules.content.models import Drama
from app.modules.common.models import AuditLog

PLATFORM='/platform/v1'
TENANT='/tenant/v1'


def bearer(token):return {'Authorization':'Bearer '+token}

def tenant_login(client,code,username,password='pass-1234'):
    return client.post(TENANT+'/auth/login',json=dict(tenant_code=code,username=username,password=password))

def logged(client,person,code=None,password='pass-1234'):
    if person.account_scope=='platform':r=client.post(PLATFORM+'/auth/login',json=dict(username=person.username,password=password))
    else:r=tenant_login(client,code or getattr(person,'_test_tenant_code',person.tenant_id),person.username,password)
    assert r.status_code==200,r.text
    return bearer(r.json()['token'])

@pytest.fixture()
def env(session,make):
    client=TestClient(app)
    platform=make.platform_admin()
    return dict(db=session,make=make,client=client,platform=platform,pheaders=logged(client,platform))

def provision(env,code=None):
    code=code or 'studio-'+uuid4().hex[:12]
    r=env['client'].post(PLATFORM+'/tenants',json=dict(code=code,name='Independent '+code,modules=['dramas','reviews','tags','groups']),headers=env['pheaders'])
    assert r.status_code==201,r.text
    value=r.json();assert value['tenant']['code']==code
    return value

def owner_for(env,value):
    tenant=value['tenant'];admin=value['administrator']
    first=tenant_login(env['client'],tenant['code'],admin['username'],admin['temporary_password'])
    assert first.status_code==200,first.text
    assert first.json()['context']['user']['must_change_password'] is True
    initial=bearer(first.json()['token'])
    changed=env['client'].post(TENANT+'/auth/password',json={'old_password':admin['temporary_password'],'new_password':'pass-1234'},headers=initial)
    assert changed.status_code==200,changed.text
    result=tenant_login(env['client'],tenant['code'],admin['username'])
    assert result.status_code==200,result.text
    return bearer(result.json()['token'])

def module_data(env,tenant):
    r=env['client'].get(PLATFORM+'/tenants/'+tenant+'/modules',headers=env['pheaders'])
    assert r.status_code==200,r.text
    return r.json()

def test_tenant_login_always_requires_context_and_never_falls_back_to_platform(env):
    native=env['make'].admin(role='admin',username='same-name')
    platform=env['make'].platform_admin(username='same-name',password='platform-pass-1234')
    for path in (TENANT+'/auth/login','/admin/v1/auth/login'):
        missing=env['client'].post(path,json={'username':native.username,'password':'pass-1234'})
        assert missing.status_code==400 and missing.json()['detail']['code']=='tenant_required'
        empty=env['client'].post(path,json={'tenant_code':'','username':native.username,'password':'pass-1234'})
        assert empty.status_code==400 and empty.json()['detail']['code']=='tenant_required'
    tenant=tenant_login(env['client'],'jjtv','same-name')
    assert tenant.status_code==200 and tenant.json()['context']['account_scope']=='tenant'
    assert tenant.json()['context']['user']['id']==native.id
    assert env['client'].post(PLATFORM+'/auth/login',json={'username':'same-name','password':'pass-1234'}).status_code==401
    p=env['client'].post(PLATFORM+'/auth/login',json={'username':'same-name','password':'platform-pass-1234'})
    assert p.status_code==200 and p.json()['context']['account_scope']=='platform'
    assert p.json()['context']['user']['id']==platform.id
    assert tenant_login(env['client'],'jjtv','same-name','platform-pass-1234').status_code==401
    assert tenant_login(env['client'],'not-a-tenant','same-name').status_code==401


def test_same_username_is_independent_in_different_tenants(env):
    a,b=provision(env),provision(env)
    person_a=env['make'].admin(role='ops',tenant_id=a['tenant']['id'],username='same-employee',password='alpha-pass-1234')
    person_b=env['make'].admin(role='finance',tenant_id=b['tenant']['id'],username='same-employee',password='bravo-pass-1234')
    first=tenant_login(env['client'],a['tenant']['code'],'same-employee','alpha-pass-1234')
    second=tenant_login(env['client'],b['tenant']['code'],'same-employee','bravo-pass-1234')
    assert first.status_code==second.status_code==200
    assert first.json()['context']['user']['id']==person_a.id
    assert second.json()['context']['user']['id']==person_b.id
    assert person_a.id!=person_b.id
    assert tenant_login(env['client'],a['tenant']['code'],'same-employee','bravo-pass-1234').status_code==401
    assert tenant_login(env['client'],b['tenant']['code'],'same-employee','alpha-pass-1234').status_code==401


def test_new_tenant_owner_changes_temporary_password_before_business_access(env):
    value=provision(env);tenant=value['tenant'];owner=value['administrator']
    initial=tenant_login(env['client'],tenant['code'],owner['username'],owner['temporary_password'])
    assert initial.status_code==200 and initial.json()['context']['user']['must_change_password'] is True
    headers=bearer(initial.json()['token'])
    assert env['client'].post('/admin/v1/dramas',json={'title':'Before password change'},headers=headers).status_code==403
    rotated=env['client'].post(TENANT+'/auth/password',json={'old_password':owner['temporary_password'],'new_password':'new-owner-password'},headers=headers)
    assert rotated.status_code==200,rotated.text
    assert env['client'].get(TENANT+'/context',headers=headers).status_code==401
    current=tenant_login(env['client'],tenant['code'],owner['username'],'new-owner-password')
    assert current.status_code==200 and current.json()['context']['user']['must_change_password'] is False
    assert env['client'].post('/admin/v1/dramas',json={'title':'After password change'},headers=bearer(current.json()['token'])).status_code==200


def test_tenant_administrator_manages_only_its_accounts_and_protected_system_role(env):
    a,b=provision(env),provision(env)
    aheaders=owner_for(env,a);bheaders=owner_for(env,b)
    arole=preset_role_id(a['tenant']['id'],'ops')
    brole=preset_role_id(b['tenant']['id'],'ops')
    created=env['client'].post(TENANT+'/staff',json={'username':'local-editor','display_name':'A editor','role_id':arole},headers=aheaders)
    assert created.status_code==201,created.text
    staff=created.json();assert staff['tenant_id']==a['tenant']['id'] and staff['account_scope']=='tenant'
    assert staff['temporary_password']
    other=env['client'].post(TENANT+'/staff',json={'username':'local-editor','role_id':brole},headers=bheaders)
    assert other.status_code==201,other.text
    assert other.json()['id']!=staff['id']
    directory=env['client'].get(TENANT+'/staff',headers=aheaders)
    assert directory.status_code==200 and all(x['tenant_id']==a['tenant']['id'] for x in directory.json()['items'])
    assert env['client'].patch(TENANT+'/staff/'+str(other.json()['id']),json={'display_name':'Must not change'},headers=aheaders).status_code==404
    assert env['client'].post(TENANT+'/staff',json={'username':'cross-role','role_id':brole},headers=aheaders).status_code in (404,422)
    assert env['client'].get(PLATFORM+'/tenants',headers=aheaders).status_code==403
    assert env['client'].post(PLATFORM+'/tenants',json={'code':'escape','name':'Escape'},headers=aheaders).status_code==403
    assert env['client'].get('/admin/v1/tenants/'+b['tenant']['id']+'/roles',headers=aheaders).status_code==403
    assert env['client'].put('/admin/v1/tenants/'+a['tenant']['id']+'/members',json={'members':[]},headers=aheaders).status_code==409


def test_last_active_tenant_admin_cannot_be_demoted_or_disabled(env):
    value=provision(env);headers=owner_for(env,value);tenant=value['tenant'];owner=value['administrator']
    for payload in ({'status':'disabled'},{'role_id':preset_role_id(tenant['id'],'ops')}):
        r=env['client'].patch(TENANT+'/staff/'+str(owner['id']),json=payload,headers=headers)
        assert r.status_code==409,r.text
    peer=env['make'].admin(role='admin',tenant_id=tenant['id'])
    peer_headers=logged(env['client'],peer,tenant['code'])
    r=env['client'].patch(TENANT+'/staff/'+str(owner['id']),json={'status':'disabled'},headers=peer_headers)
    assert r.status_code==200,r.text
    assert env['client'].get(TENANT+'/context',headers=headers).status_code==401
    assert env['client'].get(TENANT+'/context',headers=peer_headers).status_code==200


def test_employee_reset_revokes_current_sessions_and_requires_password_change(env):
    value=provision(env);owner=owner_for(env,value);tenant=value['tenant']
    person=env['make'].admin(role='ops',tenant_id=tenant['id'])
    before=logged(env['client'],person,tenant['code'])
    reset=env['client'].post(TENANT+'/staff/'+str(person.id)+'/reset-password',json={},headers=owner)
    assert reset.status_code==200,reset.text
    assert env['client'].get(TENANT+'/context',headers=before).status_code==401
    assert tenant_login(env['client'],tenant['code'],person.username).status_code==401
    after=tenant_login(env['client'],tenant['code'],person.username,reset.json()['temporary_password'])
    assert after.status_code==200 and after.json()['context']['user']['must_change_password'] is True


def test_business_session_cannot_switch_tenant_or_supply_ownership_in_payload(env):
    a,b=provision(env),provision(env)
    headers=owner_for(env,a)
    assert env['client'].get('/admin/v1/dramas',headers={**headers,'X-JJTV-Tenant':b['tenant']['id']}).status_code==403
    result=env['client'].post('/admin/v1/dramas',json={'title':'Bound ownership','tenant_id':b['tenant']['id']},headers=headers)
    assert result.status_code in (200,422),result.text
    if result.status_code==200:
        env['db'].expire_all();assert env['db'].get(Drama,result.json()['id']).tenant_id==a['tenant']['id']
    assert env['client'].get(PLATFORM+'/context',headers=headers).status_code==403


def test_platform_normal_token_has_no_implicit_business_access_and_support_is_scoped(env):
    a,b=provision(env),provision(env)
    assert env['client'].get('/admin/v1/dramas',headers=env['pheaders']).status_code==403
    assert env['client'].get('/admin/v1/dramas',headers={**env['pheaders'],'X-JJTV-Tenant':a['tenant']['id']}).status_code==403
    support=env['client'].post(PLATFORM+'/tenants/'+a['tenant']['id']+'/enter',json={},headers=env['pheaders'])
    assert support.status_code==200,support.text
    value=support.json();token=value['token'];context=value['context']
    assert context['session_kind']=='support' and context['tenant']['id']==a['tenant']['id']
    assert token not in value['entry_url']
    assert datetime.fromisoformat(value['expires_at'].replace('Z','+00:00'))>datetime.now(timezone.utc)
    assert 0<auth.get_redis().ttl(auth.SESSION_PREFIX+token)<=1800
    headers=bearer(token)
    assert env['client'].get('/admin/v1/dramas',headers=headers).status_code==200
    assert env['client'].get('/admin/v1/dramas',headers={**headers,'X-JJTV-Tenant':b['tenant']['id']}).status_code==403
    assert env['client'].get(PLATFORM+'/tenants',headers=headers).status_code==403
    assert env['client'].post(PLATFORM+'/tenants/'+b['tenant']['id']+'/enter',json={},headers=headers).status_code==403
    written=env['client'].post('/admin/v1/dramas',json={'title':'Explicit platform support'},headers=headers)
    assert written.status_code==200,written.text
    env['db'].expire_all()
    audit=env['db'].scalar(select(AuditLog).where(AuditLog.actor_id==env['platform'].id,AuditLog.tenant_id==a['tenant']['id'],AuditLog.target_id==written.json()['id']).order_by(AuditLog.id.desc()))
    assert audit is not None and audit.actor_scope=='platform'
    records=env['client'].get(PLATFORM+'/audit',params={'tenant_id':a['tenant']['id']},headers=env['pheaders'])
    assert records.status_code==200,records.text
    recorded=next(row for row in records.json()['items'] if row['id']==audit.id)
    assert recorded['actor_id']==env['platform'].id and recorded['actor_scope']=='platform'
    assert recorded['target_type']=='dramas' and recorded['target_id']==written.json()['id']
    assert recorded['payload']['method']=='POST' and recorded['payload']['path']=='/admin/v1/dramas'
    assert env['client'].post(TENANT+'/auth/logout',headers=headers).status_code==200
    assert env['client'].get('/admin/v1/dramas',headers=headers).status_code==401
    assert env['client'].get(PLATFORM+'/context',headers=env['pheaders']).status_code==200


def test_support_ttl_expiry_and_legacy_unscoped_tokens_are_not_accepted(env):
    value=provision(env)
    entered=env['client'].post(PLATFORM+'/tenants/'+value['tenant']['id']+'/enter',json={},headers=env['pheaders'])
    assert entered.status_code==200,entered.text
    token=entered.json()['token'];auth.get_redis().expire(auth.SESSION_PREFIX+token,0)
    assert env['client'].get(TENANT+'/context',headers=bearer(token)).status_code==401
    legacy='legacy-token-must-not-authenticate'
    auth.get_redis().set(auth.LEGACY_SESSION_PREFIX+legacy,str(env['platform'].id),ex=60)
    assert env['client'].get('/admin/v1/dramas',headers=bearer(legacy)).status_code==401
    assert env['client'].get(PLATFORM+'/context',headers=bearer(legacy)).status_code==401


def test_module_entitlements_mask_role_grants_immediately_without_erasing_the_role(env):
    value=provision(env);tenant=value['tenant'];owner=owner_for(env,value)
    role=env['client'].post(TENANT+'/roles',json={'name':'Module editor','permissions':['dramas.view','dramas.edit']},headers=owner)
    assert role.status_code==201,role.text
    person=env['make'].admin(role='ops',tenant_id=tenant['id'])
    assert env['client'].patch(TENANT+'/staff/'+str(person.id),json={'role_id':role.json()['id']},headers=owner).status_code==200
    headers=logged(env['client'],person,tenant['code'])
    assert env['client'].post('/admin/v1/dramas',json={'title':'Enabled'},headers=headers).status_code==200
    state=module_data(env,tenant['id'])
    enabled=[x['key'] for x in state['items'] if x['enabled'] and x['key']!='dramas']
    disabled=env['client'].put(PLATFORM+'/tenants/'+tenant['id']+'/modules',json={'version':state['version'],'module_keys':enabled},headers=env['pheaders'])
    assert disabled.status_code==200,disabled.text
    assert env['client'].get('/admin/v1/dramas',headers=headers).status_code==403
    assert env['client'].post('/admin/v1/dramas',json={'title':'Disabled'},headers=headers).status_code==403
    current=env['client'].get(TENANT+'/context',headers=headers)
    assert current.status_code==200 and '/dramas' not in current.json()['menu_keys'] and 'dramas.edit' not in current.json()['permissions']
    env['db'].expire_all();assert set(env['db'].get(TenantRole,role.json()['id']).permissions)=={'dramas.view','dramas.edit'}
    assert env['client'].get(TENANT+'/roles',headers=owner).status_code==200
    retry=env['client'].put(PLATFORM+'/tenants/'+tenant['id']+'/modules',json={'version':state['version'],'module_keys':enabled+['dramas']},headers=env['pheaders'])
    assert retry.status_code==409
    state=module_data(env,tenant['id'])
    assert env['client'].put(PLATFORM+'/tenants/'+tenant['id']+'/modules',json={'version':state['version'],'module_keys':enabled+['dramas']},headers=env['pheaders']).status_code==200
    assert env['client'].get('/admin/v1/dramas',headers=headers).status_code==200


def test_unadapted_legacy_modules_cannot_be_enabled_for_another_tenant(env):
    value=provision(env);tenant=value['tenant'];owner=owner_for(env,value)
    state=module_data(env,tenant['id'])
    unsupported=[x for x in state['items'] if not x['supported']]
    assert {'users','finance','plans'}<={x['key'] for x in unsupported}
    enabled=[x['key'] for x in state['items'] if x['enabled']]
    r=env['client'].put(PLATFORM+'/tenants/'+tenant['id']+'/modules',json={'version':state['version'],'module_keys':enabled+['users']},headers=env['pheaders'])
    assert r.status_code==422,r.text
    assert env['client'].put(PLATFORM+'/tenants/'+tenant['id']+'/modules',json={'version':state['version'],'module_keys':enabled},headers=owner).status_code==403
    assert env['client'].get('/admin/v1/users',headers=owner).status_code==403
    assert env['client'].post(TENANT+'/roles',json={'name':'Unsupported finance','permissions':['finance.view']},headers=owner).status_code==422


def test_new_owner_cannot_satisfy_rotation_by_reusing_temporary_password(env):
    value=provision(env);tenant=value["tenant"];owner=value["administrator"]
    initial=tenant_login(env["client"],tenant["code"],owner["username"],owner["temporary_password"])
    assert initial.status_code==200,initial.text
    headers=bearer(initial.json()["token"])
    result=env["client"].post(TENANT+"/auth/password",json={"old_password":owner["temporary_password"],"new_password":owner["temporary_password"]},headers=headers)
    assert result.status_code==422,result.text
    context=env["client"].get(TENANT+"/context",headers=headers)
    assert context.status_code==200 and context.json()["user"]["must_change_password"] is True
    assert env["client"].post("/admin/v1/dramas",json={"title":"Still requires rotation"},headers=headers).status_code==403


def test_disabled_selector_modules_reject_shared_readers_and_writers(env):
    tenant='jjtv'
    actor=env['make'].admin(role='admin')
    headers=logged(env['client'],actor,tenant)
    state=module_data(env,tenant)
    enabled=[x['key'] for x in state['items'] if x['enabled'] and x['key'] not in {'tags','groups','dramas','reviews'}]
    response=env['client'].put(PLATFORM+'/tenants/'+tenant+'/modules',json={'version':state['version'],'module_keys':enabled},headers=env['pheaders'])
    assert response.status_code==200,response.text
    # The account still has drama/homepage permissions, but selector aliases
    # must not reopen the functions that the platform explicitly closed.
    for path in ('/admin/v1/tags','/admin/v1/groups','/admin/v1/dramas'):
        assert env['client'].get(path,headers=headers).status_code==403
        assert env['client'].post(path,json={'name':'closed'},headers=headers).status_code==403
