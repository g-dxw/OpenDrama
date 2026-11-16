"""New optional writes obey effective site capabilities; existing owned work can finish."""
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from types import SimpleNamespace

from app.main import app
from app.modules.account import service as accounts
from app.modules.account.models import User
from app.modules.attribution import service as attribution
from app.modules.attribution.models import AttributionEvent, AttributionPlan, AttributionSource, AttributionVisit, UserAttribution
from app.modules.sites.service import get_site
from app.modules.support.models import SupportTicket
from app.modules.tenancy.models import Tenant, TenantDomain, TenantModule
from app.modules.tenancy.permissions import seed_tenant_defaults


def disable(db, feature, mode):
    if mode == 'module':
        db.get(TenantModule, ('jjtv', feature)).enabled = False
    else:
        settings = get_site(db, 'jjtv')
        settings.features = {**(settings.features or {}), feature: False}
    db.commit()


def user_headers(user):
    return {'Authorization': 'Bearer ' + accounts._issue_session(user)}


@pytest.mark.parametrize('mode', ['feature', 'module'])
def test_disabled_support_blocks_new_tickets_but_owner_can_finish_existing(session, make, mode):
    client = TestClient(app)
    owner, other = make.user(), make.user('other-support-feature@example.test')
    headers = user_headers(owner)
    body = dict(subject='Existing help request', body='Please help', category='general')
    created = client.post('/v1/support/tickets', json=body, headers=headers)
    assert created.status_code == 201, created.text
    number = created.json()['ticket']['number']
    disable(session, 'support', mode)
    assert client.get('/v1/site/bootstrap').json()['features']['support'] is False
    denied = client.post('/v1/support/tickets', json=body, headers=headers)
    assert denied.status_code == 403 and denied.json()['detail']['code'] == 'feature_disabled'
    assert session.scalar(select(func.count()).select_from(SupportTicket)) == 1
    assert client.get('/v1/support/tickets', headers=headers).status_code == 200
    assert client.get('/v1/support/tickets/' + number, headers=headers).status_code == 200
    assert client.get('/v1/support/tickets/' + number, headers=user_headers(other)).status_code == 404
    reply = client.post('/v1/support/tickets/' + number + '/messages', json={'body': 'Here are the details'}, headers=headers)
    assert reply.status_code == 200, reply.text
    assert client.post('/v1/support/tickets/' + number + '/read', headers=headers).status_code == 200
    assert client.post('/v1/support/tickets/' + number + '/close', headers=user_headers(other)).status_code == 404
    assert client.post('/v1/support/tickets/' + number + '/close', headers=headers).status_code == 200
    assert client.post('/v1/support/tickets/' + number + '/csat', json={'score': 4}, headers=headers).status_code == 200
    assert client.post('/v1/support/tickets/' + number + '/messages', json={'body': 'Closed request'}, headers=headers).status_code == 409


@pytest.fixture()
def optional_campaign(session, monkeypatch):
    monkeypatch.setenv('SETTINGS_ENCRYPTION_KEY', Fernet.generate_key().decode())
    pixel = attribution.save_pixel(session, dict(name='Optional attribution fixture', platform='meta', pixel_id='123456789',
        token='test-only-pixel-token', enabled=True, delivery_mode='test', test_event_code='TEST-OPTIONAL'), 1)
    source = AttributionSource(tenant_id='jjtv', name='Feature source', pixel_id=pixel.id, enabled=True)
    session.add(source)
    session.flush()
    plan = AttributionPlan(tenant_id='jjtv', name='Feature plan', source_id=source.id, pixel_id=pixel.id,
        pid='optional-feature-pid', landing_path='/', enabled=True)
    session.add(plan)
    session.commit()
    return plan


@pytest.mark.parametrize('mode', ['feature', 'module'])
@pytest.mark.parametrize('endpoint', ['email/authenticate', 'email/register', 'google', 'facebook'])
def test_disabled_attribution_stops_visit_and_signup_events_without_breaking_auth(session, make, monkeypatch, optional_campaign, mode, endpoint):
    client = TestClient(app)
    plan = optional_campaign
    landed = client.post('/v1/attr/visit', json={'pid': plan.pid, 'client_id': 'existing-browser'})
    assert landed.status_code == 200 and landed.json()['active'] is True
    user = make.user('optional-' + endpoint.replace('/', '-') + '-' + mode + '@example.test')
    result = (user, 'valid-authentication-result', True)
    data = {'email': user.email, 'code': '123456'}
    if endpoint == 'email/authenticate':
        monkeypatch.setattr(accounts, 'login_or_register_with_code', lambda *args, **kwargs: result)
    elif endpoint == 'email/register':
        monkeypatch.setattr(accounts, 'register', lambda *args, **kwargs: result[:2])
    elif endpoint == 'google':
        monkeypatch.setattr(accounts, 'login_with_google', lambda *args, **kwargs: result)
        data = {'id_token': 'test-google-token'}
    else:
        monkeypatch.setattr(accounts, 'login_with_facebook', lambda *args, **kwargs: result)
        data = {'access_token': 'test-facebook-token'}
    disable(session, 'attribution', mode)
    assert client.get('/v1/site/bootstrap').json()['features']['attribution'] is False
    denied = client.post('/v1/attr/visit', json={'pid': plan.pid, 'client_id': 'new-browser'})
    assert denied.status_code == 403 and denied.json()['detail']['code'] == 'feature_disabled'
    response = client.post('/v1/auth/' + endpoint, json=data, headers={'X-JJTV-Attribution': plan.pid, 'X-Device-Id': 'existing-browser'})
    assert response.status_code == 200 and response.json()['token'] == 'valid-authentication-result'
    assert session.scalar(select(func.count()).select_from(AttributionVisit)) == 1
    assert session.scalar(select(func.count()).select_from(UserAttribution)) == 0
    assert session.scalar(select(func.count()).select_from(AttributionEvent)) == 0


@pytest.mark.parametrize('mode', ['feature', 'module'])
def test_disabled_attribution_does_not_block_existing_email_login(session, make, monkeypatch, optional_campaign, mode):
    user = make.user()
    monkeypatch.setattr(accounts, 'login_with_code', lambda *args, **kwargs: (user, 'existing-login-token'))
    disable(session, 'attribution', mode)
    response = TestClient(app).post('/v1/auth/email/login', json={'email': user.email, 'code': '123456'},
        headers={'X-JJTV-Attribution': optional_campaign.pid})
    assert response.status_code == 200 and response.json()['token'] == 'existing-login-token'
    assert session.scalar(select(func.count()).select_from(AttributionEvent)) == 0


def test_unsupported_site_cannot_reopen_legacy_support_or_attribution(session, monkeypatch, optional_campaign):
    site = Tenant(id='optional-other', code='optional-other', name='Other site', kind='native', status='active')
    session.add(site)
    session.flush()
    seed_tenant_defaults(session, site)
    settings = get_site(session, site.id)
    settings.features = {'support': True, 'attribution': True}
    # Even accidentally enabled rows cannot expand supported legacy capabilities.
    for key in ('support', 'attribution'):
        session.get(TenantModule, (site.id, key)).enabled = True
    from datetime import datetime, timezone
    session.add(TenantDomain(tenant_id=site.id, hostname='optional-other.example.test', kind='site', status='active', verified_at=datetime.now(timezone.utc)))
    user = User(tenant_id=site.id, email='other-optional@example.test', display_name='Other viewer')
    session.add(user)
    session.commit()
    client = TestClient(app, base_url='http://optional-other.example.test')
    config = client.get('/v1/site/bootstrap').json()
    assert config['features']['support'] is False and config['features']['attribution'] is False
    assert client.post('/v1/support/tickets', json={'subject': 'Denied', 'body': 'Denied'}, headers=user_headers(user)).status_code == 403
    assert client.post('/v1/attr/visit', json={'pid': optional_campaign.pid}).status_code == 403
    monkeypatch.setattr(accounts, 'login_or_register_with_code', lambda *args, **kwargs: (user, 'other-auth-token', True))
    response = client.post('/v1/auth/email/authenticate', json={'email': user.email, 'code': '123456'}, headers={'X-JJTV-Attribution': optional_campaign.pid})
    assert response.status_code == 200 and response.json()['token'] == 'other-auth-token'
    assert session.scalar(select(func.count()).select_from(AttributionVisit)) == 0
    assert session.scalar(select(func.count()).select_from(UserAttribution)) == 0
    assert session.scalar(select(func.count()).select_from(AttributionEvent)) == 0


def test_missing_site_context_never_falls_back_to_jjtv_collection():
    from app.modules.public_flow import _registration_attribution
    from app.modules.attribution.router import VisitIn, visit
    db = SimpleNamespace(info={})
    request = SimpleNamespace(headers={'X-JJTV-Attribution': 'some-pid'})
    _registration_attribution(db, request, user_id=1)
    with pytest.raises(HTTPException) as denied:
        visit(VisitIn(pid='some-pid'), request, db)
    assert denied.value.status_code == 403
