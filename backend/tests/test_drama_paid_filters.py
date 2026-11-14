"""Payment-shape filters share row/count predicates and tenant scope."""
from fastapi.testclient import TestClient
from app.main import app
from app.modules.tenancy.models import Tenant

def test_paid_shape_filters_and_status_buckets_keep_other_filters(make,session):
    staff=make.admin(role='ops')
    client=TestClient(app)
    signed=client.post('/admin/v1/auth/login',json=dict(tenant_code='jjtv',username=staff.username,password='pass-1234'))
    assert signed.status_code==200,signed.text
    headers={'Authorization':'Bearer '+signed.json()['token']}
    paid=make.drama(title='Member Coins',status='published');paid.access_mode='members';paid.member_pays_coins=True
    included=make.drama(title='Member Included',status='draft');included.access_mode='members';included.member_pays_coins=False
    public=make.drama(title='Public Coins',status='published');public.access_mode='public';public.member_pays_coins=True
    session.add(Tenant(id='filter-other',code='filter-other',name='Other',kind='native',status='active'));session.flush()
    other=make.drama(title='Other Member',status='published');other.tenant_id='filter-other';other.access_mode='members';other.member_pays_coins=True
    session.commit()
    result=client.get('/admin/v1/dramas',params=dict(access_mode='members',page_size=1,status='published'),headers=headers)
    assert result.status_code==200,result.text
    body=result.json();assert body['total']==1 and body['items'][0]['id']==paid.id
    assert body['status_counts']['all']==2 and body['status_counts']['draft']==1 and body['status_counts']['published']==1
    coins=client.get('/admin/v1/dramas',params=dict(access_mode='members',member_pays_coins='true'),headers=headers).json()
    assert [x['id'] for x in coins['items']]==[paid.id]
    assert coins['status_counts']['all']==1
    free=client.get('/admin/v1/dramas',params=dict(member_pays_coins='false',q='Included'),headers=headers).json()
    assert [x['id'] for x in free['items']]==[included.id]
    assert free['status_counts']['all']==1 and free['status_counts']['draft']==1
    assert client.get('/admin/v1/dramas',params=dict(access_mode='unknown'),headers=headers).status_code==422
