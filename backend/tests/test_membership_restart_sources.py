"""Manual restart must not pretend to overwrite source-tracked paid membership.

Stripe is stubbed; the actual checkout snapshot, settlement, PostgreSQL locks,
admin permission gate and membership aggregation remain in use.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.main import app
from app.modules.billing import commerce
from app.modules.billing import service as billing
from app.modules.billing.commerce_models import MembershipGrant
from app.modules.billing.models import Order, Subscription
from app.modules.common.models import AuditLog

UTC = timezone.utc


@pytest.fixture
def checkout_source(session, make, monkeypatch):
    moment = datetime.now(UTC).replace(microsecond=0)
    monkeypatch.setattr(billing, "_now", lambda: moment)
    monkeypatch.setattr(commerce, "now", lambda: moment)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_membership_restart")
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    calls = []

    def stripe(db, account, path, data=None, method="POST", idempotency_key=None):
        calls.append((path, method))
        if path == "/prices":
            return {"id": "price_restart_source"}
        if path == "/checkout/sessions":
            return {
                "id": "cs_restart_source",
                "url": "https://checkout.stripe.com/test/restart-source",
                "status": "open",
                "payment_status": "unpaid",
            }
        raise AssertionError("Unexpected provider request: " + path)

    monkeypatch.setattr(commerce, "stripe_request", stripe)
    monkeypatch.setattr(
        commerce, "_stripe_identity",
        lambda secret: ("acct_restart_source", False, {"id": "acct_restart_source"}),
    )
    billing.ensure_default_plans(session)
    actor = make.admin(role="admin")
    user = make.user(email="restart-source@example.com")
    result = commerce.checkout(
        session, user_id=user.id, email=user.email,
        product_type="membership", code="monthly", idempotency_key="restart-source",
    )
    order = session.scalar(select(Order).where(Order.order_no == result["order_no"]))
    assert order.billing_mode == "one_time"
    return user, actor, order, moment, calls


@pytest.fixture
def paid_source(session, checkout_source):
    user, actor, order, moment, calls = checkout_source
    commerce.process_event(session, order.account_code, {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": order.external_txn_id, "mode": "payment", "payment_status": "paid",
            "amount_total": order.amount_cents, "currency": order.currency.lower(),
            "payment_intent": "pi_restart_source",
            "metadata": {"commerce_order_no": order.order_no},
        }},
    })
    grant = session.scalar(select(MembershipGrant).where(MembershipGrant.order_id == order.id))
    assert grant.ends_at == moment + timedelta(days=30)
    return user, actor, order, grant, moment, calls


def order_facts(order):
    return deepcopy({key: getattr(order, key) for key in (
        "product_snapshot", "plan_version_id", "amount_cents", "currency", "billing_mode",
        "account_code", "external_txn_id", "payment_intent_id", "checkout_key", "status",
    )})


def grant_facts(grant):
    return grant.starts_at, grant.ends_at, grant.status, grant.access_consumed


def adjustments(session, user_id):
    return session.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.target_type == "user", AuditLog.target_id == user_id,
        AuditLog.action == "membership_grant",
    ))


def staff_headers(client, actor):
    response = client.post("/admin/v1/auth/login", json={
        "tenant_code": "jjtv", "username": actor.username, "password": "pass-1234",
    })
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def adjust(session, user, actor, mode="restart"):
    return billing.grant_membership(
        session, user_id=user.id, plan_code="weekly", days=7, mode=mode,
        reason="Documented support adjustment", actor_id=actor.id,
    )


def test_restart_rejects_paid_checkout_source_without_false_success(session, paid_source):
    user, actor, order, grant, moment, calls = paid_source
    frozen_order, frozen_grant = order_facts(order), grant_facts(grant)
    assert billing.access_until(session, user.id) == moment + timedelta(days=30)
    before_calls = len(calls)
    with TestClient(app) as client:
        response = client.post(f"/admin/v1/users/{user.id}/membership", headers=staff_headers(client, actor), json={
            "plan_code": "weekly", "days": 7, "mode": "restart", "reason": "Reset request",
        })
    session.expire_all()
    effective = billing.access_until(session, user.id)
    assert response.status_code == 409, {"response": response.json(), "effective_until": str(effective)}
    assert "订单" in response.json()["detail"] and "顺延" in response.json()["detail"]
    assert effective == moment + timedelta(days=30)
    assert order_facts(session.get(Order, order.id)) == frozen_order
    assert grant_facts(session.get(MembershipGrant, grant.id)) == frozen_grant
    assert session.scalar(select(Subscription).where(Subscription.user_id == user.id)) is None
    assert adjustments(session, user.id) == 0
    assert len(calls) == before_calls


@pytest.mark.parametrize("status,start_days,end_days,consumed", [
    ("active", -1, 29, False),
    ("active", 10, 40, False),
    ("active", -1, 29, True),
    ("pending_refund", 0, 30, False),
    ("pending_refund", -31, -1, False),
])
def test_restart_preserves_live_queued_consumed_and_refund_frozen_sources(
    session, paid_source, status, start_days, end_days, consumed,
):
    user, actor, order, grant, moment, _ = paid_source
    grant.starts_at = moment + timedelta(days=start_days)
    grant.ends_at = moment + timedelta(days=end_days)
    grant.status, grant.access_consumed = status, consumed
    if status == "pending_refund":
        order.status = "refund_pending"
    session.commit()
    frozen_order, frozen_grant = order_facts(order), grant_facts(grant)
    with pytest.raises(billing.BillingError) as raised:
        adjust(session, user, actor)
    assert raised.value.status_code == 409
    session.rollback()
    assert order_facts(session.get(Order, order.id)) == frozen_order
    assert grant_facts(session.get(MembershipGrant, grant.id)) == frozen_grant
    assert adjustments(session, user.id) == 0


def test_extend_stacks_on_paid_access_without_changing_its_source(session, paid_source):
    user, actor, order, grant, moment, _ = paid_source
    frozen_order, frozen_grant = order_facts(order), grant_facts(grant)
    result = adjust(session, user, actor, mode="extend")
    assert datetime.fromisoformat(result["before_until"]) == moment + timedelta(days=30)
    assert datetime.fromisoformat(result["after_until"]) == moment + timedelta(days=37)
    assert billing.access_until(session, user.id) == moment + timedelta(days=37)
    assert order_facts(session.get(Order, order.id)) == frozen_order
    assert grant_facts(session.get(MembershipGrant, grant.id)) == frozen_grant
    assert adjustments(session, user.id) == 1


def test_extend_does_not_thaw_a_pending_refund(session, paid_source):
    user, actor, order, grant, moment, _ = paid_source
    grant.status, order.status = "pending_refund", "refund_pending"
    session.commit()
    frozen_order, frozen_grant = order_facts(order), grant_facts(grant)
    result = adjust(session, user, actor, mode="extend")
    assert result["before_until"] is None
    assert datetime.fromisoformat(result["after_until"]) == moment + timedelta(days=7)
    assert billing.access_until(session, user.id) == moment + timedelta(days=7)
    assert order_facts(session.get(Order, order.id)) == frozen_order
    assert grant_facts(session.get(MembershipGrant, grant.id)) == frozen_grant


@pytest.mark.parametrize("status,start_days,end_days", [
    ("active", -31, -1), ("revoked", 0, 30), ("refunded", 0, 30),
])
def test_restart_allows_only_expired_or_terminated_commercial_sources(
    session, paid_source, status, start_days, end_days,
):
    user, actor, order, grant, moment, _ = paid_source
    grant.starts_at, grant.ends_at = moment + timedelta(days=start_days), moment + timedelta(days=end_days)
    grant.status = status
    if status == "refunded":
        order.status = "refunded"
    session.commit()
    frozen_order, frozen_grant = order_facts(order), grant_facts(grant)
    result = adjust(session, user, actor)
    assert datetime.fromisoformat(result["after_until"]) == moment + timedelta(days=7)
    assert billing.access_until(session, user.id) == moment + timedelta(days=7)
    assert order_facts(session.get(Order, order.id)) == frozen_order
    assert grant_facts(session.get(MembershipGrant, grant.id)) == frozen_grant


def test_another_users_paid_source_does_not_block_restart(session, make, paid_source):
    user, actor, order, grant, moment, _ = paid_source
    other = make.user(email="separate-membership@example.com")
    frozen_order, frozen_grant = order_facts(order), grant_facts(grant)
    result = adjust(session, other, actor)
    assert datetime.fromisoformat(result["after_until"]) == moment + timedelta(days=7)
    assert billing.access_until(session, user.id) == moment + timedelta(days=30)
    assert billing.access_until(session, other.id) == moment + timedelta(days=7)
    assert order_facts(session.get(Order, order.id)) == frozen_order
    assert grant_facts(session.get(MembershipGrant, grant.id)) == frozen_grant


def test_staff_without_user_management_cannot_adjust_commercial_membership(session, make, paid_source):
    user, _, order, grant, _, _ = paid_source
    staff = make.admin(role="ops")
    frozen_order, frozen_grant = order_facts(order), grant_facts(grant)
    with TestClient(app) as client:
        response = client.post(f"/admin/v1/users/{user.id}/membership", headers=staff_headers(client, staff), json={
            "plan_code": "weekly", "days": 7, "mode": "restart", "reason": "Forbidden reset",
        })
    assert response.status_code == 403
    session.expire_all()
    assert order_facts(session.get(Order, order.id)) == frozen_order
    assert grant_facts(session.get(MembershipGrant, grant.id)) == frozen_grant
    assert adjustments(session, user.id) == 0


def test_restart_waits_for_inflight_paid_settlement_and_then_refuses(
    session, engine, checkout_source, monkeypatch,
):
    user, actor, order, moment, _ = checkout_source
    # Hold a real settlement just before its commit, while it owns the wallet lock.
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", session.flush)
        commerce.settle_order(session, order, "membership-reset-lock-test")
    started = Event()

    def reset_in_another_transaction():
        with Session(engine, expire_on_commit=False) as worker:
            started.set()
            try:
                result = adjust(worker, user, actor)
            except billing.BillingError as error:
                worker.rollback()
                return error.status_code
            return result

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(reset_in_another_transaction)
        try:
            assert started.wait(5)
            # A support adjustment must wait for payment's source facts to commit.
            import time
            time.sleep(0.15)
            assert not future.done()
        finally:
            session.commit()
        assert future.result(timeout=5) == 409
    session.expire_all()
    assert billing.access_until(session, user.id) == moment + timedelta(days=30)
    assert adjustments(session, user.id) == 0
