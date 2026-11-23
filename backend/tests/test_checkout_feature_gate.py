"""Disable new sales without blocking frozen orders, renewals, or cancellation."""
import copy
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.account import service as accounts
from app.modules.billing import commerce, service as billing
from app.modules.billing.commerce_models import CommerceSettlement, MembershipGrant
from app.modules.billing.models import Order, Plan, PlanVersion, Subscription
from app.modules.sites.models import SiteSettings
from app.modules.tenancy.models import TenantModule
from app.modules.wallet.models import WalletAccount
from tests.test_commerce import commercial
from tests.test_legacy_webhook_alias import post_event


@pytest.fixture
def sales(session, commercial):
    user, _actor, calls = commercial
    plan = Plan(code='monthly', name_en='Monthly'); session.add(plan); session.flush()
    session.add(PlanVersion(plan_id=plan.id, version=1, price_cents=1999, base_coins=20, bonus_coins=5, benefits={}))
    session.add(SiteSettings(tenant_id='jjtv', currency='USD', web_url='https://app.example.com', h5_url='https://m.example.com'))
    session.commit()
    return user, calls, TestClient(app), {'Authorization': 'Bearer '+accounts._issue_session(user)}


def disable_sales(session, product, via):
    module, feature = ('plans', 'membership') if product == 'membership' else ('coin_packs', 'coins')
    session.get(TenantModule, ('jjtv', module)).enabled = via != 'module'
    settings = session.get(SiteSettings, 'jjtv')
    # A storefront true override cannot reopen a disabled business module.
    settings.features = {feature: via != 'feature'}
    session.commit()


def checkout(client, headers, product, *, alias=False, key='site-gate-checkout'):
    if alias:
        return client.post('/v1/billing/checkout', headers={**headers, 'Idempotency-Key': key}, json={'plan_code': 'monthly'})
    return client.post('/v1/commerce/checkout', headers=headers, json={
        'product_type': product, 'code': 'monthly' if product == 'membership' else 'starter', 'idempotency_key': key})


@pytest.mark.parametrize('product,alias', [('coin_pack', False), ('membership', False), ('membership', True)])
@pytest.mark.parametrize('via', ['module', 'feature'])
def test_disabled_new_checkout_rejects_both_apis_before_order_or_stripe(session, sales, product, alias, via):
    _user, calls, client, headers = sales
    disable_sales(session, product, via)
    prior = copy.deepcopy(calls)
    result = checkout(client, headers, product, alias=alias)
    assert result.status_code == 403, result.text
    assert calls == prior
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Order)) == 0
    assert session.scalar(select(func.count()).select_from(CommerceSettlement)) == 0


@pytest.mark.parametrize('product,alias', [('coin_pack', False), ('membership', False), ('membership', True)])
@pytest.mark.parametrize('via', ['module', 'feature'])
def test_existing_one_time_order_retries_and_signed_settlement_survive_sales_shutdown(session, sales, product, alias, via):
    user, calls, client, headers = sales
    created = checkout(client, headers, product, alias=alias)
    assert created.status_code == 200, created.text
    number = created.json()['order_no']
    order = session.scalar(select(Order).where(Order.order_no == number))
    frozen = copy.deepcopy(order.product_snapshot)
    disable_sales(session, product, via)
    calls_before = copy.deepcopy(calls)
    retry = checkout(client, headers, product, alias=alias)
    assert retry.status_code == 200 and retry.json()['order_no'] == number and retry.json()['reused'], retry.text
    assert calls == calls_before
    event = {'id': 'evt_order_gate', 'type': 'checkout.session.completed', 'data': {'object': {
        'id': order.external_txn_id, 'mode': 'payment', 'payment_status': 'paid', 'payment_intent': 'pi_order_gate',
        'amount_total': order.amount_cents, 'currency': order.currency.lower(), 'metadata': {'commerce_order_no': number}}}}
    for _ in range(2):
        response = post_event(client, event)
        assert response.status_code == 200, response.text
    session.expire_all()
    stored = session.get(Order, order.id)
    assert stored.status == 'paid' and stored.product_snapshot == frozen
    assert session.get(WalletAccount, user.id).balance == (25 if product == 'membership' else 120)
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert session.scalar(select(func.count()).select_from(CommerceSettlement)) == 1
    if product == 'membership':
        assert billing.access_until(session, user.id) is not None


@pytest.mark.parametrize('via', ['module', 'feature'])
def test_existing_subscription_invoices_renew_and_cancel_after_new_sales_are_disabled(session, sales, via):
    user, _calls, client, headers = sales
    billing.get_billing_settings(session).billing_mode = 'subscription'; session.commit()
    response = checkout(client, headers, 'membership')
    assert response.status_code == 200, response.text
    order = session.scalar(select(Order).where(Order.order_no == response.json()['order_no']))
    original_version = order.plan_version_id
    disable_sales(session, 'membership', via)
    start = int(time.time())
    for index in range(2):
        invoice = {'id': 'evt_renew_gate_'+str(index), 'type': 'invoice.paid', 'data': {'object': {
            'id': 'in_renew_gate_'+str(index), 'subscription': 'sub_renew_gate', 'payment_intent': 'pi_renew_gate_'+str(index),
            'amount_paid': 1999, 'currency': 'usd', 'customer': 'cus_renew_gate',
            'subscription_details': {'metadata': {'commerce_order_no': order.order_no}},
            'billing_reason': 'subscription_create' if index == 0 else 'subscription_cycle',
            'lines': {'data': [{'period': {'start': start+index*30*86400, 'end': start+(index+1)*30*86400}}]}}}}
        for _ in range(2):
            response = post_event(client, invoice)
            assert response.status_code == 200, response.text
    session.expire_all()
    assert session.get(WalletAccount, user.id).balance == 50
    assert session.scalar(select(func.count()).select_from(MembershipGrant)) == 2
    assert session.scalar(select(func.count()).select_from(CommerceSettlement)) == 2
    assert {row.plan_version_id for row in session.scalars(select(Order))} == {original_version}
    profile = client.get('/v1/me', headers=headers)
    assert profile.status_code == 200 and profile.json()['membership']['active'], profile.text
    canceled = client.post('/v1/billing/cancel', headers=headers)
    assert canceled.status_code == 200, canceled.text
    session.expire_all()
    assert session.scalar(select(Subscription)).canceled_at is not None
    assert billing.access_until(session, user.id) is not None


@pytest.mark.parametrize('via', ['module', 'feature'])
def test_unpaid_subscription_can_still_be_cancelled_after_sales_close(session, sales, via):
    _user, _calls, client, headers = sales
    billing.get_billing_settings(session).billing_mode = 'subscription'; session.commit()
    response = checkout(client, headers, 'membership')
    assert response.status_code == 200, response.text
    number = response.json()['order_no']; disable_sales(session, 'membership', via)
    canceled = client.post('/v1/commerce/orders/'+number+'/cancel', headers=headers)
    assert canceled.status_code == 200 and canceled.json()['canceled'], canceled.text
    again = client.post('/v1/commerce/orders/'+number+'/cancel', headers=headers)
    assert again.status_code == 200 and again.json()['canceled'], again.text
    session.expire_all()
    assert session.scalar(select(Order)).status == 'failed'


@pytest.mark.parametrize('disabled_product', ['coin_pack', 'membership'])
@pytest.mark.parametrize('via', ['module', 'feature'])
def test_product_sales_switches_are_independent(session, sales, disabled_product, via):
    _user, _calls, client, headers = sales
    disable_sales(session, disabled_product, via)
    other = 'membership' if disabled_product == 'coin_pack' else 'coin_pack'
    response = checkout(client, headers, other)
    assert response.status_code == 200, response.text
    session.expire_all()
    assert session.scalar(select(Order)).product_type == other


@pytest.mark.parametrize('via', ['module', 'feature'])
@pytest.mark.parametrize('alias', [False, True])
def test_new_key_cannot_bypass_shutdown_by_reusing_a_pending_subscription(session, sales, via, alias):
    _user, calls, client, headers = sales
    billing.get_billing_settings(session).billing_mode = 'subscription'; session.commit()
    original = checkout(client, headers, 'membership', alias=alias)
    assert original.status_code == 200, original.text
    number = original.json()['order_no']
    disable_sales(session, 'membership', via)
    prior = copy.deepcopy(calls)
    rejected = checkout(client, headers, 'membership', alias=alias, key='a-different-new-sale-key')
    assert rejected.status_code == 403, rejected.text
    assert calls == prior
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    retry = checkout(client, headers, 'membership', alias=alias)
    assert retry.status_code == 200 and retry.json()['order_no'] == number and retry.json()['reused'], retry.text


@pytest.mark.parametrize('via', ['module', 'feature'])
def test_public_coin_catalog_hides_disabled_sales_without_changing_admin_permissions(session, sales, commercial, monkeypatch, via):
    from app.modules.sites import service as sites
    from tests.test_merchant_binding_identity import login
    user, calls, client, _headers = sales
    _same_user, actor, _same_calls = commercial
    admin_headers = login(client, actor)
    session.get(WalletAccount, user.id).balance = 77; session.commit()
    disable_sales(session, 'coin_pack', via)
    monkeypatch.setattr(sites, 'lock_site_settings', lambda *_: pytest.fail('public catalog must not take checkout locks'))
    before = copy.deepcopy(calls)
    public = client.get('/v1/coin-packs')
    assert public.status_code == 200 and public.json() == {'items': []}, public.text
    assert calls == before
    assert [item['code'] for item in commerce.list_packs(session, True)] == ['starter']
    admin = client.get('/admin/v1/coin-packs', headers=admin_headers)
    if via == 'module':
        assert admin.status_code == 403 and admin.json()['detail']['code'] == 'tenant_permission_denied', admin.text
    else:
        assert admin.status_code == 200, admin.text
        assert [item['code'] for item in admin.json()['items']] == ['starter']
    assert session.get(WalletAccount, user.id).balance == 77
    if via == 'module':
        bootstrap = client.get('/v1/site/bootstrap')
        assert bootstrap.status_code == 200 and bootstrap.json()['features']['coins'] is True


def test_public_coin_catalog_keeps_original_sales_on_prices(session, sales, monkeypatch):
    from app.modules.sites import service as sites
    _user, _calls, client, _headers = sales
    expected = commerce.list_packs(session)
    monkeypatch.setattr(sites, 'lock_site_settings', lambda *_: pytest.fail('public catalog must not take checkout locks'))
    response = client.get('/v1/coin-packs')
    assert response.status_code == 200 and response.json() == {'items': expected}, response.text
    assert expected[0]['currency'] == 'USD' and expected[0]['price_cents'] == 500


def test_public_coin_sale_switch_is_independent_for_another_site(session, sales):
    from datetime import datetime, timezone
    from app.modules.tenancy.models import Tenant, TenantDomain
    from app.modules.tenancy.permissions import seed_tenant_defaults
    from app.modules.tenancy.consumer_scope import site_context
    _user, _calls, original, _headers = sales
    site = Tenant(id='coin-catalog-site', code='coin-catalog-site', name='Coin catalog site', kind='native', status='active')
    session.add(site); seed_tenant_defaults(session, site); session.flush()
    session.add(SiteSettings(tenant_id=site.id))
    session.add(TenantDomain(tenant_id=site.id, hostname='coin-catalog.example', kind='site', status='active', verified_at=datetime.now(timezone.utc)))
    session.commit()
    with site_context(session, site.id):
        expected = commerce.save_pack(session, 'regional', dict(name_en='Regional', price_cents=800, base_coins=200), None)
    disable_sales(session, 'coin_pack', 'module')
    regional = TestClient(app, base_url='https://coin-catalog.example')
    response = regional.get('/v1/coin-packs')
    assert response.status_code == 200 and response.json() == {'items': [expected]}, response.text
    assert original.get('/v1/coin-packs').json() == {'items': []}
