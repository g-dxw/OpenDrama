from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.modules.billing import commerce, service, waffo, waffo_accounts, waffo_subscriptions
from app.modules.billing.commerce_models import CommerceSettlement, GatewayAccount, MembershipGrant
from app.modules.billing.models import Order, Plan, PlanVersion, Subscription
from app.modules.common.models import AuditLog
from app.modules.wallet.models import WalletAccount


REFERENCE = "sub_Waffo"
MERCHANT = "100112"
PERIOD_EVENT = "SUBSCRIPTION_PERIOD_CHANGED_NOTIFICATION"
STATUS_EVENT = "SUBSCRIPTION_STATUS_NOTIFICATION"


def _wire(dt):
    return dt.isoformat().replace("+00:00", "Z")


@pytest.fixture
def recurring(session, make, monkeypatch):
    monkeypatch.setenv("PAYMENT_GATEWAY_TEST_MODE", "1")
    buyer = make.user("waffo-renewal@example.com")
    plan = Plan(code="weekly", name_en="Weekly Pass")
    session.add(plan)
    session.flush()
    version = PlanVersion(plan_id=plan.id, version=1, price_cents=1000, currency="USD", benefits={}, base_coins=2)
    account = GatewayAccount(
        provider="waffo", code="waffo_test", name="Waffo", status="active", identity="d" * 64,
        config={"mode": "test", "currency": "USD", "merchant_id": MERCHANT}, credentials={},
    )
    session.add_all([version, account])
    session.flush()
    order = Order(
        order_no="waffo-initial", user_id=buyer.id, product_type="membership", plan_version_id=version.id,
        account_code=account.code, provider=account.provider, amount_cents=version.price_cents, currency="USD",
        billing_mode="subscription", status="created", checkout_key="waffo-initial-checkout",
        product_snapshot={"code": "weekly", "price_cents": 1000, "currency": "USD",
                          "base_coins": 2, "version_id": version.id, "billing_mode": "subscription"},
        checkout_context={"gateway_mode": "test", "subscription_interval": "week"},
    )
    session.add(order)
    session.commit()
    start = commerce.now().replace(microsecond=0) - timedelta(hours=1)
    end = start + timedelta(weeks=1)
    request = waffo.request_reference(order.order_no)
    remote = {
        "subscriptionId": REFERENCE, "subscriptionStatus": "ACTIVE", "subscriptionRequest": request,
        "merchantSubscriptionId": order.order_no, "merchantInfo": {"merchantId": int(MERCHANT)},
        "currency": "USD", "amount": "10.00",
        "productInfo": {"periodType": "WEEKLY", "periodInterval": "1", "startDateTime": _wire(start),
                        "nextPaymentDateTime": _wire(end), "currentPeriod": "1"},
        "paymentDetails": [],
    }
    acquisitions = {}

    def add_period(period, identifier):
        charge = {
            "acquiringOrderId": identifier, "orderStatus": "PAY_SUCCESS", "orderCurrency": "USD",
            "orderAmount": "10.00",
        }
        acquisitions[identifier] = {
            **charge, "merchantInfo": {"merchantId": MERCHANT},
            "subscriptionInfo": {"subscriptionId": REFERENCE, "subscriptionRequest": request, "period": str(period),
                                 "subscriptionEvent": "PERIOD_CHARGE"},
        }
        remote["paymentDetails"].append({**charge, "period": str(period)})

    add_period(1, "acq_first")
    monkeypatch.setattr(waffo_accounts, "credentials", lambda _: {"mode": "test", "merchant_id": MERCHANT})
    monkeypatch.setattr(waffo, "get_subscription", lambda **kwargs: remote)
    monkeypatch.setattr(waffo, "get_order", lambda provider_order_id, **kwargs: acquisitions[provider_order_id])
    return buyer, account, order, remote, acquisitions, add_period, start, end


def _notice(order):
    return {"subscriptionId": REFERENCE, "merchantSubscriptionId": order.order_no,
            "subscriptionRequest": waffo.request_reference(order.order_no)}


def test_only_authenticated_periods_settle_and_replay_once(session, recurring):
    buyer, account, order, remote, _, add_period, start, end = recurring
    assert waffo_subscriptions.handle_event(session, account, order, "PAYMENT_NOTIFICATION", _notice(order)) == {
        "ignored": "PAYMENT_NOTIFICATION",
    }
    assert order.status == "created"
    remote["productInfo"].update(currentPeriod="2", nextPaymentDateTime=_wire(end + timedelta(weeks=1)))
    add_period(2, "acq_second")
    remote["paymentDetails"].reverse()

    assert waffo_subscriptions.handle_event(session, account, order, PERIOD_EVENT, _notice(order))["status"] == "paid"
    assert waffo_subscriptions.handle_event(session, account, order, STATUS_EVENT, _notice(order))["status"] == "paid"
    assert waffo_subscriptions.reconcile(session, account, order)["status"] == "paid"
    orders = list(session.scalars(select(Order).order_by(Order.id)))
    assert orders[0].id == order.id
    assert [item.checkout_context["provider_trade_no"] for item in orders] == ["acq_first", "acq_second"]
    assert orders[1].checkout_context["waffo_initial_order_no"] == order.order_no
    assert orders[1].product_snapshot == order.product_snapshot
    assert len(list(session.scalars(select(CommerceSettlement)))) == 2
    grants = list(session.scalars(select(MembershipGrant).order_by(MembershipGrant.order_id)))
    assert [(grant.starts_at, grant.ends_at) for grant in grants] == [(start, end), (end, end + timedelta(weeks=1))]
    sub = session.scalar(select(Subscription))
    assert (sub.waffo_subscription_id, sub.current_period_start, sub.current_period_end) == (
        REFERENCE, end, end + timedelta(weeks=1),
    )
    assert session.get(WalletAccount, buyer.id).balance == 4
    assert service.membership_detail(session, buyer.id)["has_renewing_subscription"]

    # A different successful acquisition for the same period needs manual review.
    remote["paymentDetails"].pop(0)
    add_period(2, "acq_conflict")
    with pytest.raises(service.BillingError, match="already bound"):
        waffo_subscriptions.reconcile(session, account, order)
    session.rollback()
    assert len(list(session.scalars(select(CommerceSettlement)))) == 2


@pytest.mark.parametrize("tamper, error", [
    ("amount", "frozen amount"), ("period", "did not verify"), ("merchant", "did not verify"),
    ("cadence", "frozen subscription"), ("boundary", "billing boundaries"), ("status", "did not verify"),
    ("topup", "did not verify"),
])
def test_requires_exact_payment_amount_period_and_billing_window(session, recurring, tamper, error):
    _, account, order, remote, acquisitions, _, _, end = recurring
    if tamper == "amount":
        remote["paymentDetails"][0]["orderAmount"] = "9.99"
    elif tamper == "period":
        acquisitions["acq_first"]["subscriptionInfo"]["period"] = "2"
    elif tamper == "merchant":
        acquisitions["acq_first"]["merchantInfo"]["merchantId"] = "999999"
    elif tamper == "cadence":
        remote["productInfo"]["periodInterval"] = "2"
    elif tamper == "boundary":
        remote["productInfo"]["nextPaymentDateTime"] = _wire(end + timedelta(seconds=1))
    elif tamper == "topup":
        acquisitions["acq_first"]["subscriptionInfo"]["subscriptionEvent"] = "TOPUP_CHARGE"
    else:
        acquisitions["acq_first"]["orderStatus"] = "ORDER_FULLY_REFUNDED"
    with pytest.raises(service.BillingError, match=error):
        waffo_subscriptions.reconcile(session, account, order)
    session.rollback()
    assert order.status == "created"
    assert session.scalar(select(MembershipGrant)) is None


def test_monthly_period_keeps_calendar_anchor(session, recurring):
    _, account, order, remote, _, _, _, _ = recurring
    january = datetime(2024, 1, 31, 14, 4, tzinfo=timezone.utc)
    february = datetime(2024, 2, 29, 14, 4, tzinfo=timezone.utc)
    march = datetime(2024, 3, 31, 14, 4, tzinfo=timezone.utc)
    order.checkout_context = {**order.checkout_context, "subscription_interval": "month"}
    session.commit()
    remote["productInfo"].update(
        periodType="MONTHLY", startDateTime=_wire(january), nextPaymentDateTime=_wire(march), currentPeriod="2",
    )
    assert waffo_subscriptions.reconcile(session, account, order)["status"] == "paid"
    grant = session.scalar(select(MembershipGrant))
    assert (grant.starts_at, grant.ends_at) == (january, february)


def test_user_cancellation_requires_explicit_confirmation_and_preserves_paid_grant(session, recurring, monkeypatch):
    buyer, account, order, remote, _, _, _, end = recurring
    waffo_subscriptions.reconcile(session, account, order)
    requests = []

    def cancel(**kwargs):
        requests.append(kwargs)
        assert kwargs["provider_subscription_id"] == REFERENCE
        assert "scheduled_cancel_at" not in kwargs
        return {"subscriptionId": REFERENCE}

    monkeypatch.setattr(waffo, "cancel_subscription", cancel)
    with pytest.raises(service.BillingError, match="not yet confirmed"):
        service.cancel_subscription(session, buyer.id)
    session.rollback()
    assert session.scalar(select(Subscription)).canceled_at is None
    assert len(requests) == 1

    def stop(**kwargs):
        result = cancel(**kwargs)
        remote["subscriptionStatus"] = "MERCHANT_CANCELLED"
        return result

    monkeypatch.setattr(waffo, "cancel_subscription", stop)
    service.cancel_subscription(session, buyer.id)
    sub = session.scalar(select(Subscription))
    assert sub.canceled_at is not None
    assert sub.cancellation_context["waffo_state"] == "MERCHANT_CANCELLED"
    assert session.scalar(select(MembershipGrant)).status == "active"
    assert service.access_until(session, buyer.id) == end

    service.cancel_subscription(session, buyer.id)
    assert len(requests) == 2
    waffo_subscriptions.assert_can_start(session, buyer.id)


def test_admin_immediate_revocation_fences_late_paid_period(session, recurring, monkeypatch):
    buyer, account, order, remote, _, add_period, _, end = recurring
    waffo_subscriptions.reconcile(session, account, order)

    def cancel(**kwargs):
        assert "scheduled_cancel_at" not in kwargs
        remote["subscriptionStatus"] = "MERCHANT_CANCELLED"
        return {"subscriptionId": REFERENCE}

    monkeypatch.setattr(waffo, "cancel_subscription", cancel)
    result = service.revoke_membership(session, user_id=buyer.id, mode="now", reason="test", actor_id=1)
    assert result["waffo_canceled"]
    event = session.scalar(select(AuditLog).where(AuditLog.action == "membership_revoke"))
    assert event.payload["revoked_waffo_subscription_bindings"] == [{"account_code": account.code, "subscription_id": REFERENCE}]
    sub = session.scalar(select(Subscription))
    assert sub.status == "canceled"

    add_period(2, "acq_delayed")
    remote["productInfo"].update(currentPeriod="2", nextPaymentDateTime=_wire(end + timedelta(weeks=1)))
    waffo_subscriptions.handle_event(session, account, order, PERIOD_EVENT, _notice(order))
    assert [grant.status for grant in session.scalars(select(MembershipGrant).order_by(MembershipGrant.order_id))] == [
        "revoked", "revoked",
    ]
    assert service.access_until(session, buyer.id) is None


def test_paid_period_notice_waits_for_authoritative_inquiry(session, recurring):
    _, account, order, remote, _, _, _, _ = recurring
    original_payments = remote["paymentDetails"]
    remote["paymentDetails"] = []
    event = {**_notice(order), "paymentDetails": original_payments}
    with pytest.raises(service.BillingError, match="has not reached the subscription inquiry"):
        waffo_subscriptions.handle_event(session, account, order, PERIOD_EVENT, event)
    session.rollback()
    assert order.status == "created"
    remote["paymentDetails"] = [{**original_payments[0], "orderStatus": "PAY_IN_PROGRESS"}]
    with pytest.raises(service.BillingError, match="has not reached the subscription inquiry"):
        waffo_subscriptions.handle_event(session, account, order, PERIOD_EVENT, event)
    session.rollback()
    remote["paymentDetails"] = original_payments
    assert waffo_subscriptions.handle_event(session, account, order, PERIOD_EVENT, event)["status"] == "paid"


def test_renewal_rejects_shift_to_different_billing_anchor(session, recurring):
    _, account, order, remote, _, add_period, start, end = recurring
    waffo_subscriptions.reconcile(session, account, order)
    remote["productInfo"].update(
        startDateTime=_wire(start + timedelta(weeks=1)), currentPeriod="2",
        nextPaymentDateTime=_wire(end + timedelta(weeks=2)),
    )
    add_period(2, "acq_shifted")
    with pytest.raises(service.BillingError, match="billing anchor changed"):
        waffo_subscriptions.reconcile(session, account, order)
    session.rollback()
    assert len(list(session.scalars(select(CommerceSettlement)))) == 1
