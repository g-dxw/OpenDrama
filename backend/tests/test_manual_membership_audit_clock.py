"""Manual membership audit ordering must use mutation time after the wallet mutex."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.modules.billing import commerce as c
from app.modules.billing import service as billing
from app.modules.billing.commerce_models import CoinSource, CommerceRefund, MembershipGrant
from app.modules.billing.service import BillingError
from app.modules.common.models import AuditLog
from app.modules.wallet.models import WalletAccount
from tests.test_commerce import commercial, membership_order, pay
from tests.test_commerce_lifecycle import invoice, subscription_order

UTC = timezone.utc


@pytest.mark.parametrize("read_path", ["eligibility", "request"])
def test_manual_transaction_started_before_payment_cannot_hide_later_adjustment(
    session, commercial, engine, monkeypatch, read_path,
):
    user, actor, calls = commercial
    order = membership_order(session, user)
    user_id, actor_id = user.id, actor.id
    started, begin_adjustment, waiting_for_wallet = Event(), Event(), Event()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    original_wallet_lock = c.wallet_lock
    manual_session = []
    def wallet_lock(db, owner):
        if manual_session and db is manual_session[0]:
            waiting_for_wallet.set()
        return original_wallet_lock(db, owner)
    monkeypatch.setattr(c, "wallet_lock", wallet_lock)
    def adjust():
        with factory() as db:
            manual_session.append(db)
            transaction_started = db.scalar(select(func.now()))
            db.execute(text("SELECT pg_sleep(0.03)"))  # Keep real host/DB clock jitter outside the ordering assertion.
            started.set()
            assert begin_adjustment.wait(10), "payment did not acquire the wallet lock"
            result = billing.grant_membership(db, user_id=user_id, plan_code="monthly", days=3,
                mode="extend", reason="Concurrent support extension", actor_id=actor_id)
            audit = db.scalar(select(AuditLog).where(AuditLog.action == "membership_grant"))
            return transaction_started, audit.created_at, datetime.fromisoformat(result["after_until"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(adjust)
        assert started.wait(10)
        original_wallet_lock(session, user_id)
        begin_adjustment.set()
        assert waiting_for_wallet.wait(10)
        assert not pending.done(), "manual adjustment bypassed the payment wallet mutex"
        pay(session, order)  # Commits the paid source, then the waiting adjustment proceeds.
        transaction_started, mutation_audit, pass_end = pending.result(timeout=15)
    session.refresh(order)
    grant = session.scalar(select(MembershipGrant).where(MembershipGrant.order_id == order.id))
    assert transaction_started < order.paid_at
    assert pass_end > grant.ends_at, "the support pass must actually include the purchased window"
    if read_path == "eligibility":
        eligibility = c.admin_order_detail(session, order.order_no)["refund_eligibility"]
        assert not eligibility["eligible"]
        assert "人工" in eligibility["reason"]
    else:
        with pytest.raises(BillingError, match="manually adjusted after purchase"):
            c.request_refund(session, order.order_no, actor_id, "Unused purchase", "manual-race-refund")
    assert mutation_audit >= order.paid_at
    assert not [call for call in calls if call[1] == "/refunds"]
    assert session.scalar(select(CommerceRefund)) is None
    source = session.scalar(select(CoinSource).where(CoinSource.order_id == order.id))
    assert source.remaining == source.original == 25 and source.reserved == 0
    assert session.get(WalletAccount, user_id).balance == 25
    assert grant.status == "active" and order.status == "paid"


@pytest.mark.parametrize("kind", ["legacy_now", "legacy_period_end", "managed_now", "managed_period_end"])
def test_revoke_audit_uses_actual_wall_clock_in_an_existing_transaction(
    session, commercial, make, monkeypatch, kind,
):
    user, actor, _ = commercial
    if kind.startswith("managed"):
        order = subscription_order(session, user)
        c.process_event(session, "env", invoice(order))
        if kind.endswith("period_end"):
            # Reusing a completed cancel intent avoids starting a fresh DB transaction.
            billing.cancel_subscription(session, user.id)
    else:
        row = make.subscription(user)
        row.stripe_subscription_id = "sub_legacy_audit_clock"
        session.commit()
        monkeypatch.setattr(billing, "_stripe_request", lambda *args, **kwargs:{})
    transaction_started = session.scalar(select(func.now()))
    session.execute(text("SELECT pg_sleep(0.03)"))
    mutation_started = datetime.now(UTC)
    billing.revoke_membership(session, user_id=user.id,
        mode="period_end" if kind.endswith("period_end") else "now",
        reason="Support cancellation in an older transaction", actor_id=actor.id)
    audit = session.scalar(select(AuditLog).where(AuditLog.action == "membership_revoke").order_by(AuditLog.id.desc()))
    assert transaction_started < mutation_started
    assert audit.created_at >= mutation_started


def test_manual_adjustment_before_payment_does_not_block_an_unused_refund(session, commercial):
    user, actor, _ = commercial
    order = membership_order(session, user)
    billing.grant_membership(session, user_id=user.id, plan_code="monthly", days=2,
        mode="extend", reason="Manual pass before any paid source", actor_id=actor.id)
    manual_until = billing.access_until(session, user.id)
    audit = session.scalar(select(AuditLog).where(AuditLog.action == "membership_grant"))
    pay(session, order)
    assert audit.created_at < order.paid_at
    assert c.refund_eligibility(session, order)["eligible"]
    result = c.request_refund(session, order.order_no, actor.id, "Unused purchase", "earlier-adjustment-refund")
    assert result["status"] == "succeeded"
    assert billing.access_until(session, user.id) == manual_until
