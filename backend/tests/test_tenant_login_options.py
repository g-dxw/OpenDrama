"""Public tenant labels respect active status and the admin entry domain."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.tenancy.models import Tenant, TenantDomain

URL = '/tenant/v1/login-options'


@pytest.fixture()
def directory(session):
    a = Tenant(id='directory-private-a',code='directory-a',name='AAA Login tenant',kind='native',status='active')
    b = Tenant(id='directory-private-b',code='directory-b',name='AAA Login tenant',kind='native',status='active')
    paused = Tenant(id='directory-private-paused',code='directory-paused',name='Paused tenant',kind='native',status='paused')
    session.add_all([b,a,paused])
    session.flush()
    now = datetime.now(timezone.utc)
    session.add_all([
        TenantDomain(tenant_id=a.id,hostname='a-admin.example.com',kind='admin',status='active',verified_at=now),
        TenantDomain(tenant_id=b.id,hostname='b-site.example.com',kind='site',status='active',verified_at=now),
        TenantDomain(tenant_id=b.id,hostname='pending-admin.example.com',kind='admin',status='pending'),
        TenantDomain(tenant_id=b.id,hostname='verified-pending.example.com',kind='admin',status='pending',verified_at=now),
    ])
    session.commit()
    return a,b,paused


def test_public_directory_exposes_only_names_and_codes_for_active_tenants(session,directory,make):
    make.admin(username='private-staff-account')
    client = TestClient(app)
    response = client.get(URL)
    assert response.status_code == 200,response.text
    value = response.json()
    assert set(value) == {'items'}
    items = value['items']
    assert all(set(item) == {'code','name'} for item in items)
    assert {'code':'jjtv','name':'JJTV 自营'} in items
    assert {'code':'india-short-drama','name':'India Short Drama'} in items
    assert [item['code'] for item in items if item['name']=='AAA Login tenant'] == ['directory-a','directory-b']
    assert not any(item['code']=='directory-paused' for item in items)
    assert 'private-staff-account' not in response.text
    assert 'directory-private' not in response.text
    # The directory is public metadata, not a session or a way to change scope.
    assert client.get(URL,headers={'X-OpenDrama-Tenant':'directory-private-b'}).json() == value
    a,_,_ = directory
    a.status = 'paused'
    session.commit()
    current = client.get(URL)
    assert current.status_code == 200
    assert not any(item['code']==a.code for item in current.json()['items'])


def test_verified_admin_origin_lists_only_its_own_active_tenant(session,directory):
    a,_,_ = directory
    client = TestClient(app)
    response = client.get(URL,headers={'Origin':'https://a-admin.example.com'})
    assert response.status_code == 200,response.text
    assert response.json() == {'items':[{'code':a.code,'name':a.name}]}
    # Host also constrains a direct request without a browser Origin header.
    direct = TestClient(app,base_url='https://a-admin.example.com').get(URL)
    assert direct.status_code == 200 and direct.json() == response.json()
    a.status = 'paused'
    session.commit()
    paused = client.get(URL,headers={'Origin':'https://a-admin.example.com'})
    assert paused.status_code == 200 and paused.json() == {'items':[]}


@pytest.mark.parametrize('origin',[
    'https://pending-admin.example.com',
    'https://verified-pending.example.com',
    'https://b-site.example.com',
    'https://unknown-admin.example.com',
    'null',
])
def test_untrusted_or_site_origins_cannot_obtain_login_options(session,directory,origin):
    response = TestClient(app).get(URL,headers={'Origin':origin})
    assert response.status_code == 403,response.text
    assert response.json()['detail']['code'] == 'tenant_domain_mismatch'


def test_site_host_without_origin_is_not_an_admin_entry(session,directory):
    response = TestClient(app,base_url='https://b-site.example.com').get(URL)
    assert response.status_code == 403,response.text
    assert response.json()['detail']['code'] == 'tenant_domain_mismatch'
