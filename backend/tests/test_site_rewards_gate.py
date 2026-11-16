"""Rewards are denied by module and storefront feature, including implicit credits."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.account import service as accounts
from app.modules.account.models import ThirdPartyIdentity
from app.modules.billing.commerce_models import CoinSource
from app.modules.ops.config_models import AdClaim, Checkin, TaskClaim
from app.modules.sites.models import SiteSettings
from app.modules.social.models import Reaction
from app.modules.tenancy.consumer_scope import site_context
from app.modules.tenancy.models import Tenant, TenantDomain, TenantModule
from app.modules.tenancy.permissions import seed_tenant_defaults
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount


@pytest.fixture(params=['module', 'feature'])
def rewards_site(request, session, make):
    site = Tenant(id='reward-site', code='reward-site', name='Reward site', kind='native', status='active')
    session.add(site); seed_tenant_defaults(session, site); session.flush()
    module = session.get(TenantModule, (site.id, 'rewards'))
    module.enabled = request.param != 'module'
    session.add(SiteSettings(tenant_id=site.id, timezone='UTC', features={'rewards': request.param != 'feature'}))
    session.add(TenantDomain(tenant_id=site.id, hostname='rewards.example', kind='site', status='active', verified_at=datetime.now(timezone.utc)))
    session.commit()
    original = make.user(email='rewards@example.com', coins=13)
    with site_context(session, site.id):
        user = make.user(email='rewards@example.com', coins=77)
        token = accounts._issue_session(user)
        for index in range(5):
            drama = make.drama(title=f'Reward progress {index}', status='published')
            session.add(Reaction(user_id=user.id, target_type='drama', target_id=drama.id, kind='like'))
        session.commit()
    return site, user, original, TestClient(app, base_url='https://rewards.example'), {'Authorization': 'Bearer '+token}


def assert_no_reward_writes(session, site, user, original):
    session.expire_all()
    with site_context(session, site.id):
        assert session.get(WalletAccount, user.id).balance == 77
        for model in [Checkin, AdClaim, TaskClaim, CoinLedgerEntry, CoinSource]:
            assert session.scalar(select(func.count()).select_from(model)) == 0
    with site_context(session, 'jjtv'):
        assert session.get(WalletAccount, original.id).balance == 13
        assert session.scalar(select(func.count()).select_from(CoinLedgerEntry)) == 0


@pytest.mark.parametrize('path', ['/v1/me/checkin', '/v1/me/ad-reward', '/v1/me/tasks/like_5/claim'])
def test_disabled_reward_mutations_deny_without_credits_but_user_reads_work(session, rewards_site, path):
    site, user, original, client, headers = rewards_site
    response = client.post(path, headers=headers)
    assert response.status_code == 403, response.text
    assert client.get('/v1/site/bootstrap').json()['features']['rewards'] is False
    for route in ['/v1/me', '/v1/me/ledger', '/v1/me/tasks', '/v1/tasks']:
        read = client.get(route, headers=headers)
        assert read.status_code == 200, read.text
    assert_no_reward_writes(session, site, user, original)


def test_disabled_rewards_allow_registration_and_binding_without_reward(session, rewards_site, monkeypatch):
    site, user, original, client, _headers = rewards_site
    outbox = []
    monkeypatch.setattr(accounts, 'send_verification_code', lambda **value: outbox.append(value) or 'message')
    sent = client.post('/v1/auth/email/code', json={'email': 'new-reward@example.com'})
    assert sent.status_code == 200, sent.text
    response = client.post('/v1/auth/email/authenticate', json={'email': 'new-reward@example.com', 'code': outbox[0]['code']})
    assert response.status_code == 200 and response.json()['created'], response.text
    headers = {'Authorization': 'Bearer '+response.json()['token']}
    profile = client.get('/v1/me', headers=headers)
    assert profile.status_code == 200 and profile.json()['coins'] == 0, profile.text
    with site_context(session, site.id):
        assert accounts.bind_provider(session, user, provider='facebook', provider_user_id='reward-facebook') == {'bound': True, 'rewarded': False}
        assert session.scalar(select(ThirdPartyIdentity).where(ThirdPartyIdentity.user_id == user.id)).provider_user_id == 'reward-facebook'
    assert_no_reward_writes(session, site, user, original)


def test_reenabled_rewards_pay_existing_claims_once_and_other_site_is_independent(session, rewards_site):
    site, user, original, client, headers = rewards_site
    # Restoring both switches enables real configured/default rewards. A retry
    # after disabling must still be rejected rather than bypassing via replay.
    session.get(TenantModule, (site.id, 'rewards')).enabled = True
    session.get(SiteSettings, site.id).features = {'rewards': True}
    session.commit()
    checkin = client.post('/v1/me/checkin', headers=headers)
    ad = client.post('/v1/me/ad-reward', headers=headers)
    task = client.post('/v1/me/tasks/like_5/claim', headers=headers)
    assert checkin.status_code == ad.status_code == task.status_code == 200
    assert checkin.json()['amount'] == 30 and ad.json()['amount'] == 30 and task.json()['amount'] == 20
    assert client.post('/v1/me/checkin', headers=headers).json()['replayed'] is True
    assert client.post('/v1/me/ad-reward', headers=headers).status_code == 409
    assert client.post('/v1/me/tasks/like_5/claim', headers=headers).json()['replayed'] is True
    session.get(SiteSettings, site.id).features = {'rewards': False}; session.commit()
    for route in ['/v1/me/checkin', '/v1/me/ad-reward', '/v1/me/tasks/like_5/claim']:
        assert client.post(route, headers=headers).status_code == 403
    session.expire_all()
    with site_context(session, site.id):
        assert session.get(WalletAccount, user.id).balance == 157
        assert session.scalar(select(func.count()).select_from(CoinLedgerEntry)) == 3
    original_client = TestClient(app)
    result = original_client.post('/v1/me/checkin', headers={'Authorization': 'Bearer '+accounts._issue_session(original)})
    assert result.status_code == 200 and result.json()['amount'] == 30, result.text
    with site_context(session, 'jjtv'):
        assert session.get(WalletAccount, original.id).balance == 43
