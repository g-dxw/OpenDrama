import copy
from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.account import service as account_service
from app.modules.billing import commerce, creem, creem_subscriptions, service
from app.modules.billing.commerce_models import CommerceSettlement, GatewayAccount, MembershipGrant
from app.modules.billing.models import Order, Plan, PlanVersion, Subscription
from app.modules.common.models import AuditLog
from app.modules.ops.managed_settings import encrypt_secret
from app.modules.wallet.models import WalletAccount


@pytest.fixture
def recurring(session, make, monkeypatch):
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    buyer = make.user("creem-renewal@example.com")
    plan = Plan(code="monthly", name_en="Monthly VIP")
    session.add(plan)
    session.flush()
    version = PlanVersion(plan_id=plan.id, version=1, price_cents=1000, currency="USD", benefits={}, base_coins=2)
    account = GatewayAccount(
        provider="creem", code="creem_test", name="Creem", status="active", identity="f" * 64,
        config={"mode": "test"}, credentials={"api_key": encrypt_secret("creem-test-key")},
    )
    session.add_all([version, account])
    session.flush()
    order = Order(
        order_no="creem-initial", user_id=buyer.id, product_type="membership", plan_version_id=version.id,
        account_code=account.code, provider="creem", amount_cents=version.price_cents, currency="USD",
        billing_mode="subscription", status="created", checkout_key="creem-initial-checkout",
        product_snapshot={"code": "monthly", "price_cents": 1000, "currency": "USD", "base_coins": 2,
                          "version_id": version.id, "billing_mode": "subscription"},
        checkout_context={"gateway_mode": "test", "external_product_id": "prod_month", "provider_checkout_id": "ch_first",
                          "initial_transaction_id": "tran_first", "provider_initial_order_id": "ord_first"},
    )
    session.add(order)
    session.commit()
    return account, order, buyer


@pytest.fixture
def period():
    start = commerce.now().replace(microsecond=0) - timedelta(hours=1)
    return start, start + timedelta(days=30)


def invoice(trade, period, *, provider_order="ord_first", amount=1000, tax=210, mode="test"):
    start, end = period
    return {
        "id": trade, "mode": mode, "object": "transaction", "amount": amount, "amount_paid": amount + tax,
        "tax_amount": tax, "currency": "USD", "status": "paid", "type": "invoice", "order": provider_order,
        "subscription": "sub_month", "period_start": int(start.timestamp() * 1000),
        "period_end": int(end.timestamp() * 1000),
    }


def subscription(period, *, payment=None, status="active", updated=None):
    event = {
        "id": "sub_month", "mode": "test", "product": {"id": "prod_month"}, "status": status,
        "current_period_start_date": period[0].isoformat(), "current_period_end_date": period[1].isoformat(),
        "updated_at": (updated or commerce.now()).isoformat(),
    }
    if payment:
        event.update(last_transaction_id=payment["id"], last_transaction=payment)
    return event


def settle_first(session, recurring, period):
    account, order, _ = recurring
    event = subscription(period, payment=invoice("tran_first", period))
    return creem_subscriptions.handle_event(session, account, order, "subscription.paid", event)


def test_original_transaction_preserves_price_and_deduplicates_billing_period(session, recurring, period):
    account, order, buyer = recurring
    event = subscription(period, payment=invoice("tran_first", period))
    first = creem_subscriptions.handle_event(session, account, order, "subscription.paid", event)
    second = creem_subscriptions.handle_event(session, account, order, "subscription.paid", event)
    assert first == {"order_no": order.order_no, "status": "paid"}
    assert second == {"order_no": order.order_no, "replayed": True}
    duplicate_period = subscription(period, payment=invoice("tran_distinct", period))
    with pytest.raises(service.BillingError, match="already settled"):
        creem_subscriptions.handle_event(session, account, order, "subscription.paid", duplicate_period)
    session.rollback()

    assert order.amount_cents == 1000 and order.checkout_context["provider_amount_paid"] == 1210
    assert order.checkout_context["provider_tax_minor"] == 210
    assert len(list(session.scalars(select(CommerceSettlement)))) == 1
    grant = session.scalar(select(MembershipGrant).where(MembershipGrant.order_id == order.id))
    assert grant.status == "active" and (grant.starts_at, grant.ends_at) == period
    assert session.get(WalletAccount, buyer.id).balance == 2
    assert service.membership_detail(session, buyer.id)["has_renewing_subscription"]


def test_distinct_transaction_cannot_credit_overlapping_period(session, recurring, period):
    account, order, buyer = recurring
    settle_first(session, recurring, period)
    overlapping = period[0] + timedelta(days=7), period[1] + timedelta(days=7)
    event = subscription(overlapping, payment=invoice("tran_overlap", overlapping, provider_order="ord_overlap"))
    with pytest.raises(service.BillingError, match="overlaps"):
        creem_subscriptions.handle_event(session, account, order, "subscription.paid", event)
    session.rollback()
    assert session.get(WalletAccount, buyer.id).balance == 2
    assert len(list(session.scalars(select(CommerceSettlement)))) == 1


@pytest.mark.parametrize("updates", [{"amount": 999}, {"currency": "EUR"}, {"mode": "prod"}, {"amount_paid": 0}])
def test_subscription_charge_must_match_original_terms(session, recurring, period, updates):
    account, order, buyer = recurring
    txn = {**invoice("tran_first", period), **updates}
    event = subscription(period, payment=txn)
    with pytest.raises(service.BillingError, match="frozen subscription"):
        creem_subscriptions.handle_event(session, account, order, "subscription.paid", event)
    session.rollback()
    assert order.status == "created" and session.get(WalletAccount, buyer.id).balance == 0


def test_first_checkout_retained_when_renewal_arrives_first(session, recurring, period, monkeypatch):
    account, order, buyer = recurring
    following = period[1], period[1] + timedelta(days=30)
    expected = {"tran_first": invoice("tran_first", period),
                "tran_next": invoice("tran_next", following, provider_order="ord_next")}
    requests = []

    def get_transaction(**kwargs):
        assert kwargs["mode"] == "test"
        requests.append(kwargs["transaction_id"])
        return expected[kwargs["transaction_id"]]

    monkeypatch.setattr(creem, "get_transaction", get_transaction)
    event = {**subscription(following), "last_transaction_id": "tran_next"}
    result = creem_subscriptions.handle_event(session, account, order, "subscription.paid", event)
    assert result["status"] == "paid" and result["order_no"] != order.order_no
    assert requests == ["tran_next", "tran_first"]
    renewal = session.scalar(select(Order).where(Order.order_no == result["order_no"]))
    assert renewal.checkout_context["creem_initial_order_no"] == order.order_no
    assert order.status == "paid" and renewal.product_snapshot == order.product_snapshot
    row = session.scalar(select(Subscription).where(Subscription.creem_subscription_id == "sub_month"))
    assert row.plan_version_id == order.plan_version_id
    assert (row.current_period_start, row.current_period_end) == following
    assert commerce.grant_access_until(session, buyer.id) == following[1]
    assert session.get(WalletAccount, buyer.id).balance == 4


def test_customer_cancellation_keeps_access_and_ignores_stale_lifecycle(session, recurring, period, monkeypatch):
    account, order, buyer = recurring
    settle_first(session, recurring, period)
    remote = subscription(period)
    transitions = []

    def cancel(**kwargs):
        assert kwargs.get("immediate", False) is False
        transitions.append(kwargs["subscription_id"])
        remote.update(status="scheduled_cancel", updated_at=(commerce.now() + timedelta(minutes=1)).isoformat())
        return copy.copy(remote)

    monkeypatch.setattr(creem, "get_subscription", lambda **_kwargs: copy.copy(remote))
    monkeypatch.setattr(creem, "cancel_subscription", cancel)
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + account_service._issue_session(buyer)}
    assert client.post("/v1/billing/cancel", headers=headers).status_code == 200
    assert client.post("/v1/billing/cancel", headers=headers).status_code == 200
    assert transitions == ["sub_month"]

    session.expire_all()
    creem_subscriptions.handle_event(session, account, order, "subscription.active", subscription(period))
    membership = service.active_membership(session, buyer.id)
    assert membership["active"] and membership["canceling"] and datetime.fromisoformat(membership["until"]) == period[1]
    creem_subscriptions.assert_can_start(session, buyer.id)
    db_row = service.active_subscription(session, buyer.id)
    assert db_row.cancellation_context["creem_state"] == "scheduled_cancel"
    remote["status"] = "active"
    with pytest.raises(service.BillingError, match="renewing Creem subscription"):
        creem_subscriptions.assert_can_start(session, buyer.id)


def test_expired_subscription_does_not_prove_renewal_is_disabled(session, recurring, period, monkeypatch):
    _, _, buyer = recurring
    settle_first(session, recurring, period)
    remote = subscription(period, status="expired")
    attempts = []

    def cancel(**kwargs):
        attempts.append(kwargs["subscription_id"])
        return copy.copy(remote)

    monkeypatch.setattr(creem, "get_subscription", lambda **_kwargs: copy.copy(remote))
    monkeypatch.setattr(creem, "cancel_subscription", cancel)
    with pytest.raises(service.BillingError, match="renewing Creem subscription"):
        creem_subscriptions.assert_can_start(session, buyer.id)
    with pytest.raises(service.BillingError, match="not been confirmed"):
        service.cancel_subscription(session, buyer.id)
    session.rollback()
    assert attempts == ["sub_month"]
    assert not service.active_membership(session, buyer.id)["canceling"]


def test_admin_revoke_waits_for_provider_and_fences_late_invoice(session, recurring, period, make, monkeypatch):
    account, order, buyer = recurring
    admin = make.admin(role="admin")
    settle_first(session, recurring, period)
    remote = subscription(period)
    calls = []

    def cancel(**kwargs):
        assert kwargs["immediate"] is True
        calls.append(kwargs["subscription_id"])
        if len(calls) == 1:
            raise creem.CreemHTTPError(503)
        remote.update(status="canceled", updated_at=(commerce.now() + timedelta(minutes=1)).isoformat())
        return copy.copy(remote)

    monkeypatch.setattr(creem, "get_subscription", lambda **_kwargs: copy.copy(remote))
    monkeypatch.setattr(creem, "cancel_subscription", cancel)
    args = {"user_id": buyer.id, "mode": "now", "reason": "test revoke", "actor_id": admin.id}
    with pytest.raises(service.BillingError, match="unconfirmed"):
        service.revoke_membership(session, **args)
    session.rollback()
    assert service.active_membership(session, buyer.id)["active"]
    assert service.revoke_membership(session, **args)["creem_canceled"]
    assert not service.active_membership(session, buyer.id)["active"]
    audit = session.scalar(select(AuditLog).where(AuditLog.action == "membership_revoke"))
    assert audit.payload["revoked_creem_subscription_bindings"] == [
        {"account_code": account.code, "subscription_id": "sub_month"},
    ]

    following = period[1], period[1] + timedelta(days=30)
    late = subscription(following, payment=invoice("tran_late", following, provider_order="ord_late"))
    creem_subscriptions.handle_event(session, account, order, "subscription.paid", late)
    assert set(session.scalars(select(MembershipGrant.status))) == {"revoked"}
    assert service.active_subscription(session, buyer.id) is None
