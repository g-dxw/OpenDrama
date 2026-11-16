"""Admin coin-pack forms omit currency and retain immutable purchase versions."""
import copy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.billing import commerce
from app.modules.billing.commerce_models import CoinPack, CoinPackVersion, SiteMerchantBinding
from app.modules.billing.models import Order
from app.modules.sites.models import SiteSettings
from app.modules.tenancy.consumer_scope import site_context
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.permissions import seed_tenant_defaults
from app.modules.wallet.models import WalletAccount
from tests.test_commerce import commercial, pay, purchase
from tests.test_merchant_binding_identity import login


@pytest.fixture(params=['EUR', 'INR'])
def currency_site(request, session, commercial, make):
    currency = request.param
    site = Tenant(id='currency-site', code='currency-site', name='Currency site', kind='native', status='active')
    session.add(site); seed_tenant_defaults(session, site); session.flush()
    session.add_all([
        SiteSettings(tenant_id=site.id, currency=currency, web_url='https://currency.example', h5_url='https://h5.currency.example'),
        SiteMerchantBinding(tenant_id=site.id, account_code='env', explicit_reuse=True),
    ])
    session.commit()
    actor = make.admin(role='admin', tenant_id=site.id)
    with site_context(session, site.id):
        user = make.user(email='buyer@example.com')
    client = TestClient(app)
    return site, currency, user, client, login(client, actor)


def pack_form(**overrides):
    return dict(code='site_offer', name_en='Site offer', price_cents=500, base_coins=100, bonus_coins=20, **overrides)


def test_omitted_currency_creates_site_price_and_patch_keeps_order_snapshot(session, currency_site):
    site, currency, user, client, headers = currency_site
    created = client.post('/admin/v1/coin-packs', headers=headers, json=pack_form())
    assert created.status_code == 200, created.text
    assert created.json()['currency'] == currency and created.json()['version'] == 1
    original_version_id = created.json()['version_id']
    with site_context(session, site.id):
        order = purchase(session, user, code='site_offer')
        frozen = copy.deepcopy(order.product_snapshot)
        assert order.currency == currency and order.amount_cents == 500
    patched = client.patch('/admin/v1/coin-packs/site_offer', headers=headers,
        json={'price_cents': 900, 'base_coins': 999})
    assert patched.status_code == 200, patched.text
    assert patched.json()['currency'] == currency and patched.json()['version'] == 2
    session.expire_all()
    with site_context(session, site.id):
        original = session.get(CoinPackVersion, original_version_id)
        assert (original.currency, original.price_cents, original.base_coins, original.bonus_coins) == (currency, 500, 100, 20)
        order = session.get(Order, order.id)
        assert order.product_snapshot == frozen
        assert (order.product_snapshot['version_id'], order.currency, order.amount_cents) == (original_version_id, currency, 500)
        pay(session, order)
        assert session.get(WalletAccount, user.id).balance == 120
        assert session.scalar(select(func.count()).select_from(CoinPackVersion)) == 2
    # The same code is absent from the original site, including its public list.
    original = client.get('/v1/coin-packs')
    assert original.status_code == 200
    assert 'site_offer' not in {item['code'] for item in original.json()['items']}


def test_explicit_currency_requires_site_match_on_create_and_update(session, currency_site):
    site, currency, _user, client, headers = currency_site
    wrong = client.post('/admin/v1/coin-packs', headers=headers, json=pack_form(currency='USD'))
    assert wrong.status_code == 409, wrong.text
    assert currency in wrong.json()['detail']
    session.expire_all()
    with site_context(session, site.id):
        assert session.scalar(select(func.count()).select_from(CoinPack)) == 0
        assert session.scalar(select(func.count()).select_from(CoinPackVersion)) == 0
    created = client.post('/admin/v1/coin-packs', headers=headers, json=pack_form(currency=currency))
    assert created.status_code == 200, created.text
    wrong = client.patch('/admin/v1/coin-packs/site_offer', headers=headers,
        json={'currency': 'USD', 'price_cents': 999, 'base_coins': 999})
    assert wrong.status_code == 409, wrong.text
    unchanged = client.get('/admin/v1/coin-packs', headers=headers)
    assert unchanged.status_code == 200, unchanged.text
    item = unchanged.json()['items'][0]
    assert (item['currency'], item['version'], item['price_cents'], item['base_coins']) == (currency, 1, 500, 100)
    session.expire_all()
    with site_context(session, site.id):
        assert session.scalar(select(func.count()).select_from(CoinPackVersion)) == 1
