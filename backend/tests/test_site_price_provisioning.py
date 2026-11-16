"""Legacy admin price entry points must use the current site's merchant."""
import copy
import os

from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.main import app
from app.modules.billing import commerce, service as billing
from app.modules.billing.commerce_models import SiteMerchantBinding, StripeAccount, StripePriceBinding
from app.modules.billing.models import Order, Plan, PlanVersion
from app.modules.ops.managed_settings import save_config
from app.modules.sites.models import SiteSettings
from app.modules.tenancy.consumer_scope import site_context
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.permissions import seed_tenant_defaults
from tests.test_merchant_binding_identity import login


@pytest.fixture()
def pricing(session, make, monkeypatch):
    monkeypatch.setenv('SETTINGS_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_original_site_only')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_original_site_only')
    for code in ('weekly', 'monthly', 'yearly'):
        monkeypatch.setenv('STRIPE_PRODUCT_' + code.upper(), 'prod_original_' + code)
        monkeypatch.setenv('STRIPE_PRICE_' + code.upper(), 'price_original_' + code)
    session.add(StripeAccount(code='independent', name='Independent merchant', status='active',
                              remote_id='acct_independent', livemode=False))
    save_config(session, 'stripe:independent',
                {'secret_key': 'sk_test_independent_site_only', 'webhook_secret': 'whsec_independent_site_only'},
                secret_fields=('secret_key', 'webhook_secret'))
    headers = {}
    client = TestClient(app)
    for site, merchant in (('jjtv', 'env'), ('independent-site', 'independent'), ('shared-site', 'env'), ('unbound-site', None)):
        if site != 'jjtv':
            tenant = Tenant(id=site, code=site, name=site, kind='native', status='active')
            session.add(tenant)
            seed_tenant_defaults(session, tenant)
        session.add(SiteSettings(tenant_id=site, currency='USD', web_url='https://' + site + '.example.test',
                                 h5_url='https://h5.' + site + '.example.test'))
        if merchant:
            session.add(SiteMerchantBinding(tenant_id=site, account_code=merchant, explicit_reuse=site == 'shared-site'))
        billing.provision_site_billing(session, site)
        session.commit()
        actor = make.admin(role='admin', tenant_id=site)
        headers[site] = login(client, actor)
    with site_context(session, 'jjtv'):
        user = make.user(email='historical@example.test', coins=77)
        plan = session.scalar(select(Plan).where(Plan.code == 'weekly'))
        version = billing._latest_version(session, plan)
        version.stripe_price_id = 'price_historical_subscription'
        version.stripe_one_time_price_id = 'price_historical_pass'
        order = Order(order_no='historical-order-preserved', user_id=user.id, plan_version_id=version.id,
                      product_type='membership', account_code='env', amount_cents=999, currency='USD',
                      billing_mode='one_time', status='paid',
                      product_snapshot={'version_id': version.id, 'price_cents': 999, 'currency': 'USD', 'name': 'Historical'})
        session.add(order)
        session.commit()
        historical = (order.id, copy.deepcopy(order.product_snapshot), version.id)
    calls = []

    def modern(secret, path, data=None, method='POST', idempotency_key=None):
        calls.append(dict(kind='modern', secret=secret, path=path, data=data, key=idempotency_key))
        assert path == '/prices'
        return {'id': 'price_scoped_' + str(len(calls))}

    def legacy(path, data=None, method='POST'):
        calls.append(dict(kind='legacy', secret=billing.stripe_secret(), path=path, data=data))
        return {'id': 'price_legacy_' + str(len(calls))}

    monkeypatch.setattr(commerce, '_stripe_http', modern)
    monkeypatch.setattr(billing, '_stripe_request', legacy)
    environment = {name: os.environ[name] for name in ('STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET',
                   *(name for name in billing.PRICE_ENV.values()), *(name for name in billing.PRODUCT_ENV.values()))}
    return dict(client=client, headers=headers, calls=calls, historical=historical, environment=environment)


def assert_original_untouched(session, case):
    order_id, snapshot, version_id = case['historical']
    session.expire_all()
    with site_context(session, 'jjtv'):
        order = session.get(Order, order_id)
        assert (order.status, order.amount_cents, order.account_code, order.currency, order.product_snapshot) == (
            'paid', 999, 'env', 'USD', snapshot)
        version = session.get(PlanVersion, version_id)
        assert (version.stripe_price_id, version.stripe_one_time_price_id, version.price_cents) == (
            'price_historical_subscription', 'price_historical_pass', 999)
    assert {name: os.environ[name] for name in case['environment']} == case['environment']


@pytest.mark.parametrize('site,account,secret', [
    ('independent-site', 'independent', 'sk_test_independent_site_only'),
    ('shared-site', 'env', 'sk_test_original_site_only'),
])
@pytest.mark.parametrize('mode', ['subscription', 'one_time'])
def test_mode_switch_provisions_only_site_bound_merchant_and_price_bindings(session, pricing, site, account, secret, mode):
    case = pricing
    response = case['client'].post('/admin/v1/billing/settings', headers=case['headers'][site],
                                   json={'billing_mode': mode})
    assert response.status_code == 200, response.text
    assert all(item['ok'] for item in response.json()['prices'])
    assert len(case['calls']) == 3
    assert all(call['kind'] == 'modern' and call['secret'] == secret for call in case['calls'])
    assert all('product' not in call['data'] and 'product_data[name]' in call['data'] for call in case['calls'])
    with site_context(session, site):
        rows = session.scalars(select(StripePriceBinding)).all()
        assert len(rows) == 3
        assert {(row.tenant_id, row.account_code, row.billing_mode) for row in rows} == {(site, account, mode)}
        assert all(row.price_id.startswith('price_scoped_') for row in rows)
        assert all(version.stripe_price_id is None and version.stripe_one_time_price_id is None
                   for version in session.scalars(select(PlanVersion)))
    repeated = case['client'].post('/admin/v1/billing/settings', headers=case['headers'][site], json={'billing_mode': mode})
    assert repeated.status_code == 200
    assert len(case['calls']) == 3
    assert_original_untouched(session, case)


@pytest.mark.parametrize('site,account', [('independent-site', 'independent'), ('shared-site', 'env')])
def test_legacy_price_edit_and_modern_price_edit_share_site_bindings_without_legacy_ids(session, pricing, site, account):
    case = pricing
    legacy = case['client'].post('/admin/v1/plans/weekly/price', headers=case['headers'][site],
                               json={'price_cents': 1234, 'benefits': {'items': ['site benefit']}})
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()['stripe_price_id'] is None
    with site_context(session, site):
        plan = session.scalar(select(Plan).where(Plan.code == 'weekly'))
        version = billing._latest_version(session, plan)
        binding = session.scalar(select(StripePriceBinding).where(StripePriceBinding.version_id == version.id))
        assert binding is not None and (binding.tenant_id, binding.account_code) == (site, account)
        assert binding.price_id.startswith('price_scoped_')
    modern = case['client'].post('/admin/v1/commerce/plans/weekly/price', headers=case['headers'][site],
                               json={'price_cents': 2345, 'base_coins': 10, 'bonus_coins': 2,
                                     'benefits': {'items': ['modern']}, 'note': 'Site configuration update'})
    assert modern.status_code == 200, modern.text
    switched = case['client'].post('/admin/v1/billing/settings', headers=case['headers'][site],
                                  json={'billing_mode': 'subscription'})
    assert switched.status_code == 200 and all(item['ok'] for item in switched.json()['prices'])
    assert all(call['kind'] == 'modern' for call in case['calls'])
    assert_original_untouched(session, case)


def test_no_binding_fails_before_environment_credentials_or_product_ids_are_used(session, pricing):
    case = pricing
    with site_context(session, 'unbound-site'):
        plan = session.scalar(select(Plan).where(Plan.code == 'weekly'))
        version = billing._latest_version(session, plan)
        with pytest.raises(billing.BillingError) as raised:
            billing.ensure_stripe_price(session, plan, version, 'subscription')
        assert raised.value.status_code == 503
        assert 'no configured payment merchant' in raised.value.detail
    # Mode remains an editable local draft; best-effort provisioning reports every failure.
    response = case['client'].post('/admin/v1/billing/settings', headers=case['headers']['unbound-site'],
                                   json={'billing_mode': 'one_time'})
    assert response.status_code == 200, response.text
    assert all(not item['ok'] and 'no configured payment merchant' in item['error'] for item in response.json()['prices'])
    assert case['calls'] == []
    with site_context(session, 'unbound-site'):
        assert session.scalar(select(StripePriceBinding)) is None
        assert version.stripe_price_id is None and version.stripe_one_time_price_id is None
    assert_original_untouched(session, case)


def test_shared_env_site_does_not_reuse_legacy_version_price_columns(session, pricing):
    with site_context(session, 'shared-site'):
        plan = session.scalar(select(Plan).where(Plan.code == 'weekly'))
        version = billing._latest_version(session, plan)
        version.stripe_price_id = 'price_original_weekly'
        version.stripe_one_time_price_id = 'price_original_one_time'
        session.commit()
        subscription = billing.ensure_stripe_price(session, plan, version, 'subscription')
        one_time = billing.ensure_stripe_price(session, plan, version, 'one_time')
        assert subscription.startswith('price_scoped_') and one_time.startswith('price_scoped_')
        assert subscription != one_time
        assert len(session.scalars(select(StripePriceBinding)).all()) == 2
    assert all(call['kind'] == 'modern' for call in pricing['calls'])
    assert_original_untouched(session, pricing)


def test_explicit_env_binding_preserves_jjtv_legacy_provisioning(session, pricing):
    with site_context(session, 'jjtv'):
        plan = session.scalar(select(Plan).where(Plan.code == 'monthly'))
        original = billing._latest_version(session, plan)
        assert billing.ensure_stripe_price(session, plan, original, 'subscription') == 'price_original_monthly'
        version = billing.change_price(session, code='monthly', price_cents=3210, benefits={}, actor_id=1)
        assert version.stripe_one_time_price_id.startswith('price_legacy_')
    assert pricing['calls'] == [dict(kind='legacy', secret='sk_test_original_site_only', path='/prices', data={
        'product': 'prod_original_monthly', 'unit_amount': 3210, 'currency': 'usd',
        'nickname': 'Monthly v2 $32.10 one-time'})]
    assert billing._stripe_credentials.get() is None
    assert_original_untouched(session, pricing)


def test_jjtv_bound_to_independent_merchant_does_not_provision_original_env_account(session, pricing):
    session.get(SiteMerchantBinding, 'jjtv').account_code = 'independent'
    session.commit()
    with site_context(session, 'jjtv'):
        plan = session.scalar(select(Plan).where(Plan.code == 'weekly'))
        version = billing._latest_version(session, plan)
        price = billing.ensure_stripe_price(session, plan, version, 'subscription')
        binding = session.scalar(select(StripePriceBinding).where(StripePriceBinding.version_id == version.id))
        assert price.startswith('price_scoped_') and binding.account_code == 'independent'
    assert pricing['calls'][0]['secret'] == 'sk_test_independent_site_only'
    assert pricing['calls'][0]['kind'] == 'modern'
    assert_original_untouched(session, pricing)


def test_loaded_foreign_plan_and_mismatched_version_are_rejected_before_stripe(session, pricing):
    with site_context(session, 'jjtv'):
        foreign = session.scalar(select(Plan).where(Plan.code == 'weekly'))
        foreign_version = billing._latest_version(session, foreign)
    with site_context(session, 'independent-site'):
        own = session.scalar(select(Plan).where(Plan.code == 'weekly'))
        own_version = billing._latest_version(session, own)
        for plan, version in ((foreign, foreign_version), (own, foreign_version), (foreign, own_version)):
            with pytest.raises((HTTPException, billing.BillingError)):
                billing.ensure_stripe_price(session, plan, version, 'subscription')
        other_plan = session.scalar(select(Plan).where(Plan.code == 'monthly'))
        with pytest.raises(billing.BillingError) as raised:
            billing.ensure_stripe_price(session, other_plan, own_version, 'subscription')
        assert raised.value.status_code == 404
    assert pricing['calls'] == []
