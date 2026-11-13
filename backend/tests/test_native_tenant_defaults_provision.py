"""Provisioning creates isolated defaults without opening disabled capabilities."""
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.modules.content.models import Tag,HomeGroup,Drama
from app.modules.ops.config_models import ChannelTab,Banner


def provision(session,make,modules=None):
    client=TestClient(app)
    platform=make.platform_admin()
    signed=client.post('/platform/v1/auth/login',json={'username':platform.username,'password':'pass-1234'})
    assert signed.status_code==200,signed.text
    body={'code':'auto-default-studio','name':'Auto default studio'}
    if modules is not None: body['modules']=modules
    created=client.post('/platform/v1/tenants',json=body,headers={'Authorization':'Bearer '+signed.json()['token']})
    assert created.status_code==201,created.text
    value=created.json();owner=value['administrator'];tenant=value['tenant']
    logged=client.post('/tenant/v1/auth/login',json={'tenant_code':tenant['code'],'username':owner['username'],'password':owner['temporary_password']})
    assert logged.status_code==200,logged.text
    rotated=client.post('/tenant/v1/auth/password',json={'old_password':owner['temporary_password'],'new_password':'new-native-admin-1234'},headers={'Authorization':'Bearer '+logged.json()['token']})
    assert rotated.status_code==200,rotated.text
    return client,tenant,{'Authorization':'Bearer '+rotated.json()['token']},value


def test_platform_creation_initializes_own_content_defaults_in_the_same_tenant(session,make):
    client,tenant,headers,value=provision(session,make)
    assert value['content_defaults']
    tags=client.get('/admin/v1/tags',headers=headers)
    groups=client.get('/admin/v1/groups',headers=headers)
    tabs=client.get('/admin/v1/tabs',headers=headers)
    banners=client.get('/admin/v1/banners',headers=headers)
    for result in (tags,groups,tabs,banners): assert result.status_code==200,result.text
    assert len(tags.json()['items'])>=20
    assert len(groups.json()['items'])>=8
    assert len(tabs.json()['items'])>=4
    assert banners.json()['items']==[]
    assert tabs.json()['items'][0]['code']=='discover'
    assert tabs.json()['items'][0]['locked'] is True
    own_groups={row['id'] for row in groups.json()['items']}
    assert all(row['group_id'] is None or row['group_id'] in own_groups for row in tabs.json()['items'])
    for model in (Tag,HomeGroup,ChannelTab,Banner):
        assert session.scalar(select(func.count()).select_from(model).where(model.tenant_id=='jjtv'))==0
    assert session.scalar(select(func.count()).select_from(Drama).where(Drama.tenant_id==tenant['id']))==0
    public=client.get('/v1/config')
    assert public.status_code==200,public.text
    assert public.json()['tabs']==[]


def test_seeded_records_do_not_open_modules_that_platform_explicitly_disabled(session,make):
    client,tenant,headers,_=provision(session,make,modules=['dramas'])
    assert session.scalar(select(func.count()).select_from(Tag).where(Tag.tenant_id==tenant['id']))>=20
    context=client.get('/tenant/v1/context',headers=headers)
    assert context.status_code==200,context.text
    assert context.json()['enabled_modules']==['dramas']
    for path in ('/admin/v1/tags','/admin/v1/groups','/admin/v1/tabs','/admin/v1/banners'):
        assert client.get(path,headers=headers).status_code==403


def test_ordinary_preset_requires_explicit_authorization_for_new_home_ops(session,make):
    from app.modules.tenancy.models import TenantRole
    from app.modules.tenancy.permissions import preset_role_id
    client,tenant,owner,_=provision(session,make)
    role=session.get(TenantRole,preset_role_id(tenant['id'],'ops'))
    assert not set(role.permissions)&{'home_ops.view','home_ops.manage'}
    staff=make.admin(role='ops',username='default-content-worker',tenant_id=tenant['id'])
    logged=client.post('/tenant/v1/auth/login',json={'tenant_code':tenant['code'],'username':staff.username,'password':'pass-1234'})
    assert logged.status_code==200,logged.text
    headers={'Authorization':'Bearer '+logged.json()['token']}
    assert client.get('/admin/v1/tabs',headers=headers).status_code==403
    assigned=client.patch('/tenant/v1/roles/'+role.id,json={'version':role.version,'permissions':list(role.permissions)+['home_ops.view','home_ops.manage']},headers=owner)
    assert assigned.status_code==200,assigned.text
    assert client.get('/admin/v1/tabs',headers=headers).status_code==200
