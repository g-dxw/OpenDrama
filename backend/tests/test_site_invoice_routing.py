"""Signed HTTP routing must locate first invoices before checkout delivery."""
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.billing import commerce, service as billing
from app.modules.billing.commerce_models import CommerceSettlement, MembershipGrant, SiteMerchantBinding, StripeWebhookReceipt
from app.modules.billing.models import Order, Subscription
from app.modules.sites.models import SiteSettings
from app.modules.tenancy.consumer_scope import site_context
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.permissions import seed_tenant_defaults
from app.modules.wallet.models import WalletAccount
from tests.test_commerce import commercial, membership_order
from tests.test_legacy_webhook_alias import post_event

WEBHOOK = '/v1/billing/stripe/accounts/site-merchant/webhook'
SECRET = 'whsec_invoice_site'


@pytest.fixture
def pending_site_membership(session, commercial, make):
    _original_user, actor, _calls = commercial
    commerce.save_account(session, 'site-merchant', dict(name='Site merchant', status='active',
        secret_key='sk_test_invoice_site', webhook_secret=SECRET), actor.id)
    site = Tenant(id='invoice-site', code='invoice-site', name='Invoice site', kind='native', status='active')
    session.add(site); seed_tenant_defaults(session, site); session.flush()
    session.add_all([
        SiteSettings(tenant_id=site.id, web_url='https://invoice.example', h5_url='https://h5.invoice.example'),
        SiteMerchantBinding(tenant_id=site.id, account_code='site-merchant', explicit_reuse=False),
    ])
    session.commit()
    with site_context(session, site.id):
        user = make.user(email='subscriber@example.com')
        billing.get_billing_settings(session).billing_mode = 'subscription'; session.commit()
        order = membership_order(session, user)
    assert order.account_code == 'site-merchant'
    assert order.subscription_ref is None and order.payment_intent_id is None
    return site, user, order


def invoice_event(order, shape):
    stamp = int(time.time())
    details = {'metadata': {'commerce_order_no': order.order_no, 'tenant_id': 'jjtv'}}
    obj = dict(id='in_first_site', subscription='sub_first_site', payment_intent='pi_first_site',
        customer='cus_first_site', amount_paid=order.amount_cents, currency=order.currency.lower(),
        billing_reason='subscription_create', metadata={'tenant_id': 'jjtv'},
        lines={'data': [{'period': {'start': stamp, 'end': stamp + 30*86400}}]})
    if shape == 'legacy':
        obj['subscription_details'] = details
    else:
        obj.pop('subscription')
        obj['parent'] = {'subscription_details': {**details, 'subscription': 'sub_first_site'}}
    return {'id': 'evt_invoice_first_site', 'livemode': False, 'type': 'invoice.paid', 'data': {'object': obj}}


def checkout_event(order):
    return {'id': 'evt_checkout_late_site', 'livemode': False, 'type': 'checkout.session.completed', 'data': {'object': {
        'id': order.external_txn_id, 'mode': 'subscription', 'payment_status': 'paid',
        'amount_total': order.amount_cents, 'currency': order.currency.lower(),
        'subscription': 'sub_first_site', 'metadata': {'commerce_order_no': order.order_no, 'tenant_id': 'jjtv'}}}}


@pytest.mark.parametrize('shape', ['legacy', 'parent'])
@pytest.mark.parametrize('checkout_first', [False, True])
def test_first_invoice_routes_by_stored_order_across_delivery_order_and_replay(session, pending_site_membership, shape, checkout_first):
    site, user, order = pending_site_membership
    client = TestClient(app)
    invoice = invoice_event(order, shape); checkout = checkout_event(order)
    first, second = (checkout, invoice) if checkout_first else (invoice, checkout)
    # testserver resolves the original site; signed tenant fields also name the
    # wrong site. Only the existing order and verified merchant may route it.
    response = post_event(client, first, WEBHOOK+'?tenant_id=jjtv', SECRET)
    assert response.status_code == 200, response.text
    if not checkout_first:
        assert 'ignored' not in response.json(), response.text
        session.expire_all()
        with site_context(session, site.id):
            assert billing.access_until(session, user.id) is not None
            assert session.get(WalletAccount, user.id).balance == 25
            assert session.get(Order, order.id).invoice_id == 'in_first_site'
    else:
        session.expire_all()
        with site_context(session, site.id):
            assert billing.access_until(session, user.id) is None
            assert session.get(WalletAccount, user.id).balance == 0
    for event in [second, invoice, checkout]:
        response = post_event(client, event, WEBHOOK, SECRET)
        assert response.status_code == 200, response.text
    session.expire_all()
    with site_context(session, site.id):
        stored = session.get(Order, order.id)
        assert stored.status == 'paid' and stored.subscription_ref == 'sub_first_site'
        assert stored.payment_intent_id == 'pi_first_site'
        assert session.get(WalletAccount, user.id).balance == 25
        assert billing.access_until(session, user.id) is not None
        assert session.scalar(select(func.count()).select_from(Order)) == 1
        assert session.scalar(select(func.count()).select_from(CommerceSettlement)) == 1
        assert session.scalar(select(func.count()).select_from(MembershipGrant)) == 1
        assert session.scalar(select(Subscription)).account_code == 'site-merchant'
    with site_context(session, 'jjtv'):
        assert session.scalar(select(func.count()).select_from(MembershipGrant)) == 0
    receipts = list(session.scalars(select(StripeWebhookReceipt)))
    assert len(receipts) == 2
    assert all(row.processed and row.tenant_id == site.id and row.account_code == 'site-merchant' for row in receipts)


def test_legacy_first_invoice_rejects_wrong_merchant_and_signature_without_acknowledging(session, pending_site_membership, commercial):
    site, user, order = pending_site_membership
    _original_user, actor, _calls = commercial
    commerce.save_account(session, 'wrong-merchant', dict(name='Other merchant', status='active',
        secret_key='sk_test_wrong_site', webhook_secret='whsec_wrong_site'), actor.id)
    payload = invoice_event(order, 'legacy'); client = TestClient(app)
    wrong_account = post_event(client, payload, '/v1/billing/stripe/accounts/wrong-merchant/webhook', 'whsec_wrong_site')
    assert wrong_account.status_code == 401, wrong_account.text
    invalid_signature = post_event(client, payload, WEBHOOK, 'whsec_invalid')
    assert invalid_signature.status_code == 401, invalid_signature.text
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(StripeWebhookReceipt)) == 0
    with site_context(session, site.id):
        assert session.get(Order, order.id).subscription_ref is None
        assert session.get(WalletAccount, user.id).balance == 0
        assert billing.access_until(session, user.id) is None
    response = post_event(client, payload, WEBHOOK, SECRET)
    assert response.status_code == 200 and 'ignored' not in response.json(), response.text
    replay = post_event(client, payload, WEBHOOK, SECRET)
    assert replay.status_code == 200 and replay.json()['replayed'] is True
    session.expire_all()
    with site_context(session, site.id):
        assert session.get(WalletAccount, user.id).balance == 25
        assert session.scalar(select(func.count()).select_from(MembershipGrant)) == 1


def test_signed_tenant_claim_without_existing_order_cannot_create_access(session, pending_site_membership):
    site, user, order = pending_site_membership
    payload = invoice_event(order, 'legacy')
    payload['data']['object']['subscription_details']['metadata']['commerce_order_no'] = 'unknown-order'
    payload['data']['object']['subscription_details']['metadata']['tenant_id'] = site.id
    response = post_event(TestClient(app), payload, WEBHOOK, SECRET)
    assert response.status_code == 200 and response.json()['ignored'] == 'invoice.paid', response.text
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(StripeWebhookReceipt)) == 0
    with site_context(session, site.id):
        assert billing.access_until(session, user.id) is None
        assert session.get(WalletAccount, user.id).balance == 0
