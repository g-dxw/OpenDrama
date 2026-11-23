"""HTTP merchant binding checks never infer independent identity from a legacy placeholder."""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.modules.billing import commerce
from app.modules.billing.commerce_models import StripeAccount,SiteMerchantBinding
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.permissions import seed_tenant_defaults
from tests.test_commerce import commercial


def login(client,actor):
    platform=actor.account_scope=='platform'
    data={'username':actor.username,'password':'pass-1234'}
    if not platform:data['tenant_code']=actor._test_tenant_code
    response=client.post('/platform/v1/auth/login' if platform else '/tenant/v1/auth/login',json=data)
    assert response.status_code==200,response.text
    return {'Authorization':'Bearer '+response.json()['token']}


@pytest.fixture
def legacy_binding(session,make,commercial):
    _user,original_owner,_calls=commercial
    session.add(StripeAccount(code='env',name='Unverified migrated merchant',status='active',is_default=True,remote_id=None,livemode=False))
    session.add(SiteMerchantBinding(tenant_id='jjtv',account_code='env',explicit_reuse=False));session.commit()
    commerce.save_account(session,'alias',dict(name='Possible merchant alias',status='active',secret_key='sk_test_alias',webhook_secret='whsec_alias'),original_owner.id)
    target=Tenant(id='binding-target',code='binding-target',name='Target site',kind='native',status='active')
    session.add(target);seed_tenant_defaults(session,target);session.commit()
    owner=make.admin(role='admin',tenant_id=target.id)
    platform=make.platform_admin()
    client=TestClient(app)
    return dict(client=client,target=target,headers=login(client,owner),platform_headers=login(client,platform),original_headers=login(client,original_owner))


@pytest.mark.parametrize('allow_reuse',[False,True])
def test_unverified_bound_legacy_identity_requires_verification_even_for_alias_acknowledgement(session,legacy_binding,allow_reuse):
    case=legacy_binding
    response=case['client'].put('/tenant/v1/merchant-binding',headers=case['headers'],json={'account_code':'alias','allow_reuse':allow_reuse})
    assert response.status_code==409,response.text
    assert response.json()['detail']=={
        'code':'merchant_identity_verification_required',
        'message':'Verify the existing merchant identity before assigning another merchant to an independent site.',
        'action':'verify_merchant','account_code':'env'}
    session.expire_all()
    assert session.get(SiteMerchantBinding,case['target'].id) is None
    assert session.get(SiteMerchantBinding,'jjtv').account_code=='env'


@pytest.mark.parametrize('same_merchant',[False,True])
def test_verified_legacy_identity_allows_independent_binding_or_requires_explicit_reuse(session,legacy_binding,monkeypatch,same_merchant):
    case=legacy_binding;verified=[]
    def identity(secret):
        verified.append(secret)
        remote='acct_unit' if same_merchant else 'acct_distinct_legacy'
        return remote,False,{'id':remote,'country':'US'}
    monkeypatch.setattr(commerce,'_stripe_identity',identity)
    check=case['client'].post('/platform/v1/stripe/accounts/env/test',headers=case['platform_headers'])
    assert check.status_code==200,check.text
    assert len(verified)==1
    response=case['client'].put('/tenant/v1/merchant-binding',headers=case['headers'],json={'account_code':'alias','allow_reuse':False})
    if same_merchant:
        assert response.status_code==409,response.text
        assert 'explicitly allow account reuse' in response.json()['detail']
        response=case['client'].put('/tenant/v1/merchant-binding',headers=case['headers'],json={'account_code':'alias','allow_reuse':True})
    assert response.status_code==200,response.text
    assert response.json()['account_code']=='alias'
    assert response.json()['explicit_reuse'] is same_merchant
    assert 'sk_test_' not in response.text and 'whsec_' not in response.text
    session.expire_all()
    binding=session.get(SiteMerchantBinding,case['target'].id)
    assert binding.account_code=='alias' and binding.explicit_reuse is same_merchant


def test_fresh_installation_without_legacy_env_credentials_can_bind_first_real_merchant(legacy_binding,monkeypatch):
    case=legacy_binding
    monkeypatch.setenv('STRIPE_SECRET_KEY','')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','')
    response=case['client'].put('/tenant/v1/merchant-binding',headers=case['headers'],json={'account_code':'alias','allow_reuse':False})
    assert response.status_code==200,response.text
    assert response.json()['account_code']=='alias' and response.json()['explicit_reuse'] is False


def test_legacy_default_switch_cannot_bypass_unknown_or_verified_alias_binding_rules(session,legacy_binding,monkeypatch):
    case=legacy_binding
    old_site=Tenant(id='legacy-env-sharer',code='legacy-env-sharer',name='Existing shared merchant site',kind='native',status='active')
    session.add(old_site);session.flush()
    session.add(SiteMerchantBinding(tenant_id=old_site.id,account_code='env',explicit_reuse=True));session.commit()
    path='/platform/v1/stripe/accounts/alias'
    unknown=case['client'].patch(path,headers=case['platform_headers'],json={'is_default':True})
    assert unknown.status_code==409,unknown.text
    assert unknown.json()['detail']['code']=='merchant_identity_verification_required'
    session.expire_all()
    assert session.get(SiteMerchantBinding,'jjtv').account_code=='env'
    assert session.get(StripeAccount,'alias').is_default is False
    monkeypatch.setattr(commerce,'_stripe_identity',lambda secret:('acct_unit',False,{'id':'acct_unit','country':'US'}))
    assert case['client'].post('/platform/v1/stripe/accounts/env/test',headers=case['platform_headers']).status_code==200
    alias=case['client'].patch(path,headers=case['platform_headers'],json={'is_default':True})
    assert alias.status_code==409,alias.text
    assert 'explicitly' in alias.json()['detail']
    approved=case['client'].put('/tenant/v1/merchant-binding',headers=case['original_headers'],json={'account_code':'alias','allow_reuse':True})
    assert approved.status_code==200,approved.text
    saved=case['client'].patch(path,headers=case['platform_headers'],json={'is_default':True})
    assert saved.status_code==200,saved.text
    session.expire_all()
    assert session.get(StripeAccount,'alias').is_default is True
    assert session.get(SiteMerchantBinding,'jjtv').account_code=='alias'
