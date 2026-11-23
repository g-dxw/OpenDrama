"""Exercise Epay checkout and fulfillment against real PostgreSQL."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.account import service as account_service
from app.modules.billing import commerce, epay, service as billing
from app.modules.billing.commerce_models import CommerceSettlement, GatewayAccount, MembershipGrant
from app.modules.billing.models import Order, Plan, PlanVersion
from app.modules.sites.models import SiteSettings
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.permissions import seed_tenant_defaults
from app.modules.wallet.models import WalletAccount


SECRET = 'test-secret-for-epay'
ACCOUNT = 'cashier'
ACCOUNT_PATH = '/platform/v1/epay/accounts'
BINDING_PATH = '/tenant/v1/epay/merchant-binding'
CALLBACK = '/v1/billing/epay/accounts/cashier/webhook'


def staff_headers(client, user, tenant=None):
    body = {'username': user.username, 'password': 'pass-1234'}
    if tenant:
        body['tenant_code'] = tenant
    path = '/tenant/v1/auth/login' if tenant else '/platform/v1/auth/login'
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['token']}


@pytest.fixture
def merchant(session, make, monkeypatch):
    monkeypatch.setenv('SETTINGS_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('PAYMENT_WEBHOOK_BASE_URL', 'https://api.example.test')
    monkeypatch.setenv('STRIPE_SECRET_KEY', '')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', '')
    monkeypatch.setattr(epay, 'verify_merchant', lambda **kwargs: kwargs['secret'] == SECRET)
    session.merge(SiteSettings(tenant_id='jjtv', currency='CNY'))
    owner = make.admin(role='admin')
    buyer = make.user('epay-buyer@example.com')
    other = make.user('epay-other@example.com')
    platform = make.platform_admin()
    client = TestClient(app)
    admin_headers = staff_headers(client, owner, 'jjtv')
    platform_headers = staff_headers(client, platform)
    buyer_headers = {'Authorization': 'Bearer ' + account_service._issue_session(buyer)}
    other_headers = {'Authorization': 'Bearer ' + account_service._issue_session(other)}
    config = {
        'code': ACCOUNT, 'name': 'Epay Merchant', 'status': 'active',
        'endpoint': 'https://example.test/checkout/', 'merchant_id': '12345',
        'payment_methods': ['alipay', 'wxpay'], 'secret_key': SECRET,
    }
    assert client.post(ACCOUNT_PATH, headers=admin_headers, json=config).status_code == 403
    response = client.post(ACCOUNT_PATH, headers=platform_headers, json=config)
    assert response.status_code == 200, response.text
    assert SECRET not in response.text
    assert response.json()['notify_url'] == 'https://api.example.test' + CALLBACK
    row = session.get(GatewayAccount, ('epay', ACCOUNT))
    assert row and row.credentials['secret_key'] != SECRET
    assert SECRET not in str(row.config)
    response = client.put(BINDING_PATH, headers=admin_headers, json={'account_code': ACCOUNT})
    assert response.status_code == 200, response.text
    assert response.json()['configured']
    commerce.save_pack(session, 'starter', dict(name_en='Starter', price_cents=500, base_coins=40, bonus_coins=2), owner.id)
    return client, buyer, buyer_headers, other_headers, platform_headers, admin_headers


def purchase(client, headers, *, key='payment-attempt', method='alipay', kind='coin_pack', code='starter', **updates):
    payload = {
        'provider': 'epay', 'product_type': kind, 'code': code, 'account_code': ACCOUNT,
        'payment_method': method, 'idempotency_key': key, **updates,
    }
    return client.post('/v1/commerce/checkout', headers=headers, json=payload)


def notification(order_no, *, trade='trade_001', method='alipay', money='5.00', pid='12345', status='TRADE_SUCCESS'):
    args = {
        'pid': pid, 'out_trade_no': order_no, 'trade_no': trade, 'money': money,
        'type': method, 'name': 'Online Payment', 'trade_status': status,
    }
    canonical = '&'.join(f'{key}={value}' for key, value in sorted(args.items()))
    return {**args, 'sign': hashlib.md5((canonical + SECRET).encode()).hexdigest(), 'sign_type': 'MD5'}


def test_signed_callback_is_idempotent_and_pinned_to_frozen_coin_pack(merchant, session):
    client, buyer, headers, _other, platform, _admin = merchant
    assert {x['method'] for x in client.get('/v1/billing/channels').json()['items']} == {'alipay', 'wxpay'}
    first = purchase(client, headers)
    assert first.status_code == 200, first.text
    result = first.json()
    assert purchase(client, headers).json() == {**result, 'reused': True}
    assert purchase(client, headers, method='wxpay').status_code == 409
    params = parse_qs(urlsplit(result['url']).query)
    assert params['money'] == ['5.00']
    assert parse_qs(urlsplit(params['return_url'][0]).query)['order_no'] == [result['order_no']]
    assert SECRET not in result['url']
    assert client.patch(f'{ACCOUNT_PATH}/{ACCOUNT}', headers=platform, json={'secret_key': 'new-secret'}).status_code == 409
    assert client.patch(f'{ACCOUNT_PATH}/{ACCOUNT}', headers=platform, json={'status': 'disabled'}).status_code == 200
    assert client.get('/v1/billing/channels').json() == {'items': []}
    assert purchase(client, headers, key='new-payment-after-disable').status_code == 403
    pack = commerce.list_packs(session)[0]
    commerce.save_pack(session, pack['code'], dict(price_cents=900, base_coins=999), None)

    number = result['order_no']
    assert client.get(CALLBACK, params=notification(number, money='9.00')).status_code == 409
    mismatched = notification(number, method='wxpay')
    assert client.get(CALLBACK, params=mismatched).status_code == 409
    invalid = {**notification(number), 'money': '9.00'}
    assert client.get(CALLBACK, params=invalid).status_code == 401
    assert client.get(CALLBACK, params=[*notification(number).items(), ('money', '5.00')]).status_code == 400
    assert client.get(CALLBACK, params=notification(number, status='WAIT_BUYER_PAY')).text == 'success'
    assert client.get('/v1/commerce/orders/' + number, headers=headers).json()['settled'] is False

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.post(CALLBACK, data=notification(number)), range(2)))
    assert [r.text for r in responses] == ['success', 'success']
    assert client.get('/v1/commerce/orders/' + number, headers=headers).json()['settled'] is True
    again = purchase(client, headers)
    assert again.status_code == 409
    assert again.json()['detail']['code'] == 'checkout_already_paid'
    assert again.json()['detail']['order_no'] == number
    session.expire_all()
    assert session.get(WalletAccount, buyer.id).balance == 42
    assert session.scalar(select(func.count()).select_from(CommerceSettlement)) == 1


def test_reconcile_can_recover_one_time_membership_without_callback(merchant, session, monkeypatch):
    client, buyer, headers, other, _platform, _admin = merchant
    plan = Plan(code='weekly', name_en='Weekly VIP')
    session.add(plan)
    session.flush()
    session.add(PlanVersion(plan_id=plan.id, version=1, price_cents=500, currency='CNY', benefits={}))
    billing.set_billing_mode(session, 'subscription', None)
    assert purchase(client, headers, kind='membership', code=plan.code).status_code == 409
    billing.set_billing_mode(session, 'one_time', None)
    result = purchase(client, headers, kind='membership', code=plan.code)
    assert result.status_code == 200, result.text
    number = result.json()['order_no']
    called = []

    def query_order(**facts):
        called.append(facts['order_no'])
        return epay.EpayPayment(number, 'recovered_trade', 'alipay', 500, True)

    monkeypatch.setattr(epay, 'query_order', query_order)
    status_path = '/v1/commerce/orders/' + number
    assert client.get(status_path, headers=headers).json()['settled'] is False
    assert client.post(status_path + '/reconcile', headers=other).status_code == 404
    assert not called
    for _ in range(2):
        response = client.post(status_path + '/reconcile', headers=headers)
        assert response.status_code == 200 and response.json()['settled'], response.text
    assert called == [number]
    assert client.get(CALLBACK, params=notification(number, trade='recovered_trade')).text == 'success'
    session.expire_all()
    order = session.scalar(select(Order).where(Order.order_no == number))
    grant = session.scalar(select(MembershipGrant).where(MembershipGrant.order_id == order.id))
    assert grant.user_id == buyer.id and (grant.ends_at - grant.starts_at).days == 7
    assert not commerce.refund_eligibility(session, order)['eligible']


def test_bindings_cannot_share_epay_merchant_across_tenants(merchant, session, make):
    client, _buyer, _headers, _other, _platform, _admin = merchant
    site = Tenant(id='other-epay', code='other-epay', name='Other store', kind='native', status='active')
    session.add(site)
    seed_tenant_defaults(session, site)
    session.add(SiteSettings(tenant_id=site.id, currency='CNY'))
    session.commit()
    owner = make.admin(role='admin', tenant_id=site.id)
    other = staff_headers(client, owner, site.code)
    assert client.get(BINDING_PATH, headers=other).json()['available_accounts'] == []
    response = client.put(BINDING_PATH, headers=other, json={'account_code': ACCOUNT})
    assert response.status_code == 409 and 'another site' in response.text
