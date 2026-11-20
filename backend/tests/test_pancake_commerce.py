"""Waffo Pancake orders, currency proof and scope with authenticated I/O mocked."""

import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.account import service as accounts
from app.modules.billing import commerce, gateway_money, waffo_pancake as pancake
from app.modules.billing.models import Order
from app.modules.wallet.models import WalletAccount
from tests.test_epay_commerce import staff_headers

ACCOUNTS = '/platform/v1/waffo-pancake/accounts'
BINDING = '/tenant/v1/waffo-pancake/merchant-binding'
PRODUCTS = '/tenant/v1/waffo-pancake/product-bindings'
WEBHOOK = '/v1/billing/waffo-pancake/accounts/pancake_store/webhook'
STORE = 'STO_JJTVSandbox'
PRODUCT = 'PROD_SmallPack'
SECRET = 'test-only-rsa-private-key'


@pytest.fixture
def merchant(session, make, monkeypatch):
    monkeypatch.setenv('APP_ENV', 'local')
    monkeypatch.setenv('PAYMENT_GATEWAY_TEST_MODE', '1')
    monkeypatch.setenv('SETTINGS_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('STRIPE_SECRET_KEY', '')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', '')
    monkeypatch.setattr(pancake, 'list_stores', lambda **_: [
        {'id': STORE, 'name': 'Sandbox Short Drama', 'status': 'active', 'prod_enabled': False},
    ])
    prices = [{'currency': 'USD', 'amount': '5.00', 'tax_category': 'digital_goods'}]
    monkeypatch.setattr(pancake, 'get_catalog', lambda **_: [
        {'id': PRODUCT, 'name': 'Gold pack', 'status': 'active', 'billing_mode': 'one_time', 'prices': prices},
    ])
    captured = []

    def create_checkout(**payload):
        captured.append(payload)
        return {'session_id': 'SES_Unit', 'checkout_url': 'https://checkout.waffo.ai/SES_Unit', 'expires_at': None}

    def verify_webhook(body, signature, mode, store_id):
        event = json.loads(body)
        if signature != 'verified-in-adapter-test' or mode != 'test' or event.get('mode') != 'test' or event.get('storeId') != store_id:
            raise pancake.PancakeError('bad notification')
        return event

    monkeypatch.setattr(pancake, 'create_checkout', create_checkout)
    monkeypatch.setattr(pancake, 'verify_webhook', verify_webhook)
    client = TestClient(app)
    owner = make.admin(role='admin')
    buyer, other = make.user('pancake-buyer@example.com'), make.user('pancake-other@example.com')
    admin, platform = staff_headers(client, owner, 'jjtv'), staff_headers(client, make.platform_admin())
    viewers = [{'Authorization': 'Bearer ' + accounts._issue_session(viewer)} for viewer in (buyer, other)]
    response = client.post(ACCOUNTS, headers=platform, json={
        'code': 'pancake_store', 'name': 'Pancake', 'status': 'active', 'mode': 'test',
        'merchant_id': 'MER_JJTVSandbox', 'store_id': STORE, 'private_key': SECRET,
    })
    assert response.status_code == 200, response.text
    assert SECRET not in response.text
    assert client.put(BINDING, headers=admin, json={'account_code': 'pancake_store'}).json()['configured']
    commerce.save_pack(session, 'pancake_pack', dict(name_en='Gold', price_cents=500, base_coins=25), owner.id)
    item = next(row for row in client.get(PRODUCTS, headers=admin).json()['items'] if row['product_type'] == 'coin_pack')
    response = client.put(PRODUCTS, headers=admin, json={
        'account_code': 'pancake_store', 'product_type': 'coin_pack', 'version_id': item['version_id'],
        'billing_mode': 'one_time', 'external_product_id': PRODUCT,
    })
    assert response.status_code == 200, response.text
    return client, buyer, owner, viewers, captured


def checkout(client, viewer, key='pancake-purchase-key'):
    return client.post('/v1/commerce/checkout', headers=viewer, json={
        'product_type': 'coin_pack', 'code': 'pancake_pack', 'idempotency_key': key,
        'provider': 'waffo_pancake', 'account_code': 'pancake_store',
    })


def signed_event(client, number, *, store=STORE, proof='PAY_Unit', kind='order.completed', extra=None):
    body = {'eventType': kind, 'storeId': store, 'mode': 'test', 'data': {
        'orderId': 'ORD_Unit', 'orderMerchantExternalId': number, 'paymentId': proof, 'paymentStatus': 'succeeded',
        **(extra or {}),
    }}
    return client.post(WEBHOOK, json=body, headers={'X-Waffo-Signature': 'verified-in-adapter-test'})


def verified_order_and_payment(monkeypatch, number):
    order = {
        'id': 'ORD_Unit', 'status': 'completed', 'storeId': STORE, 'orderMerchantExternalId': number,
        'onetimeProduct': {'id': PRODUCT}, 'payments': [{'id': 'PAY_Unit', 'status': 'succeeded'}],
    }
    payment = {
        'id': 'PAY_Unit', 'orderId': order['id'], 'status': 'succeeded', 'orderMerchantExternalId': number,
        'snapshotAmountDetails': {'total': '5.00', 'taxAmount': '0.30', 'currency': 'USD', 'discountAmount': '0.00'},
        'amount': {'display': '5.00', 'currency': 'USD'}, 'refundStatus': None,
    }
    monkeypatch.setattr(pancake, 'get_order', lambda **_: dict(order))
    monkeypatch.setattr(pancake, 'get_payment', lambda **_: dict(payment))
    return order, payment


def test_pancake_signed_store_notification_uses_authenticated_frozen_payment(merchant, session, monkeypatch):
    client, buyer, owner, (viewer, _), captured = merchant
    assert client.get('/v1/billing/channels').json()['items'][0]['provider'] == 'waffo_pancake'
    response = checkout(client, viewer)
    assert response.status_code == 200, response.text
    result = response.json()
    assert checkout(client, viewer).json() == {**result, 'reused': True}
    assert len(captured) == 1 and captured[0]['amount'] == '5.00'
    number = result['order_no']
    assert 'payment_provider=waffo_pancake' in captured[0]['success_url'] and f'order_no={number}' in captured[0]['success_url']
    order, payment = verified_order_and_payment(monkeypatch, number)
    commerce.save_pack(session, 'pancake_pack', dict(price_cents=800, base_coins=99), owner.id)
    assert client.get('/v1/billing/channels').json()['items'] == []
    assert signed_event(client, number, store='STO_Other').status_code == 401
    payment['amount'] = {'display': '4.99', 'currency': 'USD'}
    assert signed_event(client, number).status_code == 409
    payment['amount']['display'] = '5.00'
    order['onetimeProduct'] = {'id': 'PROD_Incorrect'}
    assert signed_event(client, number).status_code == 409
    order['onetimeProduct']['id'] = PRODUCT
    assert signed_event(client, number).status_code == 200
    assert signed_event(client, number).json()['replayed']
    session.expire_all()
    assert session.get(WalletAccount, buyer.id).balance == 25
    row = session.scalar(select(Order).where(Order.order_no == number))
    assert row.checkout_context['provider_trade_no'] == 'PAY_Unit'
    assert row.checkout_context['provider_tax_minor'] == 30


def test_pancake_order_lookup_confirms_missed_callback_only_for_owner(merchant, monkeypatch):
    client, _, _, (viewer, other), _ = merchant
    result = checkout(client, viewer).json()
    verified_order_and_payment(monkeypatch, result['order_no'])
    path = '/v1/commerce/orders/' + result['order_no']
    assert client.post(path + '/reconcile', headers=other).status_code == 404
    assert client.get(path, headers=viewer).json()['settled'] is False
    assert client.post(path + '/reconcile', headers=viewer).json()['settled'] is True


def test_pancake_refund_runs_once_and_needs_matching_executed_amount(merchant, session, monkeypatch):
    client, buyer, owner, (viewer, _), _captured = merchant
    number = checkout(client, viewer).json()['order_no']
    verified_order_and_payment(monkeypatch, number)
    assert signed_event(client, number).status_code == 200
    submitted = []
    monkeypatch.setattr(pancake, 'create_refund_ticket', lambda **payload: submitted.append(payload) or {'status': 'pending'})
    monkeypatch.setattr(pancake, 'get_refund', lambda **_: None)
    monkeypatch.setattr(pancake, 'get_refund_ticket', lambda **_: {'id': 'TIC_Unit', 'status': 'pending'})
    def request():
        return commerce.request_refund(session, number, owner.id, 'Customer request', 'refund-pancake-unit')
    assert request()['status'] == 'pending'
    assert request()['status'] == 'pending'
    assert len(submitted) == 1
    assert submitted[0]['refund_no'] == 'jjtv-refund-' + number
    assert session.get(WalletAccount, buyer.id).balance == 0
    monkeypatch.setattr(pancake, 'get_refund', lambda **_: {
        'status': 'succeeded', 'amount': {'display': '5.00', 'currency': 'USD'}, 'isAmountMismatch': True,
    })
    assert request()['status'] == 'pending'
    monkeypatch.setattr(pancake, 'get_refund', lambda **_: None)
    callback = {'currency': 'USD', 'refundStatus': 'succeeded',
                'refundTicketMerchantExternalId': 'jjtv-refund-' + number, 'refundedAmount': '4.99'}
    assert signed_event(client, number, kind='refund.succeeded', extra=callback).status_code == 200
    assert request()['status'] == 'pending'
    assert signed_event(client, number, kind='refund.succeeded', extra={**callback, 'refundedAmount': '5.00'}).status_code == 200
    session.expire_all()
    assert request()['status'] == 'succeeded'
    assert session.get(WalletAccount, buyer.id).balance == 0


@pytest.mark.parametrize(('currency', 'minor', 'display'), [('JPY', 500, '500'), ('KWD', 500, '0.500'), ('USD', 500, '5.00')])
def test_gateway_amount_uses_storefront_currency_precision(currency, minor, display):
    assert gateway_money.as_major(minor, currency) == display
    assert gateway_money.as_minor(display, currency) == minor
    with pytest.raises(ValueError):
        gateway_money.as_minor(display + ('.' if '.' not in display else '') + '0001', currency)
