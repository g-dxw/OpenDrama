"""Recover Waffo subscription periods from authenticated recurring payment records."""

from calendar import monthrange
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_hex

from sqlalchemy import select, text

from app.modules.billing import commerce, waffo, waffo_accounts, waffo_commerce
from app.modules.billing.commerce_models import CommerceSettlement, MembershipGrant
from app.modules.billing.models import Order, PlanVersion, Subscription
from app.modules.billing.service import INTERVAL_OF, BillingError, _is_recurring, active_subscription

PROVIDER = waffo_accounts.PROVIDER
_EVENTS = {"SUBSCRIPTION_STATUS_NOTIFICATION", "SUBSCRIPTION_PERIOD_CHANGED_NOTIFICATION"}
_TERMINAL = {"CLOSE", "MERCHANT_CANCELLED", "USER_CANCELLED", "CHANNEL_CANCELLED", "PLATFORM_CANCELLED", "EXPIRED"}
_STATES = _TERMINAL | {"ACTIVE", "IN_PROGRESS", "AUTHORIZATION_REQUIRED"}
_CADENCES = {"week": ("WEEKLY", 1), "month": ("MONTHLY", 1), "year": ("MONTHLY", 12)}


def _invalid(message):
    return BillingError(409, message)


def _number(value):
    if isinstance(value, str) and value.isascii() and value.isdigit() and 0 < len(value) <= 4:
        value = int(value)
    if type(value) is not int or not 1 <= value <= 9999:
        raise _invalid("Waffo returned an invalid subscription period")
    return value


def _instant(value):
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError
        return moment.astimezone(timezone.utc)
    except (ValueError, AttributeError, OverflowError):
        raise _invalid("Waffo returned an invalid subscription timestamp") from None


def _wire(moment):
    return moment.isoformat().replace("+00:00", "Z")


def _boundary(anchor, cadence, offset):
    unit, step = cadence
    try:
        if unit == "WEEKLY":
            return anchor + timedelta(weeks=step * offset)
        year, month = divmod(anchor.year * 12 + anchor.month - 1 + step * offset, 12)
        return anchor.replace(year=year, month=month + 1, day=min(anchor.day, monthrange(year, month + 1)[1]))
    except (OverflowError, ValueError):
        raise _invalid("Waffo subscription period is outside the supported calendar") from None


def _window(initial, remote, period):
    product = remote.get("productInfo")
    interval = initial.checkout_context.get("subscription_interval", INTERVAL_OF.get(initial.product_snapshot.get("code"), "month"))
    cadence = _CADENCES.get(interval)
    if (not cadence or not isinstance(product, dict)
            or (product.get("periodType"), _number(product.get("periodInterval"))) != cadence):
        raise _invalid("Waffo cadence does not match the frozen subscription")
    current = _number(product.get("currentPeriod"))
    anchor = _instant(product.get("startDateTime"))
    if initial.checkout_context.get("waffo_billing_anchor") not in (None, _wire(anchor)):
        raise _invalid("Waffo subscription billing anchor changed")
    if period > current:
        raise _invalid("Waffo has not confirmed the requested billing period")
    # Payment rows carry a period number, not dates. Verify the current
    # provider boundary before projecting the immutable cadence to any period.
    boundary = product.get("nextPaymentDateTime")
    if not boundary and _state(remote) in _TERMINAL:
        boundary = product.get("endDateTime")
    if not boundary or _instant(boundary) != _boundary(anchor, cadence, current):
        raise _invalid("Waffo subscription billing boundaries could not be verified")
    initial.checkout_context = {**initial.checkout_context, "waffo_billing_anchor": _wire(anchor)}
    return _boundary(anchor, cadence, period - 1), _boundary(anchor, cadence, period)


def _state(remote):
    state = remote.get("subscriptionStatus")
    if not isinstance(state, str) or state not in _STATES:
        raise _invalid("Waffo subscription status could not be verified")
    return state


def _initial_orders(db, user_id):
    return list(db.scalars(select(Order).where(
        Order.user_id == user_id, Order.provider == PROVIDER, Order.product_type == "membership",
        Order.billing_mode == "subscription", Order.checkout_key.is_not(None),
    ).order_by(Order.id).execution_options(populate_existing=True)))


def lookup(account, order):
    try:
        remote = waffo.get_subscription(
            **waffo_accounts.credentials(account), order_no=order.order_no, provider_subscription_id=order.subscription_ref,
        )
    except waffo.WaffoError:
        raise BillingError(502, "Waffo subscription lookup is temporarily unavailable") from None
    if remote is not None:
        _state(remote)
        if (not waffo_commerce._id(remote.get("subscriptionId"))
                or remote.get("merchantSubscriptionId") != order.order_no
                or remote.get("subscriptionRequest") != waffo.request_reference(order.order_no)
                or order.subscription_ref not in (None, remote["subscriptionId"])
                or waffo_commerce._merchant_value(remote) != account.config["merchant_id"]
                or remote.get("currency") != order.currency
                or waffo_commerce.amount_minor(remote.get("amount"), order.currency) != order.amount_cents):
            raise _invalid("Waffo subscription does not match the original merchant, order and amount")
    return remote


def _lock_initial(db, account, initial):
    commerce.wallet_lock(db, initial.user_id)
    order = db.get(Order, initial.id, with_for_update=True, populate_existing=True)
    if (order is None or order.provider != PROVIDER or order.account_code != account.code
            or order.billing_mode != "subscription" or order.product_type != "membership" or not order.checkout_key
            or order.checkout_context.get("gateway_mode") != account.config["mode"] or order.currency != account.config["currency"]):
        raise _invalid("Waffo event does not match an original subscription order")
    return order


def _attach(db, account, initial, reference):
    identity = f"waffo-subscription:{account.identity}:{reference}"
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:identity))"), {"identity": identity})
    other = db.scalar(select(Order.id).where(
        Order.id != initial.id, Order.provider == PROVIDER, Order.account_code == account.code,
        Order.subscription_ref == reference, Order.checkout_key.is_not(None),
    ).execution_options(include_all_tenants=True).limit(1))
    if other is not None or initial.subscription_ref not in (None, reference):
        raise _invalid("Waffo subscription is already bound to another order")
    initial.subscription_ref = reference


def _row(db, initial):
    row = db.scalar(select(Subscription).where(
        Subscription.account_code == initial.account_code, Subscription.waffo_subscription_id == initial.subscription_ref,
    ).with_for_update().execution_options(populate_existing=True))
    if row and (row.user_id != initial.user_id or row.plan_version_id != initial.plan_version_id):
        raise _invalid("Waffo subscription owner or plan changed")
    return row


def _remember(row, remote):
    if row is None:
        return
    state = _state(remote)
    context = row.cancellation_context or {}
    if context.get("waffo_state") in _TERMINAL and state not in _TERMINAL:
        return
    row.cancellation_context = {**context, "waffo_state": state}
    if state in _TERMINAL:
        row.canceled_at = row.canceled_at or commerce.now()


def _payment_rows(remote):
    details = remote.get("paymentDetails")
    if not isinstance(details, (list, type(None))) or any(not isinstance(row, dict) for row in details or []):
        raise _invalid("Waffo returned malformed subscription payment details")
    charges = {}
    for row in details or []:
        if row.get("orderStatus") != "PAY_SUCCESS":
            continue
        period = _number(row.get("period"))
        acquisition = waffo_commerce._id(row.get("acquiringOrderId"))
        if not acquisition or (period in charges and charges[period] != row):
            raise _invalid("Waffo returned conflicting successful charges for a subscription period")
        charges[period] = row
    return sorted(charges.items())


def _verify_payment(account, initial, period, row):
    acquisition = row["acquiringOrderId"]
    if (row.get("orderCurrency") != initial.currency
            or waffo_commerce.amount_minor(row.get("orderAmount"), initial.currency) != initial.amount_cents):
        raise _invalid("Waffo recurring charge does not match the frozen amount")
    try:
        paid = waffo.get_order(**waffo_accounts.credentials(account), provider_order_id=acquisition)
    except waffo.WaffoError:
        raise BillingError(502, "Waffo recurring payment lookup is temporarily unavailable") from None
    link = paid.get("subscriptionInfo") if paid else None
    if (not isinstance(link, dict) or link.get("subscriptionId") != initial.subscription_ref
            or link.get("subscriptionRequest") != waffo.request_reference(initial.order_no)
            or link.get("subscriptionEvent") not in (None, "PERIOD_CHARGE")
            or _number(link.get("period")) != period or paid.get("acquiringOrderId") != acquisition
            or waffo_commerce._merchant_value(paid) != account.config["merchant_id"]
            or paid.get("orderStatus") != "PAY_SUCCESS" or paid.get("orderCurrency") != initial.currency
            or waffo_commerce.amount_minor(paid.get("orderAmount"), initial.currency) != initial.amount_cents):
        raise _invalid("Waffo order inquiry did not verify a paid charge for this subscription period")


def _advance(db, initial, order, start, end, remote):
    row = _row(db, initial)
    revoked = commerce._administratively_revoked_membership(db, order)
    if row is None:
        version = db.get(PlanVersion, initial.plan_version_id)
        if version is None:
            raise _invalid("Original Waffo subscription plan is unavailable")
        previous = active_subscription(db, initial.user_id, for_update=True)
        pass_end = None
        if previous and not revoked:
            if _is_recurring(previous) and not previous.canceled_at:
                raise _invalid("Waffo renewal conflicts with another active subscription")
            carry = [previous.pass_end, previous.current_period_end if not previous.managed_commerce else None]
            pass_end = max((commerce.aware(value) for value in carry if value is not None), default=None)
            previous.status = "canceled"
            db.flush()
        row = Subscription(
            tenant_id=initial.tenant_id, user_id=initial.user_id, plan_id=version.plan_id, plan_version_id=version.id,
            account_code=initial.account_code, waffo_subscription_id=initial.subscription_ref, managed_commerce=True,
            billing_mode="subscription", pass_end=pass_end, status="canceled" if revoked else "active",
            current_period_start=start, current_period_end=end, source_drama_id=initial.source_drama_id,
        )
        db.add(row)
    elif not revoked and end > commerce.aware(row.current_period_end):
        row.current_period_start, row.current_period_end = start, end
    _remember(row, remote)


def _settle(db, account, initial, remote, period, payment):
    reference = remote["subscriptionId"]
    _attach(db, account, initial, reference)
    acquisition = payment["acquiringOrderId"]
    txn = waffo_commerce.acquisition_key(account, acquisition)
    invoice = "waffo-period:" + sha256(f"{account.identity}:{reference}:{period}".encode()).hexdigest()
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:txn))"), {"txn": txn})
    earlier = db.scalar(select(Order).where(
        (Order.invoice_id == invoice) | (Order.external_txn_id == txn),
    ).execution_options(include_all_tenants=True))
    if earlier is not None:
        if (earlier.invoice_id != invoice or earlier.external_txn_id != txn or earlier.user_id != initial.user_id
                or earlier.tenant_id != initial.tenant_id or earlier.subscription_ref != reference
                or db.get(CommerceSettlement, txn) is None):
            raise _invalid("Waffo recurring charge is already bound to another period or order")
        return
    start, end = _window(initial, remote, period)
    _verify_payment(account, initial, period, payment)
    if db.scalar(select(MembershipGrant.id).join(Order, Order.id == MembershipGrant.order_id).where(
        Order.provider == PROVIDER, Order.account_code == account.code, Order.subscription_ref == reference,
        MembershipGrant.starts_at < end, MembershipGrant.ends_at > start,
    ).limit(1)):
        raise _invalid("Waffo charge overlaps a previously settled subscription period")
    if period == 1:
        if initial.status != "created":
            raise _invalid("Original Waffo subscription order requires manual reconciliation")
        order = initial
    else:
        order = Order(
            tenant_id=initial.tenant_id, order_no=token_hex(12), user_id=initial.user_id, provider=PROVIDER,
            account_code=account.code, subscription_ref=reference, product_type="membership",
            product_snapshot=dict(initial.product_snapshot), plan_version_id=initial.plan_version_id,
            amount_cents=initial.amount_cents, currency=initial.currency, billing_mode="subscription",
            status="created", source_drama_id=initial.source_drama_id, attribution_pid=initial.attribution_pid,
            checkout_context={"gateway_mode": account.config["mode"], "waffo_initial_order_no": initial.order_no},
        )
        db.add(order)
    order.invoice_id, order.external_txn_id = invoice, txn
    order.checkout_context = {
        **order.checkout_context, "provider_order_id": acquisition, "provider_trade_no": acquisition,
        "provider_amount_paid": initial.amount_cents, "provider_period_number": period, "provider_period_end": _wire(end),
    }
    db.flush()
    _advance(db, initial, order, start, end, remote)
    db.flush()
    commerce.settle_order(db, order, txn, start, end)


def _reconcile(db, account, initial, remote):
    reference = remote["subscriptionId"]
    _attach(db, account, initial, reference)
    for period, payment in _payment_rows(remote):
        # The shared settlement commits atomically for each charge. Reacquire
        # both locks before handling the next charge or lifecycle update.
        initial = _lock_initial(db, account, initial)
        _settle(db, account, initial, remote, period, payment)
    initial = _lock_initial(db, account, initial)
    _remember(_row(db, initial), remote)
    db.commit()
    return {"order_no": initial.order_no, "status": initial.status}


def handle_event(db, account, initial_order, kind, result):
    if kind not in _EVENTS:
        return {"ignored": kind}
    initial = _lock_initial(db, account, initial_order)
    remote = lookup(account, initial)
    if (remote is None or result.get("subscriptionId") != remote["subscriptionId"]
            or result.get("merchantSubscriptionId") not in (None, initial.order_no)
            or result.get("subscriptionRequest") not in (None, waffo.request_reference(initial.order_no))):
        raise _invalid("Waffo notification did not match the authenticated original subscription")
    # A successful charge listed in a signed period notice can precede its
    # visibility in the inquiry. Return a retry until inquiry sees that charge.
    if kind == "SUBSCRIPTION_PERIOD_CHANGED_NOTIFICATION":
        notified = {(period, charge["acquiringOrderId"]) for period, charge in _payment_rows(result)}
        seen = {(period, charge["acquiringOrderId"]) for period, charge in _payment_rows(remote)}
        if not notified.issubset(seen):
            raise _invalid("Waffo recurring charge has not reached the subscription inquiry yet")
    return _reconcile(db, account, initial, remote)


def reconcile(db, account, initial_order):
    initial = _lock_initial(db, account, initial_order)
    remote = lookup(account, initial)
    if remote is None:
        return {"order_no": initial.order_no, "pending": True}
    return _reconcile(db, account, initial, remote)


def assert_can_start(db, user_id):
    for initial in _initial_orders(db, user_id):
        if initial.status == "created" or initial.checkout_context.get("checkout_expired"):
            continue
        account = waffo_commerce.account_for_order(db, initial)
        remote = lookup(account, initial)
        if remote is None or _state(remote) not in _TERMINAL:
            raise _invalid("Resolve your existing Waffo subscription before purchasing another subscription")


def revocable_bindings(db, user_id):
    bindings = []
    for initial in _initial_orders(db, user_id):
        if initial.checkout_context.get("checkout_expired"):
            continue
        if not initial.subscription_ref:
            account = waffo_commerce.account_for_order(db, initial)
            remote = lookup(account, initial)
            if remote is None:
                raise _invalid("Waffo must verify any pending subscription before immediate revocation")
            _attach(db, account, initial, remote["subscriptionId"])
        bindings.append(dict(account_code=initial.account_code, subscription_id=initial.subscription_ref))
    return bindings


def _original(db, row):
    matches = [order for order in _initial_orders(db, row.user_id)
               if order.account_code == row.account_code and order.subscription_ref == row.waffo_subscription_id]
    if len(matches) != 1:
        raise _invalid("Original Waffo subscription order is unavailable")
    return matches[0]


def _cancel(db, initial, *, row=None):
    account = waffo_commerce.account_for_order(db, initial)
    remote = lookup(account, initial)
    if remote is None:
        raise _invalid("Waffo subscription could not be found")
    if _state(remote) in _TERMINAL:
        _remember(row, remote)
        return
    try:
        waffo.cancel_subscription(
            **waffo_accounts.credentials(account), provider_subscription_id=initial.subscription_ref,
            requested_at=_wire(commerce.now().replace(microsecond=0)),
        )
    except waffo.WaffoError:
        raise BillingError(502, "Waffo subscription cancellation could not be confirmed") from None
    confirmed = lookup(account, initial)
    if confirmed is None or _state(confirmed) not in _TERMINAL:
        raise _invalid("Waffo has not yet confirmed cancellation")
    _remember(row, confirmed)


def cancel_renewal(db, row):
    # Waffo can stop future charges immediately. The already-paid local
    # MembershipGrant remains valid through its verified billing period.
    _cancel(db, _original(db, row), row=row)


def cancel_known_bindings(db, bindings, row):
    if not bindings:
        return
    user_id = row.user_id if row is not None else None
    for binding in bindings:
        initial = db.scalar(select(Order).where(
            Order.provider == PROVIDER, Order.account_code == binding["account_code"],
            Order.subscription_ref == binding["subscription_id"], Order.checkout_key.is_not(None),
        ).execution_options(populate_existing=True))
        if initial is None or (user_id is not None and initial.user_id != user_id):
            raise _invalid("Original Waffo subscription order is unavailable")
        current = row if row is not None and row.waffo_subscription_id == initial.subscription_ref else None
        _cancel(db, initial, row=current)
