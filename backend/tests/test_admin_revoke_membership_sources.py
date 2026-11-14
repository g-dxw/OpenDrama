"""Administrative withdrawal follows an existing membership source through refund release."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.modules.billing import commerce as c
from app.modules.billing import service as billing
from app.modules.billing.commerce_models import CoinSource, CommerceRefund, MembershipGrant
from app.modules.common.models import AuditLog
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount
from tests.test_commerce import commercial, membership_order, pay
from tests.test_commerce_lifecycle import invoice, subscription_order

UTC = timezone.utc


@pytest.fixture
def source_case(session, commercial, monkeypatch):
    user, actor, _ = commercial
    order = membership_order(session, user)
    pay(session, order)
    original = c.stripe_request
    state = dict(outcome="pending", during_create=None, remote=None)
    def remote(db, code, path, data=None, method="POST", idempotency_key=None):
        if path == "/refunds":
            result = dict(id="re_membership_source",payment_intent=order.payment_intent_id,
                amount=order.amount_cents,currency="usd",status=state["outcome"],
                metadata=dict(commerce_order_no=order.order_no,commerce_refund_key=data["metadata[commerce_refund_key]"]))
            state["remote"] = result
            if state["during_create"] is not None:state["during_create"]()
            return deepcopy(result)
        if path == "/refunds/re_membership_source" and method == "GET":
            return {**deepcopy(state["remote"]),"status":state["outcome"]}
        return original(db,code,path,data,method,idempotency_key)
    monkeypatch.setattr(c,"stripe_request",remote)
    grant = session.scalar(select(MembershipGrant).where(MembershipGrant.order_id == order.id))
    return user, actor, order, grant, state


def temporary_access_then_revoke(db,user,actor):
    billing.grant_membership(db,user_id=user.id,plan_code="monthly",days=3,mode="extend",
        reason="Temporary access while provider refund is pending",actor_id=actor.id)
    billing.revoke_membership(db,user_id=user.id,mode="now",reason="Withdraw all current membership sources",actor_id=actor.id)


def request(db,user,actor,order):
    return c.request_refund(db,order.order_no,actor.id,"Unused one-time membership","source-release-refund")


def pending_and_revoke(db,case):
    user,actor,order,grant,_ = case
    assert request(db,user,actor,order)["status"] == "pending"
    temporary_access_then_revoke(db,user,actor)
    db.refresh(grant)
    source = db.scalar(select(CoinSource).where(CoinSource.order_id == order.id))
    assert grant.status == "refund_pending"
    assert source.remaining == 0 and source.reserved == source.original == 25
    assert db.get(WalletAccount,user.id).balance == 0
    assert billing.access_until(db,user.id) is None


def event(db,state,outcome):
    obj={**deepcopy(state["remote"]),"status":outcome}
    return c.process_event(db,"env",dict(type="refund.updated",data=dict(object=obj)))


@pytest.mark.parametrize("path", ["callback", "reconcile", "sync"])
@pytest.mark.parametrize("outcome", ["failed", "canceled"])
def test_one_time_refund_release_cannot_restore_an_administratively_revoked_source(
    session,source_case,path,outcome,
):
    user,actor,order,grant,state = source_case
    if path == "sync":
        state["outcome"] = outcome
        state["during_create"] = lambda:temporary_access_then_revoke(session,user,actor)
        result=request(session,user,actor,order)
    else:
        pending_and_revoke(session,source_case)
        state["outcome"] = outcome
        result=event(session,state,outcome) if path == "callback" else request(session,user,actor,order)
    refund=session.scalar(select(CommerceRefund).where(CommerceRefund.order_id == order.id))
    assert refund.status == outcome and order.status == "paid"
    assert grant.status == "revoked"
    assert billing.access_until(session,user.id) is None
    audit=session.scalar(select(AuditLog).where(AuditLog.action == "membership_revoke"))
    assert dict(order_id=order.id,grant_id=grant.id) in audit.payload["revoked_membership_sources"]
    # All duplicate completion paths and the old paid Checkout remain idempotent.
    event(session,state,outcome)
    request(session,user,actor,order)
    pay(session,order)
    assert grant.status == "revoked"
    source=session.scalar(select(CoinSource).where(CoinSource.order_id == order.id))
    assert source.remaining == source.original == 25 and source.reserved == 0
    assert session.get(WalletAccount,user.id).balance == 25
    assert session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.biz_type == "refund_release")) == 1
    assert session.scalar(select(func.count()).select_from(MembershipGrant)) == 1


@pytest.mark.parametrize("path", ["callback", "reconcile"])
def test_successful_refund_keeps_the_source_refunded_after_withdrawal(session,source_case,path):
    user,actor,order,grant,state = source_case
    pending_and_revoke(session,source_case)
    state["outcome"] = "succeeded"
    event(session,state,"succeeded") if path == "callback" else request(session,user,actor,order)
    assert order.status == "refunded" and grant.status == "refunded"
    assert session.get(WalletAccount,user.id).balance == 0
    assert session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.biz_type == "refund_release")) == 0
    assert billing.access_until(session,user.id) is None


def test_pending_refund_stays_frozen_after_administrative_withdrawal(session,source_case):
    user,actor,order,grant,state = source_case
    pending_and_revoke(session,source_case)
    event(session,state,"pending")
    request(session,user,actor,order)
    assert order.status == "refund_pending" and grant.status == "refund_pending"
    assert session.scalar(select(CommerceRefund)).status == "pending"
    source=session.scalar(select(CoinSource).where(CoinSource.order_id == order.id))
    assert source.remaining == 0 and source.reserved == 25
    assert session.get(WalletAccount,user.id).balance == 0


@pytest.mark.parametrize("outcome", ["failed", "canceled"])
def test_postcutoff_pass_is_not_part_of_the_existing_source_snapshot(session,source_case,outcome):
    user,actor,order,grant,state = source_case
    pending_and_revoke(session,source_case)
    result=c.checkout(session,user_id=user.id,email=user.email,product_type="membership",code="monthly",idempotency_key="post-source-cutoff-purchase")
    from app.modules.billing.models import Order
    fresh=session.scalar(select(Order).where(Order.order_no == result["order_no"]))
    pay(session,fresh)
    expected=billing.access_until(session,user.id)
    event(session,state,outcome)
    new_grant=session.scalar(select(MembershipGrant).where(MembershipGrant.order_id == fresh.id))
    assert grant.status == "revoked" and new_grant.status == "active"
    assert billing.access_until(session,user.id) == expected
    assert c.refund_eligibility(session,fresh)["eligible"]
    audit=session.scalar(select(AuditLog).where(AuditLog.action == "membership_revoke"))
    assert dict(order_id=fresh.id,grant_id=new_grant.id) not in audit.payload["revoked_membership_sources"]


@pytest.mark.parametrize("actor_type", ["user", "system"])
def test_non_admin_source_snapshot_cannot_disable_refund_release(session,source_case,actor_type):
    user,actor,order,grant,state = source_case
    request(session,user,actor,order)
    session.add(AuditLog(actor_type=actor_type,actor_id=user.id if actor_type == "user" else None,
        action="membership_revoke",target_type="user",target_id=user.id,
        payload=dict(mode="now",cutoff_at=datetime.now(UTC).isoformat(),revoked_membership_sources=[dict(order_id=order.id,grant_id=grant.id)])))
    session.commit()
    event(session,state,"failed")
    assert grant.status == "active" and billing.access_until(session,user.id) is not None


def test_source_snapshot_is_scoped_to_the_original_user(session,source_case,make):
    user,actor,order,grant,state = source_case
    request(session,user,actor,order)
    other=make.user(email="unrelated-source@example.test")
    session.add(AuditLog(actor_type="admin",actor_id=actor.id,action="membership_revoke",
        target_type="user",target_id=other.id,payload=dict(mode="now",cutoff_at=datetime.now(UTC).isoformat(),
        revoked_membership_sources=[dict(order_id=order.id,grant_id=grant.id)])))
    session.commit()
    event(session,state,"failed")
    assert grant.status == "active" and billing.access_until(session,user.id) is not None


def test_period_end_cancellation_does_not_withdraw_the_frozen_membership_source(session,commercial,monkeypatch):
    user,actor,_=commercial
    order=subscription_order(session,user)
    c.process_event(session,"env",invoice(order))
    original=c.stripe_request
    def pending(db,code,path,data=None,method="POST",idempotency_key=None):
        if path == "/refunds":return dict(id="re_period_end_source",status="pending",amount=order.amount_cents,currency="usd")
        return original(db,code,path,data,method,idempotency_key)
    monkeypatch.setattr(c,"stripe_request",pending)
    c.request_refund(session,order.order_no,actor.id,"Unused period","period-end-source")
    billing.grant_membership(session,user_id=user.id,plan_code="monthly",days=3,mode="extend",
        reason="Temporary access pending refund",actor_id=actor.id)
    billing.revoke_membership(session,user_id=user.id,mode="period_end",reason="Stop renewal only",actor_id=actor.id)
    refund=session.scalar(select(CommerceRefund))
    c.finalize_refund(session,refund,"failed");session.commit()
    grant=session.scalar(select(MembershipGrant).where(MembershipGrant.order_id == order.id))
    audit=session.scalar(select(AuditLog).where(AuditLog.action == "membership_revoke"))
    assert "revoked_membership_sources" not in audit.payload
    assert grant.status == "active" and billing.access_until(session,user.id) is not None
