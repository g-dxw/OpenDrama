from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.account import service as account_service
from app.modules.billing import commerce, pancake_accounts, pancake_subscriptions, service, waffo_pancake
from app.modules.billing.commerce_models import CommerceSettlement, GatewayAccount, MembershipGrant
from app.modules.billing.models import Order, PancakePeriodEvidence, Plan, PlanVersion, Subscription
from app.modules.common.models import AuditLog
from app.modules.wallet.models import WalletAccount


REFERENCE = "ORD_WaffleTest"
STORE = "STO_Recurring"
PRODUCT = "PROD_Recurring"


def charge(payment_id, period_number, *, amount="10.00"):
    return {
        "id": payment_id,
        "orderId": REFERENCE,
        "status": "succeeded",
        "periodNumber": period_number,
        "snapshotAmountDetails": {"currency": "USD", "total": amount, "discountAmount": "0", "taxAmount": "2.00"},
        "amount": {"display": amount, "currency": "USD"},
    }


@pytest.fixture
def recurring(session, make, monkeypatch):
    buyer = make.user("pancake-renewal@example.com")
    plan = Plan(code="weekly", name_en="Weekly Pass")
    session.add(plan)
    session.flush()
    version = PlanVersion(plan_id=plan.id, version=1, price_cents=1000, currency="USD", benefits={}, base_coins=2)
    account = GatewayAccount(
        provider="waffo_pancake", code="pancake_test", name="Waffo Pancake", status="active", identity="f" * 64,
        config={"mode": "test", "store_id": STORE}, credentials={},
    )
    session.add_all([version, account])
    session.flush()
    order = Order(
        order_no="pancake-initial", user_id=buyer.id, product_type="membership", plan_version_id=version.id,
        account_code=account.code, provider=account.provider, amount_cents=version.price_cents, currency="USD",
        billing_mode="subscription", status="created", checkout_key="pancake-initial-checkout",
        product_snapshot={"code": "weekly", "price_cents": 1000, "currency": "USD",
                          "base_coins": 2, "version_id": version.id, "billing_mode": "subscription"},
        checkout_context={"gateway_mode": "test", "external_product_id": PRODUCT, "store_id": STORE},
    )
    session.add(order)
    session.commit()
    start = commerce.now().replace(microsecond=0) - timedelta(hours=1)
    end = start + timedelta(days=7)
    payments = {"PAY_First": charge("PAY_First", 1), "PAY_Renewal": charge("PAY_Renewal", 2)}
    remote = {
        "id": REFERENCE, "status": "active", "orderMerchantExternalId": order.order_no,
        "subscriptionProduct": {"id": PRODUCT}, "store": {"id": STORE}, "testMode": True,
        "billingPeriod": "weekly", "currentPeriodStart": start.isoformat(), "currentPeriodEnd": end.isoformat(),
        "currentPeriodNumber": 1, "payments": [payments["PAY_First"]],
    }
    monkeypatch.setattr(pancake_accounts, "credentials", lambda _: {})
    monkeypatch.setattr(waffo_pancake, "get_order", lambda **kwargs: dict(remote))
    monkeypatch.setattr(waffo_pancake, "get_payment", lambda payment_id, **kwargs: payments[payment_id])
    return account, order, buyer, remote, payments, (start, end)


def event(kind, order, period_number, *, payment_id=None, window=None):
    data = {"orderId": REFERENCE, "orderMerchantExternalId": order.order_no, "currency": "USD", "periodNumber": period_number}
    if payment_id:
        data.update(paymentId=payment_id, paymentStatus="succeeded")
    if window:
        data.update(currentPeriodStart=window[0].isoformat(), currentPeriodEnd=window[1].isoformat(), billingPeriod="weekly")
    return {"eventType": kind, "mode": "test", "storeId": STORE, "data": data}


def settle_first(session, recurring):
    account, order, _, remote, _, _ = recurring
    return pancake_subscriptions.reconcile(session, account, order, remote)


@pytest.mark.parametrize("reversed_delivery", [False, True])
def test_period_and_charge_arrive_independently_and_settle_once(session, recurring, reversed_delivery):
    account, order, buyer, _, _, first = recurring
    paid = event("subscription.payment_succeeded", order, 1, payment_id="PAY_First")
    activated = event("subscription.activated", order, 1, window=first)
    pending, ready = (activated, paid) if reversed_delivery else (paid, activated)
    assert pancake_subscriptions.handle_event(session, account, order, pending)["pending"]
    proof = session.get(PancakePeriodEvidence, (order.id, 1))
    assert proof.settled_order_id is None
    assert session.get(WalletAccount, buyer.id).balance == 0
    assert pancake_subscriptions.handle_event(session, account, order, ready)["status"] == "paid"
    assert pancake_subscriptions.handle_event(session, account, order, paid)["replayed"]
    assert pancake_subscriptions.handle_event(session, account, order, activated)["replayed"]
    assert len(list(session.scalars(select(CommerceSettlement)))) == 1
    assert (proof.period_start, proof.period_end, proof.payment_id, proof.settled_order_id) == (*first, "PAY_First", order.id)
    assert order.checkout_context["provider_amount_paid"] == 1000
    assert order.checkout_context["provider_tax_minor"] == 200
    assert session.get(WalletAccount, buyer.id).balance == 2
    assert service.membership_detail(session, buyer.id)["has_renewing_subscription"]


def test_out_of_order_renewal_keeps_original_checkout_and_latest_period(session, recurring):
    account, order, buyer, remote, payments, first = recurring
    following = first[1], first[1] + timedelta(days=7)
    remote.update(currentPeriodStart=following[0].isoformat(), currentPeriodEnd=following[1].isoformat(),
                  currentPeriodNumber=2, payments=list(payments.values()))
    second = pancake_subscriptions.reconcile(session, account, order, remote)
    renewal = session.scalar(select(Order).where(Order.order_no == second["order_no"]))
    assert renewal.checkout_context["pancake_initial_order_no"] == order.order_no
    assert renewal.checkout_context["provider_trade_no"] == "PAY_Renewal"
    assert renewal.product_snapshot == order.product_snapshot
    assert order.status == "created"
    pancake_subscriptions.handle_event(session, account, order, event("subscription.activated", order, 1, window=first))
    assert pancake_subscriptions.handle_event(
        session, account, order, event("subscription.payment_succeeded", order, 1, payment_id="PAY_First"),
    )["order_no"] == order.order_no
    row = session.scalar(select(Subscription).where(Subscription.pancake_subscription_id == REFERENCE))
    assert (row.current_period_start, row.current_period_end) == following
    assert session.get(WalletAccount, buyer.id).balance == 4


def test_bad_amount_and_mismatched_period_cannot_settle(session, recurring):
    account, order, buyer, _, payments, first = recurring
    pancake_subscriptions.handle_event(session, account, order, event("subscription.activated", order, 1, window=first))
    payments["PAY_Bad"] = charge("PAY_Bad", 1, amount="9.99")
    with pytest.raises(service.BillingError, match="frozen amount"):
        pancake_subscriptions.handle_event(
            session, account, order, event("subscription.payment_succeeded", order, 1, payment_id="PAY_Bad"),
        )
    session.rollback()
    with pytest.raises(service.BillingError, match="conflicting period"):
        pancake_subscriptions.handle_event(
            session, account, order, event("subscription.payment_succeeded", order, 1, payment_id="PAY_Renewal"),
        )
    session.rollback()
    notified = event("subscription.payment_succeeded", order, 1, payment_id="PAY_First")
    notified["data"]["chargedAmount"] = "0"
    with pytest.raises(service.BillingError, match="charged amount"):
        pancake_subscriptions.handle_event(session, account, order, notified)
    session.rollback()
    assert order.status == "created"
    assert session.get(WalletAccount, buyer.id).balance == 0


def test_refund_between_payment_and_period_prevents_fulfilment(session, recurring):
    account, order, buyer, _, payments, first = recurring
    pancake_subscriptions.handle_event(
        session, account, order, event("subscription.payment_succeeded", order, 1, payment_id="PAY_First"),
    )
    payments["PAY_First"]["refundStatus"] = "refunded"
    with pytest.raises(service.BillingError, match="refund"):
        pancake_subscriptions.handle_event(session, account, order, event("subscription.activated", order, 1, window=first))
    session.rollback()
    assert order.status == "created"
    assert session.get(WalletAccount, buyer.id).balance == 0


def test_customer_cancel_stops_renewal_and_keeps_paid_access(session, recurring, monkeypatch):
    account, order, buyer, remote, _, first = recurring
    settle_first(session, recurring)
    calls = []

    def cancel(*, order_id):
        calls.append(order_id)
        remote["status"] = "canceling"
        return {"orderId": order_id, "status": "canceling"}

    monkeypatch.setattr(waffo_pancake, "cancel_subscription", cancel)
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + account_service._issue_session(buyer)}
    assert client.post("/v1/billing/cancel", headers=headers).status_code == 200
    assert client.post("/v1/billing/cancel", headers=headers).status_code == 200
    assert calls == [REFERENCE]
    session.expire_all()
    membership = service.active_membership(session, buyer.id)
    assert membership["active"] and membership["canceling"] and datetime.fromisoformat(membership["until"]) == first[1]
    pancake_subscriptions.assert_can_start(session, buyer.id)
    remote["status"] = "active"
    with pytest.raises(service.BillingError, match="renewing Waffo Pancake"):
        pancake_subscriptions.assert_can_start(session, buyer.id)
    pancake_subscriptions.handle_event(session, account, order, event("subscription.uncanceled", order, 1))
    assert not service.active_membership(session, buyer.id)["canceling"]


def test_admin_revoke_waits_for_provider_then_fences_late_payment(session, recurring, make, monkeypatch):
    account, order, buyer, remote, _, first = recurring
    settle_first(session, recurring)
    admin = make.admin(role="admin")
    calls = []

    def cancel(*, order_id):
        calls.append(order_id)
        if len(calls) == 1:
            raise waffo_pancake.PancakeTransportError("Network failure")
        remote["status"] = "canceling"
        return {"orderId": order_id, "status": "canceling"}

    monkeypatch.setattr(waffo_pancake, "cancel_subscription", cancel)
    args = {"user_id": buyer.id, "mode": "now", "reason": "test revoke", "actor_id": admin.id}
    with pytest.raises(service.BillingError, match="unconfirmed"):
        service.revoke_membership(session, **args)
    session.rollback()
    assert service.active_membership(session, buyer.id)["active"]
    assert service.revoke_membership(session, **args)["pancake_canceled"]
    assert not service.active_membership(session, buyer.id)["active"]
    audit = session.scalar(select(AuditLog).where(AuditLog.action == "membership_revoke"))
    assert audit.payload["revoked_pancake_subscription_bindings"] == [
        {"account_code": account.code, "subscription_id": REFERENCE},
    ]
    following = first[1], first[1] + timedelta(days=7)
    pancake_subscriptions.handle_event(session, account, order, event("subscription.renewed", order, 2, window=following))
    pancake_subscriptions.handle_event(session, account, order, event("subscription.payment_succeeded", order, 2, payment_id="PAY_Renewal"))
    assert set(session.scalars(select(MembershipGrant.status))) == {"revoked"}
    assert service.active_subscription(session, buyer.id) is None
