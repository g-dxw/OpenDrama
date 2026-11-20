"""Waffo acquiring checkout with isolated ledger and mocked provider calls."""

import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.account import service as accounts
from app.modules.billing import commerce, service as billing, waffo, waffo_accounts
from app.modules.billing.commerce_models import CommerceRefund
from app.modules.billing.models import Order, Plan, PlanVersion
from app.modules.wallet.models import WalletAccount
from tests.test_epay_commerce import staff_headers

ACCOUNTS = '/platform/v1/waffo/accounts'
BINDING = '/tenant/v1/waffo/merchant-binding'
WEBHOOK = '/v1/billing/waffo/accounts/waffo_store/webhook'
MERCHANT = '204800'
PRIVATE_KEY = 'test-private-key'
PUBLIC_KEY = 'test-public-key'
API_KEY = 'test-api-key'


@pytest.fixture
def merchant(session, make, monkeypatch):
    monkeypatch.setenv('APP_ENV', 'local')
    monkeypatch.setenv('PAYMENT_GATEWAY_TEST_MODE', '1')
    monkeypatch.setenv('SETTINGS_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('STRIPE_SECRET_KEY', '')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', '')
    monkeypatch.setattr(waffo, 'verify_merchant',
                        lambda **auth: (auth['merchant_id'], auth['mode'], auth['api_key']) == (MERCHANT, 'test', API_KEY))
    sent = []

    def create_checkout(**payload):
        sent.append(payload)
        return {'acquiringOrderId': 'acq_checkout', 'checkout_url': 'https://cashier.waffo.com/c/test_checkout'}

    def verify(body, signature, key):
        if signature != 'verified-in-adapter' or key != PUBLIC_KEY:
            raise waffo.WaffoVerificationError()
        return json.loads(body)

    monkeypatch.setattr(waffo, 'create_checkout', create_checkout)
    monkeypatch.setattr(waffo, 'verify_webhook', verify)
    monkeypatch.setattr(waffo, 'acknowledgment', lambda key: (b'{"message":"success"}', 'test-merchant-signature'))
    client = TestClient(app)
    owner = make.admin(role='admin')
    admin, platform = staff_headers(client, owner, 'jjtv'), staff_headers(client, make.platform_admin())
    buyer, other = make.user('waffo-buyer@example.com'), make.user('waffo-other@example.com')
    viewers = [{'Authorization': 'Bearer ' + accounts._issue_session(user)} for user in (buyer, other)]
    data = {
        'code': 'waffo_store', 'name': 'Waffo acquiring', 'mode': 'test', 'status': 'active',
        'currency': 'USD', 'merchant_id': MERCHANT, 'api_key': API_KEY, 'private_key': PRIVATE_KEY, 'public_key': PUBLIC_KEY,
    }
    assert client.post(ACCOUNTS, headers=admin, json=data).status_code == 403
    response = client.post(ACCOUNTS, headers=platform, json=data)
    assert response.status_code == 200, response.text
    assert not any(phrase in response.text for phrase in (API_KEY, PRIVATE_KEY, PUBLIC_KEY))
    assert response.json()['subscriptions_enabled'] is False
    assert client.put(BINDING, headers=admin, json={'account_code': data['code']}).json()['configured']
    commerce.save_pack(session, 'waffo_coins', dict(name_en='Coin pack W', price_cents=777, base_coins=30), owner.id)
    return client, owner, buyer, viewers, sent


def checkout(client, viewer):
    return client.post('/v1/commerce/checkout', headers=viewer, json={
        'provider': 'waffo', 'account_code': 'waffo_store', 'product_type': 'coin_pack',
        'code': 'waffo_coins', 'idempotency_key': 'waffo-checkout-fixed',
    })


def result(number, **overrides):
    return {
        'merchantOrderId': number, 'paymentRequestId': waffo.request_reference(number),
        'acquiringOrderId': 'acq_checkout', 'orderStatus': 'PAY_SUCCESS',
        'orderCurrency': 'USD', 'orderAmount': '7.77', 'merchantInfo': {'merchantId': int(MERCHANT)}, **overrides,
    }


def webhook(client, data, *, event='PAYMENT_NOTIFICATION', signature='verified-in-adapter'):
    return client.post(WEBHOOK, json={'eventType': event, 'result': data}, headers={'X-SIGNATURE': signature})


def test_waffo_signed_payment_needs_original_merchant_amount_and_reference(merchant, session):
    client, owner, buyer, (viewer, _), sent = merchant
    assert client.get('/v1/billing/channels').json()['items'][0]['billing_modes'] == ['one_time']
    first = checkout(client, viewer)
    assert first.status_code == 200, first.text
    pay = first.json()
    assert checkout(client, viewer).json() == {**pay, 'reused': True}
    assert len(sent) == 1 and sent[0]['amount'] == '7.77'
    number = pay['order_no']
    assert 'payment_provider=waffo' in sent[0]['success_url'] and f'order_no={number}' in sent[0]['success_url']
    commerce.save_pack(session, 'waffo_coins', dict(price_cents=800, base_coins=99), owner.id)
    assert webhook(client, result(number), signature='tampered').status_code == 401
    assert webhook(client, result(number, orderAmount='7.76')).status_code == 409
    assert webhook(client, result(number, orderCurrency='INR')).status_code == 409
    assert webhook(client, result(number, acquiringOrderId='wrong_acquisition')).status_code == 409
    assert webhook(client, result(number, merchantInfo={'merchantId': 'wrong-merchant'})).status_code == 409
    assert webhook(client, result(number, orderStatus='PAY_IN_PROGRESS')).status_code == 200
    path = '/v1/commerce/orders/' + number
    assert not client.get(path, headers=viewer).json()['settled']
    for _ in range(2):
        signed = webhook(client, result(number))
        assert signed.status_code == 200 and signed.content == b'{"message":"success"}', signed.text
        assert signed.headers['X-SIGNATURE'] == 'test-merchant-signature'
    assert client.get(path, headers=viewer).json()['settled']
    session.expire_all()
    assert session.get(WalletAccount, buyer.id).balance == 30
    row = session.scalar(select(Order).where(Order.order_no == number))
    assert row.checkout_context['provider_trade_no'] == 'acq_checkout'


def test_waffo_payment_return_reconciles_remote_evidence_for_owner(merchant, monkeypatch):
    client, _, _, (viewer, other), _sent = merchant
    number = checkout(client, viewer).json()['order_no']
    path = '/v1/commerce/orders/' + number
    calls = []
    monkeypatch.setattr(waffo, 'get_order', lambda **fields: calls.append(fields['order_no']) or result(number))
    assert client.post(path + '/reconcile', headers=other).status_code == 404
    assert not calls
    assert client.post(path + '/reconcile', headers=viewer).json()['settled']
    assert calls == [number]


def test_waffo_refund_submits_once_and_ignores_incomplete_payment_proof(merchant, session, monkeypatch):
    client, owner, buyer, (viewer, _), _sent = merchant
    number = checkout(client, viewer).json()['order_no']
    assert webhook(client, result(number)).status_code == 200
    monkeypatch.setattr(waffo, 'get_order', lambda **_: result(number))
    submitted = []
    monkeypatch.setattr(waffo, 'create_refund', lambda **payload: submitted.append(payload) or {})
    monkeypatch.setattr(waffo, 'get_refund', lambda **_: None)

    def request():
        return commerce.request_refund(session, number, owner.id, 'Customer request', 'refund-fixture')

    assert request()['status'] == 'pending'
    assert request()['status'] == 'pending'
    assert len(submitted) == 1 and submitted[0]['amount'] == '7.77'
    assert session.get(WalletAccount, buyer.id).balance == 0
    ref = 'jjtv-refund-' + number
    proof = {'merchantRefundOrderId': ref, 'refundRequestId': waffo.request_reference(ref),
             'acquiringOrderId': 'acq_checkout', 'acquiringRefundOrderId': 'waffo_refund',
             'refundAmount': '7.77', 'refundStatus': 'REFUND_IN_PROGRESS'}
    assert webhook(client, proof, event='REFUND_NOTIFICATION').status_code == 200
    assert request()['status'] == 'pending'
    assert webhook(client, {**proof, 'refundStatus': 'ORDER_FULLY_REFUNDED'}, event='REFUND_NOTIFICATION').status_code == 200
    session.expire_all()
    assert request()['status'] == 'succeeded'
    assert session.get(WalletAccount, buyer.id).balance == 0


def test_waffo_early_chargeback_fences_late_paid_notification(merchant, session):
    client, _owner, buyer, (viewer, _), _sent = merchant
    number = checkout(client, viewer).json()['order_no']
    chargeback = {
        'originalOrderId': 'acq_checkout', 'originalPaymentRequestId': waffo.request_reference(number),
        'merchantOrderId': number, 'merchantId': MERCHANT, 'chargebackStatus': 'CHARGEBACK',
    }
    assert webhook(client, {**chargeback, 'merchantId': 'wrong'}, event='CHARGEBACK_NOTIFICATION').status_code == 409
    response = webhook(client, chargeback, event='CHARGEBACK_NOTIFICATION')
    assert response.status_code == 200, response.text
    assert webhook(client, result(number)).status_code == 200
    session.expire_all()
    row = session.scalar(select(Order).where(Order.order_no == number))
    assert row.status == 'refund_review'
    assert session.scalar(select(CommerceRefund.status).where(CommerceRefund.order_id == row.id)) == 'manual_review'
    assert session.get(WalletAccount, buyer.id).balance == 0


def test_waffo_recurring_uses_explicit_merchant_enablement_and_frozen_cadence(merchant, session, monkeypatch):
    client, owner, _buyer, (viewer, _), _sent = merchant
    plan = Plan(code='weekly', name_en='Weekly membership')
    session.add(plan)
    session.flush()
    session.add(PlanVersion(plan_id=plan.id, version=1, price_cents=799, currency='USD', benefits={}))
    billing.set_billing_mode(session, 'subscription', owner.id)
    def subscribe():
        return client.post('/v1/commerce/checkout', headers=viewer, json={
            'provider': 'waffo', 'account_code': 'waffo_store', 'product_type': 'membership',
            'code': 'weekly', 'idempotency_key': 'waffo-subscription-fixture',
        })

    assert subscribe().status_code == 409
    waffo_accounts.save_account(session, 'waffo_store', {'subscriptions_enabled': True}, owner.id)
    modes = client.get('/v1/billing/channels').json()['items'][0]['billing_modes']
    assert modes == ['one_time', 'subscription']
    recorded = []
    monkeypatch.setattr(waffo, 'create_subscription', lambda **fields: recorded.append(fields) or {
        'subscriptionId': 'sub_checkout', 'checkout_url': 'https://cashier.waffo.com/c/sub_checkout',
    })
    response = subscribe()
    assert response.status_code == 200, response.text
    assert response.json()['session_id'] == 'sub_checkout'
    assert recorded[0]['interval'] == 'week' and recorded[0]['amount'] == '7.99'
    assert recorded[0]['management_url'].endswith('#account/orders')
