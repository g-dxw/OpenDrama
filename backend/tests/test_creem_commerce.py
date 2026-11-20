"""Creem account, catalog and one-time checkout with mocked provider I/O."""

import hashlib
import hmac
import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.account import service as accounts
from app.modules.billing import commerce, creem
from app.modules.billing.commerce_models import CommerceRefund, GatewayAccount
from app.modules.billing.models import Order
from app.modules.wallet.models import WalletAccount
from tests.test_epay_commerce import staff_headers

ACCOUNT_PATH = '/platform/v1/creem/accounts'
BIND_PATH = '/tenant/v1/creem/merchant-binding'
CATALOG_PATH = '/tenant/v1/creem/product-bindings'
WEBHOOK = '/v1/billing/creem/accounts/creem_store/webhook'
API_KEY = 'creem_test_local_key'
WEBHOOK_SECRET = 'local-creem-webhook-secret'


@pytest.fixture
def store(session, make, monkeypatch):
    monkeypatch.setenv('APP_ENV', 'local')
    monkeypatch.setenv('PAYMENT_GATEWAY_TEST_MODE', '1')
    monkeypatch.setenv('SETTINGS_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('STRIPE_SECRET_KEY', '')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', '')
    monkeypatch.setattr(creem, 'verify_merchant', lambda api_key, mode: api_key == API_KEY and mode == 'test')
    products = {
        'prod_pack': dict(id='prod_pack', status='active', price=500, currency='USD', billing_type='onetime', mode='test'),
        'prod_wrong': dict(id='prod_wrong', status='active', price=900, currency='USD', billing_type='onetime', mode='test'),
    }
    monkeypatch.setattr(creem, 'get_product', lambda product_id, **_: products[product_id])
    checkouts = []

    def create_checkout(**call):
        reference = f'ch_pack_{len(checkouts)}'
        checkouts.append(call)
        return {'id': reference, 'checkout_url': f'https://checkout.creem.io/{reference}'}

    monkeypatch.setattr(creem, 'create_checkout', create_checkout)
    client = TestClient(app)
    owner = make.admin(role='admin')
    buyer = make.user('creem-buyer@example.com')
    other = make.user('creem-other@example.com')
    admin = staff_headers(client, owner, 'jjtv')
    platform = staff_headers(client, make.platform_admin())
    viewers = [{'Authorization': 'Bearer ' + accounts._issue_session(viewer)} for viewer in (buyer, other)]
    merchant = {
        'code': 'creem_store', 'name': 'Creem store', 'mode': 'test', 'status': 'active',
        'api_key': API_KEY, 'webhook_secret': WEBHOOK_SECRET,
    }
    response = client.post(ACCOUNT_PATH, headers=platform, json=merchant)
    assert response.status_code == 200, response.text
    assert API_KEY not in response.text and WEBHOOK_SECRET not in response.text
    row = session.get(GatewayAccount, ('creem', 'creem_store'))
    assert row.credentials['webhook_secret'] != WEBHOOK_SECRET
    assert client.put(BIND_PATH, headers=admin, json={'account_code': 'creem_store'}).json()['configured']
    commerce.save_pack(session, 'starter', dict(name_en='Starter', price_cents=500, base_coins=48, bonus_coins=2), owner.id)
    mapping = next(row for row in client.get(CATALOG_PATH, headers=admin).json()['items'] if row['product_type'] == 'coin_pack')
    write = {
        'account_code': 'creem_store', 'product_type': 'coin_pack',
        'version_id': mapping['version_id'], 'billing_mode': 'one_time',
    }
    response = client.put(CATALOG_PATH, headers=admin, json={**write, 'external_product_id': 'prod_wrong'})
    assert response.status_code == 409, response.text
    assert client.get('/v1/billing/channels').json()['items'] == []
    response = client.put(CATALOG_PATH, headers=admin, json={**write, 'external_product_id': 'prod_pack'})
    assert response.status_code == 200, response.text
    return client, buyer, owner, viewers, platform, checkouts, products


def checkout(client, headers, *, key='creem-coin-purchase'):
    return client.post('/v1/commerce/checkout', headers=headers, json={
        'provider': 'creem', 'account_code': 'creem_store', 'product_type': 'coin_pack',
        'code': 'starter', 'idempotency_key': key,
    })


def paid(result, *, mode='test', amount=500, trade='tran_paid'):
    return {
        'id': result['session_id'], 'mode': mode, 'request_id': result['order_no'],
        'status': 'completed', 'metadata': {'jjtv_order_no': result['order_no']},
        'product': {'id': 'prod_pack'}, 'units': 1,
        'order': {
            'id': 'ord_paid', 'mode': mode, 'status': 'paid', 'type': 'onetime', 'product': 'prod_pack',
            'amount': amount, 'amount_due': amount + 100, 'amount_paid': amount + 100, 'tax_amount': 100,
            'currency': 'USD', 'transaction': trade,
        }
    }


def webhook(client, kind, obj, *, sign=True):
    raw = json.dumps({'id': 'evt_test', 'eventType': kind, 'object': obj}).encode()
    signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest() if sign else '0' * 64
    return client.post(WEBHOOK, content=raw, headers={'creem-signature': signature})


def test_creem_frozen_catalog_signed_checkout_and_refund_review(store, session):
    client, buyer, owner, (viewer, _other), platform, checkouts, _products = store
    channels = client.get('/v1/billing/channels').json()['items']
    assert channels[0]['products'] == [{'product_type': 'coin_pack', 'product_code': 'starter', 'billing_mode': 'one_time'}]
    first = checkout(client, viewer)
    assert first.status_code == 200, first.text
    result = first.json()
    assert checkout(client, viewer).json() == {**result, 'reused': True}
    assert len(checkouts) == 1 and f'order_no={result["order_no"]}' in checkouts[0]['success_url']
    assert 'payment_provider=creem' in checkouts[0]['success_url']
    commerce.save_pack(session, 'starter', dict(price_cents=900, base_coins=99), owner.id)
    assert client.get('/v1/billing/channels').json()['items'] == []
    assert checkout(client, viewer, key='next-creem-purchase').status_code == 409
    assert client.patch(ACCOUNT_PATH + '/creem_store', headers=platform, json={'mode': 'production'}).status_code == 409
    assert webhook(client, 'checkout.completed', paid(result, mode='prod')).status_code == 409
    assert webhook(client, 'checkout.completed', paid(result, amount=501)).status_code == 409
    assert webhook(client, 'checkout.completed', paid(result), sign=False).status_code == 401
    assert not client.get('/v1/commerce/orders/' + result['order_no'], headers=viewer).json()['settled']
    assert webhook(client, 'checkout.completed', paid(result)).status_code == 200
    assert webhook(client, 'checkout.completed', paid(result)).json()['replayed']
    session.expire_all()
    row = session.scalar(select(Order).where(Order.order_no == result['order_no']))
    assert (row.amount_cents, row.checkout_context['provider_amount_paid'], row.checkout_context['provider_tax_minor']) == (500, 600, 100)
    assert session.get(WalletAccount, buyer.id).balance == 50
    response = webhook(client, 'refund.created', {'id': 'ref_example', 'mode': 'test', 'status': 'succeeded',
        'refund_amount': 600, 'refund_currency': 'USD', 'transaction': {'id': 'tran_paid', 'mode': 'test',
        'status': 'refunded', 'amount': 500, 'amount_paid': 600, 'currency': 'USD'}})
    assert response.status_code == 200 and response.json()['manual_review'], response.text
    session.expire_all()
    assert row.status == 'refund_review'
    assert session.scalar(select(CommerceRefund)).status == 'manual_review'
    assert session.get(WalletAccount, buyer.id).balance == 50


def test_creem_return_recovery_requires_order_owner_and_test_mode(store, monkeypatch):
    client, _buyer, _owner, (viewer, other), _platform, _checkouts, _products = store
    monkeypatch.setenv('APP_ENV', 'production')
    assert client.get('/v1/billing/channels').json()['items'] == []
    assert checkout(client, viewer).status_code == 403
    monkeypatch.setenv('APP_ENV', 'local')
    result = checkout(client, viewer).json()
    path = '/v1/commerce/orders/' + result['order_no']
    used = []

    def lookup(checkout_id, **_):
        assert checkout_id == result['session_id']
        used.append(checkout_id)
        return paid(result)

    monkeypatch.setattr(creem, 'get_checkout', lookup)
    assert client.post(path + '/reconcile', headers=other).status_code == 404
    assert client.get(path, headers=viewer).json()['settled'] is False
    assert not used
    assert client.post(path + '/reconcile', headers=viewer).json()['settled'] is True
    assert len(used) == 1


def test_creem_timed_out_refund_reconciles_only_exact_original_transaction(store, session, monkeypatch):
    client, buyer, owner, (viewer, _), _platform, _checkouts, _products = store
    result = checkout(client, viewer).json()
    assert webhook(client, 'checkout.completed', paid(result)).status_code == 200
    remote = {
        'id': 'tran_paid', 'mode': 'test', 'status': 'paid',
        'amount': 500, 'amount_paid': 600, 'currency': 'USD', 'refunded_amount': 0,
    }
    requests = []
    monkeypatch.setattr(creem, 'get_transaction', lambda **_: dict(remote))

    def refund(**facts):
        requests.append(facts['transaction_id'])
        raise creem.CreemTransportError('outcome unknown')

    monkeypatch.setattr(creem, 'refund_transaction', refund)
    def request():
        return commerce.request_refund(session, result['order_no'], owner.id, 'Customer request', 'refund-creem-purchase')

    assert request()['status'] == 'pending'
    assert session.get(WalletAccount, buyer.id).balance == 0
    remote.update(status='partialRefund', refunded_amount=500)
    assert request()['status'] == 'pending'
    remote.update(status='refunded', refunded_amount=600)
    assert request()['status'] == 'succeeded'
    assert request()['status'] == 'succeeded'
    assert requests == ['tran_paid']
