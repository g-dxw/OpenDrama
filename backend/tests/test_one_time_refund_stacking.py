"""Frozen one-time credits reconcile without moving or consuming another paid source."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.modules.billing import commerce as c
from app.modules.billing import service as billing
from app.modules.billing.models import Order
from app.modules.billing.commerce_models import CommerceRefund, MembershipGrant, CoinSource
from app.modules.billing.service import BillingError
from app.modules.common.models import AuditLog
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount
from tests.test_commerce import commercial, membership_order, pay

UTC=timezone.utc


@pytest.fixture
def stacking(session,commercial,monkeypatch):
    user,actor,calls=commercial
    clock=dict(now=datetime.now(UTC));monkeypatch.setattr(c,"now",lambda:clock["now"])
    first=membership_order(session,user,"stacking-first");pay(session,first)
    original=c.stripe_request;refunds={}
    def pending(db,code,path,data=None,method="POST",idempotency_key=None):
        if path == "/refunds":
            owner=db.scalar(select(Order).where(Order.order_no == data["metadata[commerce_order_no]"]))
            remote=dict(id="re_"+owner.order_no,status="pending",amount=owner.amount_cents,currency="usd",payment_intent=owner.payment_intent_id)
            refunds[remote["id"]]=remote
            return deepcopy(remote)
        if path.startswith("/refunds/"):return deepcopy(refunds[path.split("/")[-1]])
        return original(db,code,path,data,method,idempotency_key)
    monkeypatch.setattr(c,"stripe_request",pending)
    return dict(user=user,actor=actor,first=first,clock=clock,point=clock["now"],calls=calls,refunds=refunds)


def checkout(db,case,key):
    result=c.checkout(db,user_id=case["user"].id,email=case["user"].email,product_type="membership",code="monthly",idempotency_key=key)
    return db.scalar(select(Order).where(Order.order_no == result["order_no"]))


def freeze(db,case,order):
    return c.request_refund(db,order.order_no,case["actor"].id,"Unused pass","refund-"+order.order_no)


def outcome(db,case,order,status):
    refund=db.scalar(select(CommerceRefund).where(CommerceRefund.order_id == order.id))
    event=deepcopy(case["refunds"][refund.refund_id]);event["status"]=status
    case["refunds"][refund.refund_id]=event
    return c.process_event(db,"env",dict(type="refund.updated",data=dict(object=event)))


def grant(db,order):return db.scalar(select(MembershipGrant).where(MembershipGrant.order_id == order.id))
def detail(db,order):return c.admin_order_detail(db,order.order_no)["membership_refund_resolution"]

def collision(db,case,status="failed",elapsed=timedelta()):
    second=checkout(db,case,"stacking-second")  # Already-open Checkout can race the refund reservation.
    case["clock"]["now"]+=elapsed
    freeze(db,case,case["first"]);pay(db,second);outcome(db,case,case["first"],status)
    return second


def resolve(db,case,order,key="resolve-source-one",reason="Reviewed original source and current rights",revision=None):
    return c.resolve_membership_refund(db,order.order_no,case["actor"].id,reason,key,revision or detail(db,order)["revision"])


def active_duration(db,user_id,point):
    windows=sorted((max(c.aware(g.starts_at),point),c.aware(g.ends_at)) for g in db.scalars(select(MembershipGrant).where(MembershipGrant.user_id == user_id,MembershipGrant.status == "active")) if c.aware(g.ends_at)>point)
    total=timedelta();start=end=None
    for left,right in windows:
        if end is None or left>end:
            if end is not None:total+=end-start
            start,end=left,right
        else:end=max(end,right)
    return total+(end-start if end is not None else timedelta())


@pytest.mark.parametrize("status",["failed","canceled","succeeded"])
@pytest.mark.parametrize("consumed",[False,True])
def test_all_paid_duration_is_preserved_by_explicit_source_resolution(session,stacking,status,consumed):
    case=stacking;first=case["first"];second=checkout(session,case,"stacking-second")
    freeze(session,case,first);pay(session,second)
    second_grant=grant(session,second);window=(second_grant.starts_at,second_grant.ends_at)
    if consumed:c.mark_membership_consumed(session,case["user"].id,123);session.commit()
    eligible=c.refund_eligibility(session,second)["eligible"]
    outcome(session,case,first,status)
    if status == "succeeded":
        assert first.status == "refunded" and grant(session,first).status == "refunded"
        assert active_duration(session,case["user"].id,case["point"]) == timedelta(days=30)
        assert not detail(session,first)["required"]
    else:
        state=detail(session,first)
        assert state["required"] and not state["can_resolve"] and state["provider_status"] == status
        assert state["remaining_seconds"] == 30*86400
        assert first.status == "refund_review" and grant(session,first).status == "refund_pending"
        assert session.get(WalletAccount,case["user"].id).balance == 25
        with pytest.raises(BillingError):resolve(session,case,first)
        case["clock"]["now"]=c.aware(second_grant.ends_at)
        result=resolve(session,case,first)
        assert result["restored"] and result["released_coins"] == 25
        assert active_duration(session,case["user"].id,case["point"]) == timedelta(days=60)
        assert session.get(WalletAccount,case["user"].id).balance == 50
    assert (second_grant.starts_at,second_grant.ends_at) == window and second_grant.access_consumed == consumed
    assert c.refund_eligibility(session,second)["eligible"] == eligible


def test_new_checkout_is_blocked_but_open_session_payment_is_recorded(session,stacking):
    case=stacking;second=checkout(session,case,"already-open")
    freeze(session,case,case["first"])
    for key in ("fresh-member", "already-open"):
        with pytest.raises(BillingError) as error:checkout(session,case,key)
        assert error.value.detail["code"] == "membership_refund_pending"
    pay(session,second);outcome(session,case,case["first"],"failed")
    with pytest.raises(BillingError):checkout(session,case,"review-member")
    assert second.status == "paid" and detail(session,case["first"])["required"]
    assert session.scalar(select(func.count()).select_from(Order)) == 2


def test_failed_refund_without_conflict_restores_only_frozen_remaining_budget(session,stacking):
    case=stacking;first=case["first"]
    case["clock"]["now"]+=timedelta(days=3);freeze(session,case,first)
    frozen=first.checkout_context["membership_refund_freeze"]
    assert frozen["remaining_microseconds"] == 27*86400*1000000
    case["clock"]["now"]+=timedelta(days=5);outcome(session,case,first,"failed")
    g=grant(session,first)
    assert c.aware(g.starts_at) == case["clock"]["now"]
    assert c.aware(g.ends_at)-c.aware(g.starts_at) == timedelta(days=27)
    window=(g.starts_at,g.ends_at);case["clock"]["now"]+=timedelta(days=1);outcome(session,case,first,"failed")
    assert (g.starts_at,g.ends_at) == window
    assert not detail(session,first)["required"]


def test_elapsed_budget_is_fixed_and_cas_replay_never_extends_it(session,stacking):
    case=stacking;first=case["first"];second=collision(session,case,elapsed=timedelta(days=3))
    case["clock"]["now"]=c.aware(grant(session,second).ends_at)
    state=detail(session,first);assert state["can_resolve"] and state["remaining_seconds"] == 27*86400
    before=(session.get(WalletAccount,case["user"].id).balance,session.scalar(select(func.count()).select_from(AuditLog)),deepcopy(first.checkout_context))
    with pytest.raises(BillingError) as error:resolve(session,case,first,revision="0"*64)
    assert error.value.detail["code"] == "membership_resolution_changed" and error.value.detail["committed"] is False
    assert before == (session.get(WalletAccount,case["user"].id).balance,session.scalar(select(func.count()).select_from(AuditLog)),first.checkout_context)
    result=resolve(session,case,first,revision=state["revision"])
    assert datetime.fromisoformat(result["ends_at"])-datetime.fromisoformat(result["starts_at"]) == timedelta(days=27)
    case["clock"]["now"]+=timedelta(days=2)
    replay=resolve(session,case,first,revision=state["revision"])
    assert replay == {**result,"replayed":True}
    for kwargs,code in ((dict(key="another-source-key"),"membership_resolution_already_resolved"),(dict(reason="Different reason",revision=state["revision"]),"membership_resolution_key_conflict")):
        with pytest.raises(BillingError) as error:resolve(session,case,first,**kwargs)
        assert error.value.detail["code"] == code
    closed=detail(session,first)
    assert closed["resolved"] and not closed["required"] and closed["result"] == result
    assert session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.biz_type == "refund_release")) == 1
    assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "membership_source.resolve")) == 1


@pytest.mark.parametrize("invalid",["budget","refund_id","version","unknown_status"])
def test_unproven_budget_or_provider_outcome_cannot_resolve(session,stacking,invalid):
    case=stacking;first=case["first"];second=collision(session,case)
    case["clock"]["now"]=c.aware(grant(session,second).ends_at)
    context=deepcopy(first.checkout_context)
    if invalid == "budget":context.pop("membership_refund_freeze")
    elif invalid == "refund_id":context["membership_refund_reconciliation"]["provider_refund_id"]="re_other"
    elif invalid == "version":context["membership_refund_reconciliation"]["version"]=99
    else:context["membership_refund_reconciliation"]["provider_status"]="pending"
    first.checkout_context=context;session.commit()
    state=detail(session,first)
    assert state["required"] and not state["can_resolve"] and state["blocked_reason"]
    with pytest.raises(BillingError):resolve(session,case,first)
    assert grant(session,first).status == "refund_pending"
    assert session.scalar(select(CoinSource).where(CoinSource.order_id == first.id)).reserved == 25


def test_admin_withdrawal_resolves_funds_without_restoring_access(session,stacking):
    case=stacking;first=case["first"];collision(session,case)
    prior=detail(session,first)["revision"]
    billing.revoke_membership(session,user_id=case["user"].id,mode="now",reason="Administrative withdrawal",actor_id=case["actor"].id)
    state=detail(session,first)
    assert state["disposition"] == "release_revoked" and state["can_resolve"]
    with pytest.raises(BillingError) as error:resolve(session,case,first,revision=prior)
    assert error.value.detail["code"] == "membership_resolution_changed"
    result=resolve(session,case,first)
    assert not result["restored"] and result["starts_at"] is None and result["ends_at"] is None
    assert result["released_coins"] == 25 and grant(session,first).status == "revoked"
    assert billing.access_until(session,case["user"].id) is None


def test_two_known_review_credits_resolve_in_order_without_permanent_cycle(session,stacking):
    case=stacking;first=case["first"]
    second=checkout(session,case,"second-paid");pay(session,second)
    third=checkout(session,case,"third-open")
    freeze(session,case,first);freeze(session,case,second);pay(session,third)
    outcome(session,case,first,"failed");outcome(session,case,second,"canceled")
    case["clock"]["now"]=c.aware(grant(session,third).ends_at)
    assert detail(session,first)["can_resolve"] and not detail(session,second)["can_resolve"]
    original=deepcopy(second.checkout_context)
    context=deepcopy(original);context["membership_refund_reconciliation"]["provider_refund_id"]="re_stale"
    second.checkout_context=context;session.commit()
    assert not detail(session,first)["can_resolve"], "unknown later proof must block even the first credit"
    second.checkout_context=original;session.commit()
    resolve(session,case,first,key="resolve-first-credit")
    assert not detail(session,second)["can_resolve"]
    case["clock"]["now"]=c.aware(grant(session,first).ends_at)
    assert detail(session,second)["can_resolve"]
    resolve(session,case,second,key="resolve-second-credit")
    assert active_duration(session,case["user"].id,case["point"]) == timedelta(days=90)


def test_historical_conflict_is_retained_after_the_other_source_refunds(session,stacking):
    case=stacking;first=case["first"];second=collision(session,case)
    freeze(session,case,second);outcome(session,case,second,"succeeded")
    state=detail(session,first)
    assert state["required"] and state["can_resolve"]
    resolve(session,case,first)
    assert grant(session,second).status == "refunded"
    assert active_duration(session,case["user"].id,case["point"]) == timedelta(days=30)
