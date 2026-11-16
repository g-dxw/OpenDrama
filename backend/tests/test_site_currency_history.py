"""A site's first order freezes its denomination, serialized with new checkout."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, delete
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.modules.billing import commerce
from app.modules.billing.models import Order, Plan, PlanVersion, BillingSettings
from app.modules.billing.service import BillingError
from app.modules.ops import service as auth
from app.modules.common.models import AuditLog
from app.modules.sites import router as sites_router
from app.modules.sites.models import SiteSettings
from app.modules.sites.service import get_site, lock_site_settings
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.permissions import seed_tenant_defaults
from tests.test_commerce import commercial, purchase, pay


def headers(person):
    return {'Authorization': 'Bearer ' + auth.create_session(person)}


@pytest.fixture()
def currency_env(session, commercial):
    get_site(session, 'jjtv')
    session.commit()
    return commercial


def add_history(db, user, status='paid'):
    order = Order(tenant_id=user.tenant_id, order_no='CURRENCY-HISTORY-' + status, user_id=user.id,
        product_type='coin_pack', billing_mode='one_time', amount_cents=10000, currency='USD', status=status,
        product_snapshot={'currency': 'USD', 'price_cents': 10000})
    if status in {'paid', 'refunded'}: order.paid_at = datetime.now(timezone.utc)
    if status == 'refunded': order.refunded_at = datetime.now(timezone.utc)
    db.add(order)
    db.commit()
    return order


def patch(client, db, make, actor, endpoint, values):
    if endpoint == 'tenant':
        return client.patch('/tenant/v1/site', json=values, headers=headers(actor))
    db.expire_all()
    tenant = db.get(Tenant, actor.tenant_id)
    return client.patch('/platform/v1/tenants/' + actor.tenant_id,
        json={'version': tenant.version, **values}, headers=headers(make.platform_admin()))


@pytest.mark.parametrize('status', ['created', 'paid', 'failed', 'refunded'])
@pytest.mark.parametrize('endpoint', ['tenant', 'platform'])
def test_any_order_history_blocks_denomination_changes(session, currency_env, make, status, endpoint):
    user, actor, _ = currency_env
    order = add_history(session, user, status)
    client = TestClient(app)
    before_settings = session.get(SiteSettings, 'jjtv')
    before_timezone, before_version = before_settings.timezone, before_settings.version
    before_name = session.get(Tenant, 'jjtv').name
    update_actions = ['site.settings.update', 'platform.tenant.update']
    before_audits = session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action.in_(update_actions)))
    values = {'currency': 'EUR', 'timezone': 'Asia/Tokyo'}
    if endpoint == 'platform': values['name'] = 'Must not persist'
    response = patch(client, session, make, actor, endpoint, values)
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'site_currency_has_orders'
    session.expire_all()
    assert session.get(SiteSettings, 'jjtv').currency == 'USD'
    assert session.get(Order, order.id).currency == 'USD'
    assert session.get(Order, order.id).amount_cents == 10000
    assert (session.get(SiteSettings, 'jjtv').timezone, session.get(SiteSettings, 'jjtv').version) == (before_timezone, before_version)
    assert session.get(Tenant, 'jjtv').name == before_name
    assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action.in_(update_actions))) == before_audits


@pytest.mark.parametrize('endpoint', ['tenant', 'platform'])
def test_same_currency_and_other_settings_remain_editable_with_history(session, currency_env, make, endpoint):
    user, actor, _ = currency_env
    add_history(session, user)
    client = TestClient(app)
    values = {'currency': 'USD', 'timezone': 'Asia/Tokyo'}
    if endpoint == 'platform': values['name'] = 'Allowed settings update'
    response = patch(client, session, make, actor, endpoint, values)
    assert response.status_code == 200, response.text
    response = patch(client, session, make, actor, endpoint, {'timezone': 'Europe/Paris'})
    assert response.status_code == 200, response.text
    session.expire_all()
    row = session.get(SiteSettings, 'jjtv')
    assert row.currency == 'USD' and row.timezone == 'Europe/Paris'
    if endpoint == 'platform': assert session.get(Tenant, 'jjtv').name == 'Allowed settings update'


@pytest.mark.parametrize('endpoint', ['tenant', 'platform'])
def test_order_free_site_can_choose_currency(session, currency_env, make, endpoint):
    _, actor, _ = currency_env
    response = patch(TestClient(app), session, make, actor, endpoint, {'currency': 'EUR'})
    assert response.status_code == 200, response.text
    session.expire_all()
    assert session.get(SiteSettings, 'jjtv').currency == 'EUR'


def test_existing_orders_do_not_freeze_an_independent_site_or_platform_creation(session, currency_env, make):
    user, _, _ = currency_env
    add_history(session, user)
    other = Tenant(id='currency-independent', code='currency-independent', name='Independent', kind='native', status='active')
    session.add(other)
    seed_tenant_defaults(session, other)
    get_site(session, other.id)
    session.commit()
    owner = make.admin(role='admin', tenant_id=other.id)
    client = TestClient(app)
    response = client.patch('/tenant/v1/site', json={'currency': 'INR'}, headers=headers(owner))
    assert response.status_code == 200, response.text
    session.expire_all()
    assert session.get(SiteSettings, other.id).currency == 'INR'
    assert session.get(SiteSettings, 'jjtv').currency == 'USD'
    created = client.post('/platform/v1/tenants', headers=headers(make.platform_admin()),
        json={'code': 'currency-created', 'name': 'New currency site', 'currency': 'JPY', 'timezone': 'Asia/Tokyo'})
    assert created.status_code == 201, created.text
    assert created.json()['site']['currency'] == 'JPY'


def test_pending_refund_keeps_its_original_site_currency(session, currency_env, monkeypatch):
    user, actor, _ = currency_env
    order = purchase(session, user)
    pay(session, order)
    original_remote = commerce.stripe_request
    def remote(db, code, path, *args, **kwargs):
        if path == '/refunds': return {'id': 're_currency_pending', 'status': 'pending'}
        return original_remote(db, code, path, *args, **kwargs)
    monkeypatch.setattr(commerce, 'stripe_request', remote)
    assert commerce.request_refund(session, order.order_no, actor.id, 'test', 'currency-pending-refund')['status'] == 'pending'
    response = TestClient(app).patch('/tenant/v1/site', headers=headers(actor), json={'currency': 'EUR'})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'site_currency_has_orders'
    session.expire_all()
    assert session.get(Order, order.id).currency == session.get(SiteSettings, 'jjtv').currency == 'USD'


def checkout_worker(factory, user_id, email):
    with factory() as db:
        db.info['content_tenant_id'] = 'jjtv'
        return commerce.checkout(db, user_id=user_id, email=email, product_type='coin_pack',
            code='starter', idempotency_key='currency-concurrent-checkout')


@pytest.mark.parametrize('endpoint', ['tenant', 'platform'])
def test_order_creation_wins_lock_then_currency_change_is_rejected(session, currency_env, make, monkeypatch, endpoint):
    user, actor, _ = currency_env
    user_id, email = user.id, user.email
    factory = sessionmaker(bind=session.bind, expire_on_commit=False)
    locked, release, patch_started = Event(), Event(), Event()
    original_snapshot = commerce.product_snapshot
    def snapshot(*args, **kwargs):
        locked.set()
        assert release.wait(10), 'checkout fixture was not released'
        return original_snapshot(*args, **kwargs)
    monkeypatch.setattr(commerce, 'product_snapshot', snapshot)
    client = TestClient(app)
    # Resolve credentials before starting concurrent transactions.
    request_headers = headers(actor if endpoint == 'tenant' else make.platform_admin())
    request_path = '/tenant/v1/site' if endpoint == 'tenant' else '/platform/v1/tenants/jjtv'
    request_body = {'currency': 'EUR'}
    if endpoint == 'platform': request_body['version'] = session.get(Tenant, 'jjtv').version
    def change():
        patch_started.set()
        return client.patch(request_path, json=request_body, headers=request_headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        buying = pool.submit(checkout_worker, factory, user_id, email)
        assert locked.wait(10)
        changing = pool.submit(change)
        assert patch_started.wait(10)
        try:
            with pytest.raises(FutureTimeout): changing.result(timeout=0.2)
        finally: release.set()
        purchase_result = buying.result(timeout=15)
        response = changing.result(timeout=15)
    assert response.status_code == 409, response.text
    session.expire_all()
    order = session.scalar(select(Order).where(Order.order_no == purchase_result['order_no']))
    assert order.currency == session.get(SiteSettings, 'jjtv').currency == 'USD'


def test_currency_change_wins_lock_then_old_price_cannot_create_a_mixed_order(session, currency_env, monkeypatch):
    user, actor, _ = currency_env
    user_id, email = user.id, user.email
    factory = sessionmaker(bind=session.bind, expire_on_commit=False)
    locked, release, checkout_started = Event(), Event(), Event()
    original_guard = sites_router.assert_currency_change_allowed
    def guard(*args, **kwargs):
        original_guard(*args, **kwargs)
        locked.set()
        assert release.wait(10), 'currency fixture was not released'
    monkeypatch.setattr(sites_router, 'assert_currency_change_allowed', guard)
    client = TestClient(app)
    request_headers = headers(actor)
    def buying():
        checkout_started.set()
        return checkout_worker(factory, user_id, email)
    with ThreadPoolExecutor(max_workers=2) as pool:
        changing = pool.submit(client.patch, '/tenant/v1/site', json={'currency': 'EUR'}, headers=request_headers)
        assert locked.wait(10)
        purchase_future = pool.submit(buying)
        assert checkout_started.wait(10)
        try:
            with pytest.raises(FutureTimeout): purchase_future.result(timeout=0.2)
        finally: release.set()
        assert changing.result(timeout=15).status_code == 200
        with pytest.raises(BillingError, match='currency'):
            purchase_future.result(timeout=15)
    session.expire_all()
    assert session.get(SiteSettings, 'jjtv').currency == 'EUR'
    assert session.scalar(select(func.count()).select_from(Order)) == 0


def test_first_concurrent_lock_atomically_provisions_missing_settings(session):
    assert session.get(SiteSettings, 'jjtv') is None
    factory = sessionmaker(bind=session.bind, expire_on_commit=False)
    first_locked, release, second_started = Event(), Event(), Event()
    def first():
        with factory() as db:
            row = lock_site_settings(db, 'jjtv')
            first_locked.set()
            assert release.wait(10)
            value = row.currency
            db.commit()
            return value
    def second():
        with factory() as db:
            second_started.set()
            row = lock_site_settings(db, 'jjtv')
            value = row.currency
            db.commit()
            return value
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first)
        assert first_locked.wait(10)
        two = pool.submit(second)
        assert second_started.wait(10)
        try:
            with pytest.raises(FutureTimeout): two.result(timeout=0.2)
        finally: release.set()
        assert one.result(timeout=15) == two.result(timeout=15) == 'USD'
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(SiteSettings).where(SiteSettings.tenant_id == 'jjtv')) == 1



def missing_billing_membership(db):
    db.execute(delete(BillingSettings).where(BillingSettings.tenant_id == 'jjtv'))
    plan = Plan(tenant_id='jjtv', code='monthly', name_en='Monthly')
    db.add(plan)
    db.flush()
    db.add(PlanVersion(tenant_id='jjtv', plan_id=plan.id, version=1, price_cents=500, currency='USD'))
    db.commit()
    assert db.scalar(select(BillingSettings).where(BillingSettings.tenant_id == 'jjtv')) is None


def member_checkout_worker(factory, user_id, email):
    with factory() as db:
        db.info['content_tenant_id'] = 'jjtv'
        return commerce.checkout(db, user_id=user_id, email=email, product_type='membership',
            code='monthly', idempotency_key='currency-first-member-checkout')


def test_missing_billing_settings_member_order_retains_lock_until_first_order_commit(session, currency_env, monkeypatch):
    user, actor, _ = currency_env
    missing_billing_membership(session)
    user_id, email = user.id, user.email
    factory = sessionmaker(bind=session.bind, expire_on_commit=False)
    ready_to_create_order, release = Event(), Event()
    original_select_account = commerce.select_account
    def select_account(*args, **kwargs):
        # Preserve the entire real product_snapshot -> billing_mode -> lazy
        # settings initialization path, and every helper before Order creation.
        result = original_select_account(*args, **kwargs)
        ready_to_create_order.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(commerce, 'select_account', select_account)
    client, owner_headers = TestClient(app), headers(actor)
    with ThreadPoolExecutor(max_workers=2) as pool:
        buying = pool.submit(member_checkout_worker, factory, user_id, email)
        assert ready_to_create_order.wait(10)
        changing = pool.submit(client.patch, '/tenant/v1/site', json={'currency': 'EUR'}, headers=owner_headers)
        try:
            with pytest.raises(FutureTimeout): changing.result(timeout=0.2)
        finally: release.set()
        bought = buying.result(timeout=15)
        response = changing.result(timeout=15)
    assert response.status_code == 409 and response.json()['detail']['code'] == 'site_currency_has_orders'
    session.expire_all()
    order = session.scalar(select(Order).where(Order.order_no == bought['order_no']))
    assert order.currency == session.get(SiteSettings, 'jjtv').currency == 'USD'
    assert session.scalar(select(BillingSettings).where(BillingSettings.tenant_id == 'jjtv')) is not None


def test_currency_change_precedes_missing_billing_settings_member_order_without_mixing(session, currency_env, monkeypatch):
    user, actor, _ = currency_env
    missing_billing_membership(session)
    user_id, email = user.id, user.email
    factory = sessionmaker(bind=session.bind, expire_on_commit=False)
    locked, release = Event(), Event()
    original_guard = sites_router.assert_currency_change_allowed
    def guard(*args, **kwargs):
        original_guard(*args, **kwargs)
        locked.set()
        assert release.wait(10)
    monkeypatch.setattr(sites_router, 'assert_currency_change_allowed', guard)
    client, owner_headers = TestClient(app), headers(actor)
    with ThreadPoolExecutor(max_workers=2) as pool:
        changing = pool.submit(client.patch, '/tenant/v1/site', json={'currency': 'EUR'}, headers=owner_headers)
        assert locked.wait(10)
        buying = pool.submit(member_checkout_worker, factory, user_id, email)
        try:
            with pytest.raises(FutureTimeout): buying.result(timeout=0.2)
        finally: release.set()
        assert changing.result(timeout=15).status_code == 200
        with pytest.raises(BillingError, match='currency'):
            buying.result(timeout=15)
    session.expire_all()
    assert session.get(SiteSettings, 'jjtv').currency == 'EUR'
    assert session.scalar(select(func.count()).select_from(Order)) == 0
