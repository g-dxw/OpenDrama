"""Confirmed one-time Checkout expiry must expose the existing recovery contract."""
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.modules.billing import commerce as c
from app.modules.billing.commerce_models import MembershipGrant
from app.modules.billing.models import Order
from app.modules.billing.service import BillingError
from app.modules.wallet.models import WalletAccount
from tests.test_commerce import commercial, membership_order, pay, purchase


def expire(db, order):
    return c.process_event(db, order.account_code, {"type":"checkout.session.expired",
        "data":{"object":{"id":order.external_txn_id,
            "metadata":{"commerce_order_no":order.order_no}}}})


def retry(db, user, order, key="one-time-original", **kwargs):
    return c.checkout(db, user_id=user.id, email=user.email,
        product_type=order.product_type, code=order.product_snapshot["code"],
        idempotency_key=key, **kwargs)


@pytest.mark.parametrize("kind", ["membership", "coin_pack"])
@pytest.mark.parametrize("with_url", [True, False])
def test_confirmed_expired_one_time_checkout_requires_a_new_purchase(
    session, commercial, kind, with_url,
):
    user, _, calls = commercial
    order = (membership_order(session, user, "one-time-original") if kind == "membership"
        else purchase(session, user, "one-time-original"))
    assert order.billing_mode == "one_time"
    expire(session, order)
    assert order.status == "failed" and order.checkout_context["session_expired"]
    if not with_url:
        order.checkout_url = None
        session.commit()
    for _ in range(2):
        with pytest.raises(BillingError) as error:
            retry(session, user, order)
        assert error.value.status_code == 409
        assert error.value.detail == dict(code="checkout_expired", order_no=order.order_no,
            message="This checkout has expired. Start a new purchase.", action="new_purchase")
        session.rollback()
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert len([call for call in calls if call[1] == "/checkout/sessions"]) == 1
    assert session.get(WalletAccount, user.id).balance == 0
    assert session.scalar(select(func.count()).select_from(MembershipGrant)) == 0
    replacement = retry(session, user, order, key="one-time-replacement")
    assert replacement["order_no"] != order.order_no
    assert session.scalar(select(func.count()).select_from(Order)) == 2
    assert len([call for call in calls if call[1] == "/checkout/sessions"]) == 2


def test_expiry_arriving_during_price_lookup_is_checked_after_relocking(
    session, commercial, monkeypatch,
):
    user, _, calls = commercial
    order = membership_order(session, user, "one-time-original")
    stale_url = order.checkout_url
    order.checkout_url = None
    session.commit()
    original = c.ensure_price
    def lookup(db, account, snapshot):
        # Model a webhook commit after the first check and before the checkout
        # refreshes its order under the wallet lock. A stored URL remains stale.
        price = original(db, account, snapshot)
        order.checkout_url = stale_url
        expire(db, order)
        return price
    monkeypatch.setattr(c, "ensure_price", lookup)
    with pytest.raises(BillingError) as error:
        retry(session, user, order)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "checkout_expired"
    assert error.value.detail["action"] == "new_purchase"
    session.expire_all()
    assert session.get(Order, order.id).status == "failed"
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert len([call for call in calls if call[1] == "/checkout/sessions"]) == 1


@pytest.mark.parametrize("outcome", ["paid", "refund_pending", "refunded", "refund_review"])
def test_settled_orders_take_priority_over_historical_expiry(
    session, commercial, monkeypatch, outcome,
):
    user, actor, calls = commercial
    order = membership_order(session, user, "one-time-original")
    expire(session, order)
    pay(session, order)
    assert order.status == "paid" and order.checkout_context["session_expired"]
    if outcome == "refund_pending":
        original = c.stripe_request
        def pending(db, code, path, data=None, method="POST", idempotency_key=None):
            if path == "/refunds":
                return dict(id="re_pending_expiry", status="pending",
                    amount=int(data["amount"]), currency="usd")
            return original(db, code, path, data, method, idempotency_key)
        monkeypatch.setattr(c, "stripe_request", pending)
        c.request_refund(session, order.order_no, actor.id, "Unused", "expired-paid-refund")
    elif outcome == "refunded":
        c.request_refund(session, order.order_no, actor.id, "Unused", "expired-paid-refund")
    elif outcome == "refund_review":
        c.process_event(session, "env", {"type":"refund.created", "data":{"object":{
            "id":"re_external_expiry", "payment_intent":order.payment_intent_id,
            "status":"pending"}}})
    assert order.status == outcome
    balance = session.get(WalletAccount, user.id).balance
    for _ in range(2):
        result = retry(session, user, order)
        assert result["reused"] and result["order_no"] == order.order_no
    assert order.status == outcome
    assert session.get(WalletAccount, user.id).balance == balance
    assert session.scalar(select(func.count()).select_from(MembershipGrant)) == 1
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert len([call for call in calls if call[1] == "/checkout/sessions"]) == 1


def test_failed_without_expiry_proof_keeps_original_idempotent_attempt(session, commercial):
    user, _, calls = commercial
    order = membership_order(session, user, "one-time-original")
    c.process_event(session, "env", {"type":"checkout.session.async_payment_failed",
        "data":{"object":{"id":order.external_txn_id}}})
    assert order.status == "failed" and not order.checkout_context.get("session_expired")
    result = retry(session, user, order)
    assert result["reused"] and result["order_no"] == order.order_no
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert len([call for call in calls if call[1] == "/checkout/sessions"]) == 1


def test_unknown_create_response_retries_frozen_request_with_same_key(
    session, commercial, monkeypatch,
):
    user, _, _ = commercial
    original = c.stripe_request
    attempts = []
    def unknown(db, code, path, data=None, method="POST", idempotency_key=None):
        if path == "/checkout/sessions":
            attempts.append((deepcopy(data), idempotency_key))
            raise BillingError(503, "unknown remote outcome")
        return original(db, code, path, data, method, idempotency_key)
    monkeypatch.setattr(c, "stripe_request", unknown)
    with pytest.raises(BillingError):
        membership_order(session, user, "one-time-original")
    session.rollback()
    order = session.scalar(select(Order))
    assert order.external_txn_id is None and not order.checkout_context.get("session_expired")
    def recovered(db, code, path, data=None, method="POST", idempotency_key=None):
        if path == "/checkout/sessions":
            attempts.append((deepcopy(data), idempotency_key))
        return original(db, code, path, data, method, idempotency_key)
    monkeypatch.setattr(c, "stripe_request", recovered)
    result = c.checkout(session, user_id=user.id, email="changed@example.test",
        product_type="membership", code="monthly", idempotency_key="one-time-original",
        success_url="https://jjtv.8jj.net/changed", cancel_url="https://jjtv.8jj.net/changed")
    assert result["reused"] and result["order_no"] == order.order_no
    assert attempts[0] == attempts[1]
    assert session.scalar(select(func.count()).select_from(Order)) == 1


@pytest.mark.parametrize("mismatch", ["product", "account"])
def test_expiry_does_not_bypass_idempotency_product_or_account_binding(
    session, commercial, mismatch,
):
    user, _, _ = commercial
    order = membership_order(session, user, "one-time-original")
    expire(session, order)
    with pytest.raises(BillingError) as error:
        c.checkout(session, user_id=user.id, email=user.email, idempotency_key="one-time-original",
            product_type="coin_pack" if mismatch == "product" else "membership",
            code="starter" if mismatch == "product" else "monthly",
            account_code="another" if mismatch == "account" else None)
    assert error.value.status_code == 409
    assert isinstance(error.value.detail, str)
    expected = "another product" if mismatch == "product" else "another payment channel"
    assert expected in error.value.detail
    assert session.scalar(select(func.count()).select_from(Order)) == 1
