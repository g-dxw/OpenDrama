"""Tenant-local role/menu/action authorization; no platform superuser shortcuts."""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from app.main import app
from app.modules.content.models import Drama,Episode
from app.modules.tenancy.india import IndiaError,IndiaReply
from app.modules.tenancy.models import DEFAULT_TENANT,INDIA_TENANT,Tenant,TenantRole,TenantMember
from app.modules.tenancy.permissions import preset_role_id,seed_tenant_defaults

ROLES='/tenant/v1/roles'
CONTENT_KEYS={'dramas.view','dramas.edit','dramas.upload','dramas.publish','dramas.delete','dramas.pricing','episodes.delete','reviews.view','reviews.decide','tags.view','tags.manage','groups.view','groups.manage'}

def login(client,person,code=None):
    path='/platform/v1/auth/login' if person.account_scope=='platform' else '/tenant/v1/auth/login'
    payload={'username':person.username,'password':'pass-1234'}
    if person.account_scope=='tenant':payload['tenant_code']=code or getattr(person,'_test_tenant_code',person.tenant_id)
    r=client.post(path,json=payload)
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token']}

@pytest.fixture()
def ctx(session,make):
    client=TestClient(app)
    owner,operator,finance=make.admin(role='admin'),make.admin(role='ops'),make.admin(role='finance')
    platform=make.platform_admin()
    return dict(db=session,make=make,client=client,tenant=DEFAULT_TENANT,owner=owner,operator=operator,finance=finance,platform=platform,
                owner_headers=login(client,owner),operator_headers=login(client,operator),finance_headers=login(client,finance),platform_headers=login(client,platform))

def catalog(ctx):
    r=ctx['client'].get(ROLES,headers=ctx['owner_headers'])
    assert r.status_code==200,r.text
    return r.json()

def role(ctx,permissions,name='Custom business role'):
    r=ctx['client'].post(ROLES,json={'name':name,'permissions':permissions},headers=ctx['owner_headers'])
    assert r.status_code==201,r.text
    value=r.json()
    assert value['builtin_key'] is None and set(value['permissions'])==set(permissions)
    return value

def assign(ctx,person,value):
    r=ctx['client'].patch('/tenant/v1/staff/'+str(person.id),json={'role_id':value['id']},headers=ctx['owner_headers'])
    assert r.status_code==200,r.text
    # Assignment may deliberately invalidate the old account authorization version.
    updated=login(ctx['client'],person)
    for key in ('operator','finance'):
        if ctx[key].id==person.id:ctx[key+'_headers']=updated
    return updated

def seed(ctx,tenant=None,title='Scoped draft',ready=False):
    d=Drama(tenant_id=tenant or ctx['tenant'],title=title,status='draft',coin_price_per_episode=20)
    ctx['db'].add(d);ctx['db'].flush()
    e=Episode(drama_id=d.id,episode_number=1,title='First',status='in_review' if ready else 'not_uploaded',bunny_video_id='fake-video' if ready else None)
    ctx['db'].add(e);ctx['db'].commit()
    return d,e

def test_tenant_admin_governs_business_roles_but_system_role_is_protected(ctx):
    data=catalog(ctx)
    protected=next(x for x in data['roles'] if x['builtin_key']=='tenant_admin')
    r=ctx['client'].patch(ROLES+'/'+protected['id'],json={'version':protected['version'],'permissions':[]},headers=ctx['owner_headers'])
    assert r.status_code in (403,409)
    assert ctx['client'].delete(ROLES+'/'+protected['id']+'?version='+str(protected['version']),headers=ctx['owner_headers']).status_code in (403,409)
    value=role(ctx,['dramas.view','dramas.edit'])
    for headers in (ctx['operator_headers'],ctx['platform_headers']):
        assert ctx['client'].get(ROLES,headers=headers).status_code==403
        assert ctx['client'].post(ROLES,json={'name':'Unauthorized','permissions':[]},headers=headers).status_code==403
    updated=ctx['client'].patch(ROLES+'/'+value['id'],json={'version':value['version'],'name':'Renamed'},headers=ctx['owner_headers'])
    assert updated.status_code==200 and updated.json()['version']==value['version']+1
    stale=ctx['client'].patch(ROLES+'/'+value['id'],json={'version':value['version'],'permissions':[]},headers=ctx['owner_headers'])
    assert stale.status_code==409
    assert ctx['client'].delete(ROLES+'/'+value['id']+'?version='+str(updated.json()['version']),headers=ctx['owner_headers']).status_code in (200,204)

@pytest.mark.parametrize('permissions',[['unknown.menu'],['dramas.edit'],['dramas.view','staff.manage'],['dramas.view','api_keys.manage']])
def test_roles_cannot_grant_unknown_system_or_dependency_free_permissions(ctx,permissions):
    r=ctx['client'].post(ROLES,json={'name':'Rejected','permissions':permissions},headers=ctx['owner_headers'])
    assert r.status_code==422,r.text

def test_assigned_custom_role_cannot_be_deleted(ctx):
    value=role(ctx,['dramas.view'])
    assign(ctx,ctx['operator'],value)
    r=ctx['client'].delete(ROLES+'/'+value['id']+'?version='+str(value['version']),headers=ctx['owner_headers'])
    assert r.status_code==409

def test_role_revocation_changes_current_context_and_HTTP_without_relogin(ctx):
    value=role(ctx,['dramas.view','dramas.edit'])
    headers=assign(ctx,ctx['operator'],value)
    assert ctx['client'].post('/admin/v1/dramas',json={'title':'Before revocation'},headers=headers).status_code==200
    changed=ctx['client'].patch(ROLES+'/'+value['id'],json={'version':value['version'],'permissions':['dramas.view']},headers=ctx['owner_headers'])
    assert changed.status_code==200
    assert ctx['client'].post('/admin/v1/dramas',json={'title':'Must not write'},headers=headers).status_code==403
    current=ctx['client'].get('/tenant/v1/context',headers=headers)
    assert current.status_code==200 and 'dramas.edit' not in current.json()['permissions']
    assert '/dramas' in current.json()['menu_keys']

@pytest.mark.parametrize('field,value',[('free_episode_count',5),('coin_price_per_episode',99),('cover_url','https://invalid.example/cover.jpg')])
def test_mixed_native_metadata_patch_rejects_ungranted_fields_atomically(ctx,field,value):
    d,_=seed(ctx)
    headers=assign(ctx,ctx['operator'],role(ctx,['dramas.view','dramas.edit']))
    r=ctx['client'].patch(f'/admin/v1/dramas/{d.id}',json={'title':'Must roll back',field:value},headers=headers)
    assert r.status_code==403,r.text
    ctx['db'].expire_all()
    assert ctx['db'].get(Drama,d.id).title=='Scoped draft'
    assert ctx['db'].get(Drama,d.id).coin_price_per_episode==20

def test_pricing_only_and_upload_only_do_not_grant_metadata_edit(ctx):
    d,_=seed(ctx)
    headers=assign(ctx,ctx['finance'],role(ctx,['dramas.view','dramas.pricing']))
    assert ctx['client'].patch(f'/admin/v1/dramas/{d.id}',json={'coin_price_per_episode':35},headers=headers).status_code==200
    assert ctx['client'].patch(f'/admin/v1/dramas/{d.id}',json={'title':'Forbidden'},headers=headers).status_code==403
    headers=assign(ctx,ctx['operator'],role(ctx,['dramas.view','dramas.upload'],'Uploader'))
    assert ctx['client'].patch(f'/admin/v1/dramas/{d.id}',json={'cover_url':'https://invalid.example/cover.jpg'},headers=headers).status_code==200
    assert ctx['client'].patch(f'/admin/v1/dramas/{d.id}',json={'title':'Forbidden'},headers=headers).status_code==403

@pytest.mark.parametrize('path,body',[('/episodes/{e}/publish',{}),('/episodes/{e}/unpublish',{}),('/episodes/bulk-status',{'episode_ids':[1],'action':'publish'}),('/dramas/{d}/status',{'to_status':'published'}),('/dramas/{d}/schedule',{'at':'2099-01-01T00:00:00Z'}),('/schedules/apply',{}),('/reviews/{d}/publish',{})])
def test_every_publication_path_needs_publication_permission(ctx,path,body):
    d,e=seed(ctx,ready=True)
    headers=assign(ctx,ctx['operator'],role(ctx,['dramas.view','dramas.edit','reviews.view','reviews.decide']))
    r=ctx['client'].post('/admin/v1'+path.format(d=d.id,e=e.id),json=body,headers=headers)
    assert r.status_code==403,(path,r.text)
    ctx['db'].expire_all();assert ctx['db'].get(Drama,d.id).status=='draft'

def test_upload_TUS_HEAD_and_mutations_are_not_read_only_shortcuts(ctx):
    _,e=seed(ctx)
    headers=assign(ctx,ctx['operator'],role(ctx,['dramas.view']))
    for method,path,payload in [('HEAD','/admin/v1/dev-tus/not-authorized',None),('POST',f'/admin/v1/episodes/{e.id}/upload-session',{'filename':'x.mp4','mime':'video/mp4','size_bytes':8,'width':720,'height':1280,'duration_seconds':12}),('POST',f'/admin/v1/episodes/{e.id}/sync-status',{}),('POST',f'/admin/v1/episodes/{e.id}/upload-headers',{})]:
        kwargs={'headers':headers}
        if payload is not None:kwargs['json']=payload
        assert ctx['client'].request(method,path,**kwargs).status_code==403

def test_episode_deletion_does_not_grant_whole_drama_or_account_management(ctx):
    d,e=seed(ctx)
    headers=assign(ctx,ctx['operator'],role(ctx,['dramas.view','episodes.delete']))
    assert ctx['client'].delete(f'/admin/v1/episodes/{e.id}',headers=headers).status_code==204
    assert ctx['client'].post(f'/admin/v1/dramas/{d.id}/delete',json={'confirm':True},headers=headers).status_code==403
    assert ctx['client'].get('/tenant/v1/staff',headers=headers).status_code==403
    assert ctx['client'].post('/tenant/v1/staff',json={'username':'escalation','role_id':preset_role_id(DEFAULT_TENANT,'tenant_admin')},headers=headers).status_code==403

def test_tags_and_groups_need_their_own_mutation_grants(ctx):
    headers=assign(ctx,ctx['operator'],role(ctx,['dramas.view']))
    for path,body in [('/admin/v1/tags',{'name_en':'Forbidden'}),('/admin/v1/groups',{'name':'Forbidden'})]:
        assert ctx['client'].post(path,json=body,headers=headers).status_code==403
    assert ctx['client'].get('/admin/v1/groups',headers=headers).status_code==403
    # Tags remain an intentional read dependency of the drama editor.
    assert ctx['client'].get('/admin/v1/tags',headers=headers).status_code==200

def test_business_grants_cannot_reach_another_tenants_data_or_roles(ctx):
    other=Tenant(id='other-permission-test',code='other-permission-test',name='Other',kind='native',status='active')
    ctx['db'].add(other);ctx['db'].flush();seed_tenant_defaults(ctx['db'],other);ctx['db'].commit()
    d,e=seed(ctx,other.id,'Other content')
    headers=assign(ctx,ctx['operator'],role(ctx,sorted(CONTENT_KEYS)))
    assert ctx['client'].get(f'/admin/v1/dramas/{d.id}',headers=headers).status_code==404
    assert ctx['client'].get('/admin/v1/dramas',headers={**headers,'X-JJTV-Tenant':other.id}).status_code==403
    assert ctx['client'].patch('/tenant/v1/staff/'+str(ctx['operator'].id),json={'role_id':preset_role_id(other.id,'tenant_admin')},headers=ctx['owner_headers']).status_code in (404,422)

@pytest.mark.parametrize('state',['pending','rejected'])
def test_due_schedules_cannot_bypass_supplier_approval(ctx,state):
    from app.modules.ops.models import Supplier
    supplier=Supplier(name='Unapproved supplier '+state)
    ctx['db'].add(supplier);ctx['db'].flush()
    d,e=seed(ctx,ready=True)
    d.supplier_id=supplier.id;d.review_state=state;d.status='in_review';d.scheduled_publish_at=datetime.now(timezone.utc)-timedelta(minutes=1)
    ctx['db'].commit()
    headers=assign(ctx,ctx['operator'],role(ctx,['dramas.view','dramas.publish']))
    r=ctx['client'].post('/admin/v1/schedules/apply',headers=headers)
    assert r.status_code==200,r.text
    ctx['db'].expire_all();assert ctx['db'].get(Drama,d.id).status=='in_review' and ctx['db'].get(Drama,d.id).scheduled_publish_at is not None
    d=ctx['db'].get(Drama,d.id);d.review_state='approved';ctx['db'].commit()
    assert ctx['client'].post('/admin/v1/schedules/apply',headers=headers).status_code==200
    ctx['db'].expire_all();assert ctx['db'].get(Drama,d.id).status=='published'

def test_tenant_api_key_staff_scope_is_bounded_and_cannot_enter_platform(ctx):
    def issue(scopes):
        r=ctx['client'].post('/admin/v1/api-keys',json={'user_id':ctx['owner'].id,'name':'Scoped key','scopes':scopes,'expires_in_days':90},headers=ctx['owner_headers'])
        assert r.status_code==201,r.text
        return {'Authorization':'Bearer '+r.json()['key']}
    read_write=issue(['read','write'])
    privileged=issue(['read','write','staff'])
    for method in ('GET','POST'):
        kwargs={'headers':read_write}
        if method=='POST':kwargs['json']={'name':'Forbidden','permissions':['dramas.view']}
        r=ctx['client'].request(method,ROLES,**kwargs)
        assert r.status_code==403 and r.json()['detail']['code']=='scope_denied'
    r=ctx['client'].post(ROLES,json={'name':'Staff-scoped role','permissions':['dramas.view']},headers=privileged)
    assert r.status_code==201,r.text
    assert ctx['client'].get('/platform/v1/tenants',headers=privileged).status_code==403
    assert ctx['client'].post('/platform/v1/tenants/jjtv/enter',json={},headers=privileged).status_code==403
    assert ctx['client'].post('/tenant/v1/auth/password',json={'old_password':'pass-1234','new_password':'changed-password'},headers=privileged).status_code==403

@pytest.fixture()
def india_ctx(ctx,monkeypatch):
    owner=ctx['make'].admin(role='admin',tenant_id=INDIA_TENANT)
    user=ctx['make'].admin(role='ops',tenant_id=INDIA_TENANT)
    current={**ctx,'tenant':INDIA_TENANT,'owner':owner,'operator':user,'owner_headers':login(ctx['client'],owner),'operator_headers':login(ctx['client'],user)}
    monkeypatch.setenv('TENANT_CONNECTION_KEY',Fernet.generate_key().decode());monkeypatch.setenv('INDIA_API_BASE_URL','https://india.example/v1')
    calls=[];behavior={'deny':False}
    class FakeIndia:
        def __init__(self,**kwargs):pass
        async def call(self,action,**kwargs):
            calls.append((action,kwargs))
            if action=='login':return IndiaReply({'access_token':'a'*43,'refresh_token':'r'*43,'user':{'id':'old-editor','name':'Editor','role':'editor'}},'old-login')
            if behavior['deny']:raise IndiaError('india_forbidden',403,upstream_code='FORBIDDEN',request_id='old-denied')
            return IndiaReply({'id':'series-existing','version':3},'old-success')
    from app.modules.tenancy import router
    monkeypatch.setattr(router,'IndiaClient',FakeIndia)
    current.update(calls=calls,behavior=behavior,base='/admin/v1/tenants/india-short-drama/india')
    return current

def connect_india(ctx,headers):
    r=ctx['client'].post(ctx['base']+'/session',json={'username':'old-editor','password':'old-password'},headers=headers)
    assert r.status_code==200,r.text

@pytest.mark.parametrize('field,value',[('free_episode_count',0),('member_window',{'hours':0})])
def test_india_pricing_fields_require_grant_even_when_the_value_looks_unchanged(india_ctx,field,value):
    ctx=india_ctx;headers=assign(ctx,ctx['operator'],role(ctx,['dramas.view','dramas.edit']))
    connect_india(ctx,headers);before=len(ctx['calls'])
    r=ctx['client'].patch(ctx['base']+'/admin/series/series-existing',json={'version':3,field:value},headers={**headers,'Idempotency-Key':str(uuid4())})
    assert r.status_code==403 and len(ctx['calls'])==before

def test_india_pricing_only_and_upstream_authority_remain_independent(india_ctx):
    ctx=india_ctx;headers=assign(ctx,ctx['operator'],role(ctx,['dramas.view','dramas.pricing']))
    connect_india(ctx,headers)
    target=ctx['base']+'/admin/series/series-existing'
    key={**headers,'Idempotency-Key':str(uuid4())}
    r=ctx['client'].patch(target,json={'version':3,'free_episode_count':1},headers=key)
    assert r.status_code==200,r.text
    before=len(ctx['calls']);assert ctx['client'].patch(target,json={'version':3,'title':'Forbidden'},headers={**headers,'Idempotency-Key':str(uuid4())}).status_code==403
    assert len(ctx['calls'])==before
    ctx['behavior']['deny']=True
    r=ctx['client'].patch(target,json={'version':3,'free_episode_count':1},headers={**headers,'Idempotency-Key':str(uuid4())})
    assert r.status_code==403 and r.json()['detail']['code']=='india_forbidden'
