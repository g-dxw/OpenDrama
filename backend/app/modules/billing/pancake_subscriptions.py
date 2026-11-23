"""Correlate Pancake's independent charge and paid-period deliveries."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_hex

from sqlalchemy import select, text

from app.modules.billing import commerce, pancake_accounts, waffo_pancake as pancake
from app.modules.billing.commerce_models import GatewayAccount, MembershipGrant
from app.modules.billing.models import Order, PancakePeriodEvidence, PlanVersion, Subscription
from app.modules.billing.service import BillingError, INTERVAL_OF, _is_recurring, active_subscription


PROVIDER = pancake_accounts.PROVIDER
_PERIOD_EVENTS = {"subscription.activated", "subscription.renewed", "subscription.recovered"}
_LIFECYCLE_EVENTS = {
    "subscription.canceling", "subscription.canceled", "subscription.uncanceled", "subscription.past_due",
}
_CHARGE_EVENT = "subscription.payment_succeeded"
_TERMINAL = {"canceled", "closed", "expired"}
_NON_RENEWING = _TERMINAL | {"canceling"}
_REMOTE_STATES = _NON_RENEWING | {"active", "pending", "trialing", "past_due"}
_INTERVALS = {"week": "weekly", "month": "monthly", "year": "yearly"}


def _reference(value, prefix):
    if (isinstance(value, str) and value.startswith(prefix) and len(prefix) < len(value) <= 100
            and value[len(prefix):].isascii() and value[len(prefix):].isalnum()):
        return value
    raise BillingError(409, "Invalid Waffo Pancake payment or subscription reference")


def _period(value):
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise BillingError(409, "Waffo Pancake subscription period could not be verified")
    return value


def _date(value):
    if not isinstance(value, str):
        raise BillingError(409, "Invalid Waffo Pancake subscription period window")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is not None:
            return result.astimezone(timezone.utc)
        # Pancake's signed lifecycle events can contain an ISO UTC date.
        if len(value) == 10:
            return result.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        pass
    raise BillingError(409, "Invalid Waffo Pancake subscription period window")


def _window(data):
    start, end = _date(data.get("currentPeriodStart")), _date(data.get("currentPeriodEnd"))
    if end <= start or end - start > timedelta(days=370):
        raise BillingError(409, "Invalid Waffo Pancake subscription period window")
    return start, end


def _account(db, code):
    account = db.get(GatewayAccount, (PROVIDER, code))
    if account is None:
        raise BillingError(503, "Original Waffo Pancake subscription account is unavailable")
    return account


def _lock_initial(db, account, order):
    commerce.wallet_lock(db, order.user_id)
    initial = db.get(Order, order.id, with_for_update=True, populate_existing=True)
    if (initial is None or initial.user_id != order.user_id or initial.provider != PROVIDER
            or initial.account_code != account.code or initial.product_type != "membership"
            or initial.billing_mode != "subscription" or initial.checkout_key is None
            or (initial.checkout_context or {}).get("gateway_mode") != account.config["mode"]
            or (initial.checkout_context or {}).get("store_id") != account.config["store_id"]):
        raise BillingError(409, "Waffo Pancake subscription does not match the original order")
    return initial


def _attach(db, account, initial, remote):
    reference = _reference(remote.get("id"), "ORD_")
    context = initial.checkout_context
    if initial.subscription_ref not in (None, reference) or context.get("provider_order_id") not in (None, reference):
        raise BillingError(409, "Waffo Pancake subscription identity changed")
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:reference))"),
               {"reference": f"pancake-order:{account.identity}:{reference}"})
    other = db.scalar(select(Order.id).where(
        Order.provider == PROVIDER, Order.account_code == account.code, Order.subscription_ref == reference,
        Order.checkout_key.is_not(None), Order.id != initial.id,
    ).execution_options(include_all_tenants=True).limit(1))
    if other is not None:
        raise BillingError(409, "Waffo Pancake subscription belongs to another order")
    initial.subscription_ref = reference
    initial.checkout_context = {**context, "provider_order_id": reference}
    return reference


def _subscription_row(db, initial, reference):
    row = db.scalar(select(Subscription).where(
        Subscription.account_code == initial.account_code, Subscription.pancake_subscription_id == reference,
    ).with_for_update().execution_options(populate_existing=True))
    if row and (row.user_id != initial.user_id or row.plan_version_id != initial.plan_version_id):
        raise BillingError(409, "Waffo Pancake subscription owner or plan changed")
    return row


def _remote_state(remote):
    state = remote.get("status")
    if state not in _REMOTE_STATES:
        raise BillingError(409, "Waffo Pancake renewal status could not be verified")
    return state


def _remember_state(row, remote):
    state = _remote_state(remote)
    context = row.cancellation_context or {}
    previous = context.get("pancake_state")
    if previous in _TERMINAL and state not in _TERMINAL:
        return
    row.cancellation_context = {**context, "pancake_state": state}
    if state in _NON_RENEWING:
        row.canceled_at = row.canceled_at or commerce.now()
    elif previous == "canceling":
        row.canceled_at = None


def _record_charge(db, account, initial, reference, data, remote):
    from app.modules.billing import pancake_commerce

    payment_id = _reference(data.get("paymentId"), "PAY_")
    reported_period = data.get("periodNumber")
    if reported_period is not None:
        _period(reported_period)
    payment_key = pancake_commerce.payment_key(account, payment_id)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:reference))"), {"reference": payment_key})
    earlier = db.scalar(select(PancakePeriodEvidence).where(
        PancakePeriodEvidence.payment_key == payment_key,
    ).execution_options(include_all_tenants=True))
    if earlier is not None:
        if earlier.initial_order_id != initial.id or reported_period not in (None, earlier.period_number):
            raise BillingError(409, "Waffo Pancake charge belongs to a different subscription period")
        if earlier.settled_order_id is not None:
            return earlier, None
    payment = pancake_commerce.verified_payment(account, initial, remote, payment_id)
    if payment.get("id") != payment_id or payment.get("orderId") != reference or payment.get("status") != "succeeded":
        raise BillingError(409, "Waffo Pancake charge does not match the frozen order")
    authenticated_period = payment.get("periodNumber")
    if authenticated_period is not None and reported_period not in (None, authenticated_period):
        raise BillingError(409, "Waffo Pancake charge has conflicting period numbers")
    period_number = _period(authenticated_period if authenticated_period is not None else reported_period)
    if earlier is not None and earlier.period_number != period_number:
        raise BillingError(409, "Waffo Pancake charge belongs to a different subscription period")
    evidence = _evidence(db, initial, period_number)
    if evidence.payment_id not in (None, payment_id):
        raise BillingError(409, "Waffo Pancake period contains two different successful charges")
    evidence.payment_id = payment_id
    evidence.payment_key = payment_key
    return evidence, payment


def _evidence(db, initial, period_number):
    row = db.get(PancakePeriodEvidence, (initial.id, period_number))
    if row is None:
        row = PancakePeriodEvidence(tenant_id=initial.tenant_id, initial_order_id=initial.id, period_number=period_number)
        db.add(row)
    return row


def _record_period(db, initial, period_number, data):
    expected_period = _INTERVALS[INTERVAL_OF.get(initial.product_snapshot.get("code"), "month")]
    if data.get("billingPeriod") not in (None, expected_period):
        raise BillingError(409, "Waffo Pancake period does not match the frozen billing interval")
    start, end = _window(data)
    evidence = _evidence(db, initial, period_number)
    if evidence.period_start is not None and (start, end) != (evidence.period_start, evidence.period_end):
        raise BillingError(409, "Waffo Pancake subscription period changed")
    evidence.period_start, evidence.period_end = start, end
    return evidence


def _advance_period(db, initial, order, reference, start, end, remote):
    row = _subscription_row(db, initial, reference)
    if row is None:
        version = db.get(PlanVersion, initial.plan_version_id)
        if version is None:
            raise BillingError(409, "Original Waffo Pancake subscription plan is unavailable")
        previous = active_subscription(db, initial.user_id, for_update=True)
        revoked = commerce._administratively_revoked_membership(db, order)
        superseded = bool(previous and not previous.canceled_at and _is_recurring(previous))
        pass_end = None
        if previous and not revoked and not superseded:
            windows = [previous.pass_end, previous.current_period_end if not previous.managed_commerce else None]
            pass_end = max((commerce.aware(value) for value in windows if value is not None), default=None)
            previous.status = "canceled"
            db.flush()
        row = Subscription(
            tenant_id=initial.tenant_id, user_id=initial.user_id, plan_id=version.plan_id, plan_version_id=version.id,
            account_code=initial.account_code, pancake_subscription_id=reference, managed_commerce=True,
            billing_mode="subscription", pass_end=pass_end, status="canceled" if revoked or superseded else "active",
            current_period_start=start, current_period_end=end, source_drama_id=initial.source_drama_id,
        )
        db.add(row)
    elif end > commerce.aware(row.current_period_end):
        row.current_period_start, row.current_period_end = start, end
    _remember_state(row, remote)


def _settle(db, account, initial, reference, evidence, remote, payment=None):
    from app.modules.billing import pancake_commerce

    if evidence.settled_order_id is not None:
        settled = db.get(Order, evidence.settled_order_id)
        db.commit()
        return {"order_no": settled.order_no, "replayed": True}
    if evidence.payment_key is None or evidence.period_start is None:
        db.commit()
        return {"order_no": initial.order_no, "pending": True}
    start, end = evidence.period_start, evidence.period_end
    # A payment could have been refunded while its period event was in
    # transit. Recheck any charge proof saved by an earlier transaction.
    if payment is None:
        payment = pancake_commerce.verified_payment(account, initial, remote, evidence.payment_id)
    if payment.get("periodNumber") not in (None, evidence.period_number):
        raise BillingError(409, "Waffo Pancake charge changed its subscription period")
    tax = pancake_accounts.amount_minor((payment.get("snapshotAmountDetails") or {}).get("taxAmount", "0"), initial.currency, allow_zero=True)
    if tax > initial.amount_cents:
        raise BillingError(409, "Waffo Pancake payment tax exceeds the order amount")
    if db.scalar(select(Order.id).where(
        Order.external_txn_id == evidence.payment_key,
    ).execution_options(include_all_tenants=True).limit(1)):
        raise BillingError(409, "Waffo Pancake charge is already attached to an order")
    if db.scalar(select(MembershipGrant.id).join(Order, Order.id == MembershipGrant.order_id).where(
        Order.provider == PROVIDER, Order.account_code == account.code, Order.subscription_ref == reference,
        MembershipGrant.starts_at < end, MembershipGrant.ends_at > start,
    ).limit(1)):
        raise BillingError(409, "Waffo Pancake charge overlaps a previously settled period")
    if evidence.period_number == 1:
        if initial.status != "created":
            raise BillingError(409, "Original Waffo Pancake order requires manual reconciliation")
        order = initial
    else:
        order = Order(
            tenant_id=initial.tenant_id, order_no=token_hex(12), user_id=initial.user_id, provider=PROVIDER,
            account_code=initial.account_code, subscription_ref=reference, product_type="membership",
            product_snapshot=dict(initial.product_snapshot), plan_version_id=initial.plan_version_id,
            amount_cents=initial.amount_cents, currency=initial.currency, billing_mode="subscription",
            status="created", source_drama_id=initial.source_drama_id, attribution_pid=initial.attribution_pid,
            checkout_context={
                key: initial.checkout_context[key] for key in ("gateway_mode", "external_product_id", "store_id",
                                                               "provider_order_id")
            } | {"pancake_initial_order_no": initial.order_no},
        )
        db.add(order)
    order.invoice_id = "pancake-period:" + sha256(
        f"{account.identity}:{reference}:{evidence.period_number}".encode()
    ).hexdigest()
    order.external_txn_id = evidence.payment_key
    order.checkout_context = {
        **order.checkout_context, "provider_trade_no": evidence.payment_id, "provider_amount_paid": initial.amount_cents,
        "provider_tax_minor": tax,
        "provider_period_number": evidence.period_number, "provider_period_end": end.isoformat(),
    }
    db.flush()
    evidence.settled_order_id = order.id
    _advance_period(db, initial, order, reference, start, end, remote)
    db.flush()
    return commerce.settle_order(db, order, evidence.payment_key, start, end)


def handle_event(db, account, initial_order, event):
    from app.modules.billing import pancake_commerce

    event_type = event["eventType"]
    if event_type != _CHARGE_EVENT and event_type not in (_PERIOD_EVENTS | _LIFECYCLE_EVENTS):
        return {"ignored": event_type}
    initial = _lock_initial(db, account, initial_order)
    context = initial.checkout_context
    data = event["data"]
    wire_mode = "test" if account.config["mode"] == "test" else "prod"
    if (event.get("mode") != wire_mode or event.get("storeId") != context["store_id"]
            or data.get("orderMerchantExternalId") not in (None, initial.order_no) or data.get("currency") != initial.currency):
        raise BillingError(409, "Waffo Pancake event does not match the frozen subscription")
    remote = pancake_commerce.remote_order(db, account, initial)
    if not remote or remote.get("id") != data.get("orderId"):
        raise BillingError(409, "Waffo Pancake subscription could not yet be correlated")
    reference = _attach(db, account, initial, remote)
    row = _subscription_row(db, initial, reference)
    if row is not None:
        _remember_state(row, remote)
    if event_type in _LIFECYCLE_EVENTS:
        db.commit()
        return {"order_no": initial.order_no}
    payment = None
    if event_type == _CHARGE_EVENT:
        if data.get("paymentStatus") != "succeeded":
            raise BillingError(409, "Waffo Pancake has not confirmed a successful recurring payment")
        if data.get("chargedAmount") is not None and pancake_accounts.amount_minor(
            data["chargedAmount"], initial.currency, allow_zero=True,
        ) != initial.amount_cents:
            raise BillingError(409, "Waffo Pancake charged amount does not match the frozen subscription")
        evidence, payment = _record_charge(db, account, initial, reference, data, remote)
    else:
        period_number = data.get("periodNumber")
        if period_number is None and _window(data) == _window(remote):
            period_number = remote.get("currentPeriodNumber")
        evidence = _record_period(db, initial, _period(period_number), data)
    return _settle(db, account, initial, reference, evidence, remote, payment)


def reconcile(db, account, initial_order, remote):
    if not remote or remote.get("currentPeriodNumber") in (None, 0):
        return {"order_no": initial_order.order_no, "pending": True}
    initial = _lock_initial(db, account, initial_order)
    reference = _attach(db, account, initial, remote)
    period_number = _period(remote.get("currentPeriodNumber"))
    evidence = _record_period(db, initial, period_number, remote)
    charges = [payment for payment in (remote.get("payments") or []) if (
        payment.get("status") == "succeeded" and payment.get("periodNumber") == period_number
    )]
    if len(charges) > 1:
        raise BillingError(409, "Waffo Pancake period contains two different successful charges")
    payment = None
    if charges:
        evidence, payment = _record_charge(db, account, initial, reference, {
            "paymentId": charges[0].get("id"), "periodNumber": period_number,
        }, remote)
    return _settle(db, account, initial, reference, evidence, remote, payment)


def _original_order(db, code, reference):
    order = db.scalar(select(Order).where(
        Order.provider == PROVIDER, Order.account_code == code, Order.subscription_ref == reference,
        Order.checkout_key.is_not(None), Order.product_type == "membership", Order.billing_mode == "subscription",
    ))
    if order is None:
        raise BillingError(409, "Original Waffo Pancake subscription order is unavailable")
    return order


def _remote_lookup(db, code, reference):
    from app.modules.billing import pancake_commerce

    account = _account(db, code)
    original = _original_order(db, code, reference)
    remote = pancake_commerce.remote_order(db, account, original)
    if not remote or remote.get("id") != reference:
        raise BillingError(409, "Waffo Pancake renewal status could not be verified")
    return account, remote


def assert_can_start(db, user_id):
    for binding in existing_bindings(db, user_id):
        code, reference = binding["account_code"], binding["subscription_id"]
        # A signed charge and period may still be pairing; check known
        # original orders even when an active Subscription row is absent.
        _, remote = _remote_lookup(db, code, reference)
        if _remote_state(remote) not in _NON_RENEWING:
            raise BillingError(409, "An active renewing Waffo Pancake subscription already exists")


def _cancel_remote(db, code, reference):
    account, remote = _remote_lookup(db, code, reference)
    if _remote_state(remote) in _NON_RENEWING:
        return remote
    try:
        pancake.cancel_subscription(**pancake_accounts.credentials(account), order_id=reference)
    except pancake.PancakeError:
        raise BillingError(502, "Waffo Pancake cancellation outcome is unconfirmed; check and retry") from None
    _, remote = _remote_lookup(db, code, reference)
    if _remote_state(remote) not in _NON_RENEWING:
        raise BillingError(409, "Waffo Pancake renewal cancellation has not been confirmed")
    return remote


def cancel_renewal(db, row):
    remote = _cancel_remote(db, row.account_code, row.pancake_subscription_id)
    _remember_state(row, remote)


def cancel_known_bindings(db, bindings, row):
    for binding in bindings:
        code, reference = binding["account_code"], binding["subscription_id"]
        if row is not None and (code, reference) == (row.account_code, row.pancake_subscription_id):
            cancel_renewal(db, row)
        else:
            _cancel_remote(db, code, reference)


def existing_bindings(db, user_id):
    rows = db.execute(select(Order.account_code, Order.subscription_ref).where(
        Order.provider == PROVIDER, Order.user_id == user_id, Order.product_type == "membership",
        Order.billing_mode == "subscription", Order.checkout_key.is_not(None), Order.subscription_ref.is_not(None),
    ).distinct().order_by(Order.account_code, Order.subscription_ref))
    return [{"account_code": code, "subscription_id": reference} for code, reference in rows]


def revocable_bindings(db, user_id):
    """Prove every submitted purchase's disposition before removing access."""
    from app.modules.billing import pancake_commerce

    pending = db.scalars(select(Order).where(
        Order.provider == PROVIDER, Order.user_id == user_id, Order.product_type == "membership",
        Order.billing_mode == "subscription", Order.checkout_key.is_not(None), Order.subscription_ref.is_(None),
        Order.status == "created",
    ).order_by(Order.id).with_for_update())
    for order in pending:
        account = _account(db, order.account_code)
        remote = pancake_commerce.remote_order(db, account, order)
        if remote:
            _attach(db, account, order, remote)
            continue
        expires_at = order.checkout_context.get("provider_checkout_expires_at")
        if expires_at is not None and _date(expires_at) <= commerce.now():
            continue
        raise BillingError(409, "An outstanding Waffo Pancake checkout must be resolved before revoking membership")
    return existing_bindings(db, user_id)
