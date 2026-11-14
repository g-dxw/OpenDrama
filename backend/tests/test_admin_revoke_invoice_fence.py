"""An administrative immediate revoke survives delayed paid subscription invoices."""
from copy import deepcopy
from datetime import datetime, timezone
import time

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.modules.billing import commerce as c
from app.modules.billing import service as billing
from app.modules.billing.commerce_models import CommerceRefund, CommerceSettlement, MembershipGrant
from app.modules.billing.models import Order, Subscription
from app.modules.billing.service import BillingError
from app.modules.common.models import AuditLog
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount
from tests.test_commerce import commercial, pay
from tests.test_commerce_lifecycle import invoice, lifecycle, subscription_order

UTC = timezone.utc


@pytest.fixture
def revoked_invoice_case(session, commercial):
    user, actor, _ = commercial
    first = subscription_order(session, user)
    initial = invoice(first)
    c.process_event(session, "env", initial)
    binding = session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    stamp = int(time.time())
    delayed = invoice(first, number="in_delayed_admin_revoke")
    obj = delayed["data"]["object"]
    obj["billing_reason"] = "subscription_cycle"
    obj["status_transitions"] = {"paid_at":stamp-10}
    obj["lines"]["data"][0]["period"] = {"start":stamp-60,"end":stamp+60*86400}
    delayed["created"] = stamp-5
    return user, actor, first, binding, delayed


def revoke(db, user, actor, mode="now"):
    return billing.revoke_membership(db, user_id=user.id, mode=mode,
        reason="Administrative access withdrawal", actor_id=actor.id)


def grant_for_invoice(db, invoice_id):
    order = db.scalar(select(Order).where(Order.invoice_id == invoice_id))
    return order, db.scalar(select(MembershipGrant).where(MembershipGrant.order_id == order.id))


@pytest.mark.parametrize("future_period", [False, True])
def test_delayed_paid_invoice_cannot_restore_immediately_revoked_membership(
    session, revoked_invoice_case, monkeypatch, future_period,
):
    user, actor, _, _, delayed = revoked_invoice_case
    stamp = int(time.time())
    if future_period:
        delayed["data"]["object"]["lines"]["data"][0]["period"]["start"] = stamp+60
    revoke(session, user, actor)
    assert billing.access_until(session, user.id) is None
    for kind in ("invoice.paid", "invoice.payment_succeeded", "invoice.paid"):
        delayed["type"] = kind
        c.process_event(session, "env", delayed)
    order, grant = grant_for_invoice(session, delayed["data"]["object"]["id"])
    assert order.status == "paid"
    assert grant.status == "revoked"
    assert session.scalar(select(func.count()).select_from(CommerceSettlement)) == 2
    assert session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.biz_type == "purchase")) == 2
    assert session.get(WalletAccount, user.id).balance == 50
    monkeypatch.setattr(c, "now", lambda:datetime.fromtimestamp(stamp+120, UTC))
    assert billing.access_until(session, user.id) is None


@pytest.mark.parametrize("path", ["admin", "user"])
def test_period_end_cancellation_does_not_apply_an_administrative_cutoff(
    session, revoked_invoice_case, path,
):
    user, actor, _, _, delayed = revoked_invoice_case
    if path == "admin":revoke(session, user, actor, mode="period_end")
    else:billing.cancel_subscription(session, user.id)
    c.process_event(session, "env", delayed)
    _, grant = grant_for_invoice(session, delayed["data"]["object"]["id"])
    assert grant.status == "active"
    assert billing.access_until(session, user.id) == grant.ends_at


def test_failed_provider_revoke_does_not_install_an_access_cutoff(
    session, revoked_invoice_case, monkeypatch,
):
    user, actor, _, _, delayed = revoked_invoice_case
    original = c.stripe_request
    def unavailable(db, code, path, data=None, method="POST", idempotency_key=None):
        if method == "DELETE" and path.startswith("/subscriptions/"):
            raise BillingError(503, "provider unavailable")
        return original(db, code, path, data, method, idempotency_key)
    monkeypatch.setattr(c, "stripe_request", unavailable)
    with pytest.raises(BillingError, match="provider unavailable"):
        revoke(session, user, actor)
    session.rollback()
    assert session.scalar(select(AuditLog).where(AuditLog.action == "membership_revoke")) is None
    c.process_event(session, "env", delayed)
    _, grant = grant_for_invoice(session, delayed["data"]["object"]["id"])
    assert grant.status == "active" and billing.access_until(session, user.id) is not None


def test_late_old_invoice_preserves_a_new_one_time_pass(session, revoked_invoice_case):
    user, actor, _, _, delayed = revoked_invoice_case
    revoke(session, user, actor)
    billing.get_billing_settings(session).billing_mode = "one_time"
    session.commit()
    result = c.checkout(session, user_id=user.id, email=user.email, product_type="membership",
        code="monthly", idempotency_key="post-revoke-pass")
    new_order = session.scalar(select(Order).where(Order.order_no == result["order_no"]))
    pay(session, new_order)
    expected = billing.access_until(session, user.id)
    c.process_event(session, "env", delayed)
    _, old_grant = grant_for_invoice(session, delayed["data"]["object"]["id"])
    new_grant = session.scalar(select(MembershipGrant).where(MembershipGrant.order_id == new_order.id))
    assert old_grant.status == "revoked" and new_grant.status == "active"
    assert billing.access_until(session, user.id) == expected


@pytest.mark.parametrize("account", ["env", "other_merchant"])
def test_late_old_invoice_cannot_override_a_new_subscription_binding(
    session, revoked_invoice_case, account,
):
    user, actor, _, old_binding, delayed = revoked_invoice_case
    revoke(session, user, actor)
    if account != "env":
        c.save_account(session, account, dict(name="Other", status="active", secret_key="sk_test_other",
            webhook_secret="whsec_other"), actor.id)
    result = c.checkout(session, user_id=user.id, email=user.email, product_type="membership",
        code="monthly", account_code=account, idempotency_key="post-revoke-subscription")
    new_order = session.scalar(select(Order).where(Order.order_no == result["order_no"]))
    stripe_id = "sub_new_after_revoke_"+account
    fresh = invoice(new_order, subscription=stripe_id, number="in_new_after_revoke")
    c.process_event(session, account, fresh)
    expected = billing.access_until(session, user.id)
    c.process_event(session, "env", delayed)
    _, old_grant = grant_for_invoice(session, delayed["data"]["object"]["id"])
    _, fresh_grant = grant_for_invoice(session, fresh["data"]["object"]["id"])
    assert old_grant.status == "revoked" and fresh_grant.status == "active"
    assert billing.access_until(session, user.id) == expected
    current = session.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == "active"))
    assert (current.account_code, current.stripe_subscription_id) == (account, stripe_id)


def test_lifecycle_resume_does_not_erase_the_administrative_access_fence(session, revoked_invoice_case):
    user, actor, first, _, delayed = revoked_invoice_case
    revoke(session, user, actor)
    c.process_event(session, "env", lifecycle(first, kind="customer.subscription.updated",
        created=int(time.time())+1, cancel=False))
    c.process_event(session, "env", delayed)
    _, grant = grant_for_invoice(session, delayed["data"]["object"]["id"])
    assert grant.status == "revoked" and billing.access_until(session, user.id) is None


def test_admin_revoke_and_invoice_share_the_wallet_serialization_lock(
    session, revoked_invoice_case, engine, monkeypatch,
):
    user, actor, _, _, delayed = revoked_invoice_case
    original = c.stripe_request
    factory = sessionmaker(bind=engine)
    blocked = []
    def remote(db, code, path, data=None, method="POST", idempotency_key=None):
        if method == "DELETE" and path.startswith("/subscriptions/"):
            with factory() as competing:
                competing.execute(text("SET LOCAL lock_timeout='250ms'"))
                with pytest.raises(DBAPIError) as error:
                    c.process_event(competing, "env", deepcopy(delayed))
                assert (getattr(error.value.orig,"sqlstate",None) or getattr(error.value.orig,"pgcode",None)) == "55P03"
                blocked.append(True)
        return original(db, code, path, data, method, idempotency_key)
    monkeypatch.setattr(c, "stripe_request", remote)
    revoke(session, user, actor)
    assert blocked == [True]
    c.process_event(session, "env", delayed)
    _, grant = grant_for_invoice(session, delayed["data"]["object"]["id"])
    assert grant.status == "revoked"


def test_another_merchant_audit_cannot_revoke_this_binding(session, revoked_invoice_case):
    user, actor, _, binding, delayed = revoked_invoice_case
    # Imported/audited provider IDs are merchant scoped, even if a legacy index
    # prevents creating two live local bindings with the same ID.
    session.add(AuditLog(actor_type="admin", actor_id=actor.id, action="membership_revoke",
        target_type="user", target_id=user.id, payload=dict(mode="now", account_code="other_merchant",
        stripe_subscription_id=binding.stripe_subscription_id, cutoff_at=datetime.now(UTC).isoformat())))
    session.commit()
    c.process_event(session, "env", delayed)
    _, grant = grant_for_invoice(session, delayed["data"]["object"]["id"])
    assert grant.status == "active" and billing.access_until(session, user.id) is not None


@pytest.mark.parametrize("history_source", ["subscription_row", "original_order_only"])
@pytest.mark.parametrize("new_manual_pass", [False, True])
@pytest.mark.parametrize("existing_grant", ["still_active", "expired"])
def test_current_manual_pass_revoke_fences_historical_subscription_bindings(
    session, revoked_invoice_case, history_source, new_manual_pass, existing_grant,
):
    user, actor, _, historical, delayed = revoked_invoice_case
    # Restore a valid historical closed binding without an earlier admin cutoff.
    historical.status = "canceled"
    historical.canceled_at = datetime.now(UTC)
    if existing_grant == "expired":
        historical.current_period_end = datetime.fromtimestamp(int(time.time())-1, UTC)
        prior = session.scalar(select(MembershipGrant).where(MembershipGrant.order_id == revoked_invoice_case[2].id))
        prior.ends_at = historical.current_period_end
    session.commit()
    if history_source == "original_order_only":
        session.delete(historical)
        session.commit()
    billing.grant_membership(session, user_id=user.id, plan_code="monthly", days=3,
        mode="extend", reason="Manual pass on a separate row", actor_id=actor.id)
    current = session.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == "active"))
    assert current.stripe_subscription_id is None
    revoke(session, user, actor)
    expected = None
    if new_manual_pass:
        billing.grant_membership(session, user_id=user.id, plan_code="monthly", days=5,
            mode="extend", reason="New manual pass after cutoff", actor_id=actor.id)
        current = session.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == "active"))
        expected = billing.access_until(session, user.id)
    c.process_event(session, "env", delayed)
    _, grant = grant_for_invoice(session, delayed["data"]["object"]["id"])
    assert grant.status == "revoked"
    assert billing.access_until(session, user.id) == expected
    if new_manual_pass:
        session.refresh(current)
        assert current.status == "active" and current.stripe_subscription_id is None
    else:
        assert session.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == "active")) is None
    assert session.scalar(select(func.count()).select_from(CommerceSettlement)) == 2
    assert session.get(WalletAccount, user.id).balance == 50


@pytest.mark.parametrize("outcome", ["failed", "canceled"])
def test_refund_release_does_not_reactivate_an_administratively_revoked_subscription(
    session, revoked_invoice_case, monkeypatch, outcome,
):
    user, actor, first, _, _ = revoked_invoice_case
    original = c.stripe_request
    def pending(db, code, path, data=None, method="POST", idempotency_key=None):
        if path == "/refunds":return dict(id="re_pending_admin_cutoff",status="pending",amount=int(data["amount"]),currency="usd")
        return original(db,code,path,data,method,idempotency_key)
    monkeypatch.setattr(c,"stripe_request",pending)
    c.request_refund(session,first.order_no,actor.id,"Unused subscription period","admin-cutoff-refund")
    grant = session.scalar(select(MembershipGrant).where(MembershipGrant.order_id == first.id))
    assert grant.status == "refund_pending"
    assert session.get(WalletAccount,user.id).balance == 0
    billing.grant_membership(session,user_id=user.id,plan_code="monthly",days=3,mode="extend",
        reason="Manual access while refund is pending",actor_id=actor.id)
    revoke(session,user,actor)
    assert billing.access_until(session,user.id) is None
    refund = session.scalar(select(CommerceRefund).where(CommerceRefund.order_id == first.id))
    c.finalize_refund(session,refund,outcome);session.commit()
    assert refund.status == outcome and first.status == "paid"
    assert session.get(WalletAccount,user.id).balance == 25
    assert grant.status == "revoked"
    assert billing.access_until(session,user.id) is None


@pytest.mark.parametrize("actor_type", ["user", "system"])
def test_non_admin_audit_is_not_an_administrative_withdrawal(session, revoked_invoice_case, actor_type):
    user, _, _, binding, delayed = revoked_invoice_case
    session.add(AuditLog(actor_type=actor_type, actor_id=user.id if actor_type == "user" else None,
        action="membership_revoke", target_type="user", target_id=user.id,
        payload=dict(mode="now",cutoff_at=datetime.now(UTC).isoformat(),revoked_subscription_bindings=[
            dict(account_code=binding.account_code,stripe_subscription_id=binding.stripe_subscription_id)])))
    session.commit()
    c.process_event(session,"env",delayed)
    _, grant = grant_for_invoice(session,delayed["data"]["object"]["id"])
    assert grant.status == "active" and billing.access_until(session,user.id) is not None
