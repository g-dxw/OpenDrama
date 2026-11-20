"""Creem recurring charges, paid periods, and cancellation evidence."""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from sqlalchemy import select, text

from app.modules.billing import commerce, creem, creem_accounts
from app.modules.billing.commerce_models import CommerceSettlement, GatewayAccount, MembershipGrant
from app.modules.billing.models import Order, PlanVersion, Subscription
from app.modules.billing.service import BillingError, _is_recurring, active_subscription


PROVIDER = creem_accounts.PROVIDER
_CANCELED = "canceled"
_SCHEDULED = "scheduled_cancel"
_LIFECYCLE = {
    "subscription.active", "subscription.canceled", "subscription.expired", "subscription.past_due",
    "subscription.paused", "subscription.scheduled_cancel", "subscription.trialing", "subscription.unpaid",
    "subscription.update",
}
_STATES = {_CANCELED, _SCHEDULED, "active", "expired", "past_due", "paused", "trialing", "unpaid"}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _id(value):
    identifier = value.get("id") if isinstance(value, dict) else value
    return identifier if isinstance(identifier, str) and 0 < len(identifier) <= 100 else None


def _account(db, code):
    account = db.get(GatewayAccount, (PROVIDER, code))
    if account is None:
        raise BillingError(503, "Original Creem subscription account is unavailable")
    return account


def _remote_subscription(account, subscription_id):
    try:
        remote = creem.get_subscription(**creem_accounts.api_credentials(account), subscription_id=subscription_id)
    except creem.CreemError:
        raise BillingError(502, "Creem subscription status is temporarily unavailable") from None
    if (_id(remote) != subscription_id or remote.get("status") not in _STATES
            or not creem.mode_matches(account.config["mode"], remote.get("mode"))):
        raise BillingError(409, "Creem subscription status could not be verified")
    return remote


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _remember_state(row, remote, *, authoritative=False):
    state = remote.get("status")
    if state not in _STATES:
        return
    context = row.cancellation_context or {}
    previous = context.get("creem_state")
    before = _date(context.get("creem_state_at"))
    stamp = _date(remote.get("updated_at"))
    # A webhook can be delayed beyond a later cancellation or reactivation.
    # Keep the most recent provider state. An authenticated current lookup can
    # break a tie, while a terminal canceled subscription can never be revived.
    if previous == _CANCELED and state != _CANCELED:
        return
    if before is not None and (stamp is None or stamp <= before) and not authoritative:
        return
    row.cancellation_context = {
        **context, "creem_state": state, "creem_state_at": (stamp or before or commerce.now()).isoformat(),
    }
    if state in {_CANCELED, _SCHEDULED}:
        row.canceled_at = row.canceled_at or _date(remote.get("canceled_at")) or stamp or commerce.now()
    elif previous == _SCHEDULED or (authoritative and row.canceled_at is not None):
        row.canceled_at = None


def assert_can_start(db, user_id):
    rows = db.scalars(select(Subscription).where(
        Subscription.user_id == user_id, Subscription.creem_subscription_id.is_not(None),
    ).order_by(Subscription.id).with_for_update().execution_options(populate_existing=True))
    for row in rows:
        if (row.cancellation_context or {}).get("creem_state") == _CANCELED:
            continue
        remote = _remote_subscription(_account(db, row.account_code), row.creem_subscription_id)
        # Expired subscriptions can still collect a retry.
        if remote["status"] not in {_CANCELED, _SCHEDULED}:
            raise BillingError(409, "An active renewing Creem subscription already exists")
        _remember_state(row, remote, authoritative=True)


def _cancel_remote(db, code, subscription_id, *, immediate=False):
    account = _account(db, code)
    acceptable = {_CANCELED} if immediate else {_CANCELED, _SCHEDULED}
    remote = _remote_subscription(account, subscription_id)
    if remote["status"] in acceptable:
        return remote
    try:
        creem.cancel_subscription(**creem_accounts.api_credentials(account), subscription_id=subscription_id,
                                  **({"immediate": True} if immediate else {}))
    except creem.CreemError:
        raise BillingError(502, "Creem cancellation outcome is unconfirmed; check the subscription and retry") from None
    # Future retries look up current state before sending a mutation, including
    # when the provider accepted a previous request whose response was lost.
    remote = _remote_subscription(account, subscription_id)
    if remote["status"] not in acceptable:
        raise BillingError(409, "Creem cancellation has not been confirmed")
    return remote


def cancel_renewal(db, row, *, immediate=False):
    remote = _cancel_remote(db, row.account_code, row.creem_subscription_id, immediate=immediate)
    _remember_state(row, remote, authoritative=True)


def cancel_known_bindings(db, bindings, row):
    for binding in bindings:
        code, subscription_id = binding["account_code"], binding["subscription_id"]
        if row is not None and (code, subscription_id) == (row.account_code, row.creem_subscription_id):
            cancel_renewal(db, row, immediate=True)
        else:
            _cancel_remote(db, code, subscription_id, immediate=True)


def existing_bindings(db, user_id):
    rows = db.execute(select(Order.account_code, Order.subscription_ref).where(
        Order.provider == PROVIDER, Order.user_id == user_id, Order.product_type == "membership",
        Order.billing_mode == "subscription", Order.checkout_key.is_not(None), Order.subscription_ref.is_not(None),
    ).distinct().order_by(Order.account_code, Order.subscription_ref))
    return [{"account_code": code, "subscription_id": reference} for code, reference in rows]


def _lock_initial(db, account, initial_id, user_id, sub_id):
    commerce.wallet_lock(db, user_id)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:reference))"),
               {"reference": f"creem-subscription:{account.identity}:{sub_id}"})
    order = db.get(Order, initial_id, with_for_update=True, populate_existing=True)
    if (order is None or order.provider != PROVIDER or order.account_code != account.code
            or order.user_id != user_id or order.product_type != "membership" or order.billing_mode != "subscription"
            or order.checkout_key is None or order.subscription_ref not in (None, sub_id)
            or order.checkout_context.get("gateway_mode") != account.config["mode"]):
        raise BillingError(409, "Creem subscription does not match its original order")
    order.subscription_ref = sub_id
    other = db.scalar(select(Order.id).where(
        Order.provider == PROVIDER, Order.account_code == account.code, Order.subscription_ref == sub_id,
        Order.checkout_key.is_not(None), Order.id != order.id,
    ).execution_options(include_all_tenants=True).limit(1))
    if other is not None:
        raise BillingError(409, "Creem subscription is already linked to another order")
    return order


def _original_transaction(db, account, order, sub_id):
    from app.modules.billing.creem_commerce import _remote_checkout, _validate_charge, _validate_checkout

    context = order.checkout_context
    transaction_id = context.get("initial_transaction_id")
    if transaction_id:
        return transaction_id
    if not context.get("provider_checkout_id"):
        raise BillingError(409, "Original Creem checkout is still being correlated")
    checkout = _remote_checkout(account, order)
    _validate_checkout(account, order, checkout)
    if _id(checkout.get("subscription")) != sub_id:
        raise BillingError(409, "Creem subscription does not match its original checkout")
    transaction_id, _, _ = _validate_charge(account, order, checkout)
    order.checkout_context = {**context, "initial_transaction_id": transaction_id,
                              "provider_initial_order_id": _id(checkout["order"])}
    db.flush()
    return transaction_id


def _fetch_transaction(account, identifier):
    try:
        return creem.get_transaction(**creem_accounts.api_credentials(account), transaction_id=identifier)
    except creem.CreemError:
        raise BillingError(502, "Creem recurring transaction lookup is unavailable") from None


def _payment_period(value):
    if type(value) is not int or not 946684800000 <= value <= 4102444800000:
        raise BillingError(409, "Creem recurring payment has an invalid billing period")
    return (_EPOCH + timedelta(milliseconds=value)).replace(microsecond=0)


def _proof(account, initial, sub_id, payment, *, first):
    amount, gross, tax = payment.get("amount"), payment.get("amount_paid"), payment.get("tax_amount")
    tax = 0 if tax is None else tax
    trade = _id(payment)
    provider_order = _id(payment.get("order"))
    expected_order = initial.checkout_context.get("provider_initial_order_id") if first else None
    if (not trade or not trade.startswith("tran_") or payment.get("status") != "paid" or payment.get("type") != "invoice"
            or not creem.mode_matches(account.config["mode"], payment.get("mode"))
            or _id(payment.get("subscription")) != sub_id or (expected_order and provider_order != expected_order)
            or payment.get("currency") != initial.currency or type(amount) is not int or amount != initial.amount_cents
            or type(tax) is not int or tax < 0 or type(gross) is not int or gross <= 0 or tax > gross
            or gross not in {amount, amount + tax} or payment.get("discount_amount") not in (None, 0)):
        raise BillingError(409, "Creem recurring charge does not match the frozen subscription")
    start, end = _payment_period(payment.get("period_start")), _payment_period(payment.get("period_end"))
    if end <= start or end - start > timedelta(days=370):
        raise BillingError(409, "Creem recurring payment has an invalid billing period")
    return trade, start, end, gross, tax


def _subscription_row(db, account, initial, sub_id):
    row = db.scalar(select(Subscription).where(
        Subscription.account_code == account.code, Subscription.creem_subscription_id == sub_id,
    ).with_for_update().execution_options(populate_existing=True))
    if row is not None and (row.user_id != initial.user_id or row.plan_version_id != initial.plan_version_id):
        raise BillingError(409, "Creem subscription owner or plan changed")
    return row


def _advance_paid_period(db, account, initial, order, sub_id, start, end):
    row = _subscription_row(db, account, initial, sub_id)
    if row is None:
        version = db.get(PlanVersion, initial.plan_version_id)
        if version is None:
            raise BillingError(409, "Original Creem subscription plan is unavailable")
        previous = active_subscription(db, order.user_id, for_update=True)
        revoked = commerce._administratively_revoked_membership(db, order)
        superseded = bool(previous and not previous.canceled_at and _is_recurring(previous))
        pass_end = None
        if not revoked and not superseded and previous:
            windows = [previous.pass_end, previous.current_period_end if not previous.managed_commerce else None]
            pass_end = max((commerce.aware(value) for value in windows if value is not None), default=None)
            previous.status = "canceled"
            db.flush()
        row = Subscription(
            user_id=order.user_id, plan_id=version.plan_id, plan_version_id=version.id, account_code=account.code,
            creem_subscription_id=sub_id, managed_commerce=True, billing_mode="subscription", pass_end=pass_end,
            status="canceled" if revoked or superseded else "active", current_period_start=start, current_period_end=end,
            source_drama_id=initial.source_drama_id,
        )
        db.add(row)
    elif end > commerce.aware(row.current_period_end):
        row.current_period_start, row.current_period_end = start, end
    return row


def _settle(db, account, initial, subscription, sub_id, proof, *, first):
    from app.modules.billing.creem_commerce import merchant_transaction_key

    trade, start, end, gross, tax = proof
    txn = merchant_transaction_key(account, trade)
    period = "creem:" + hashlib.sha256(f"{account.identity}:{sub_id}:{start.isoformat()}".encode()).hexdigest()
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:txn))"), {"txn": txn})
    previous = db.scalar(select(Order).where(Order.external_txn_id == txn).execution_options(include_all_tenants=True))
    if previous is not None:
        previous_origin = previous.checkout_context.get("creem_initial_order_no", previous.order_no)
        if (previous_origin != initial.order_no or previous.tenant_id != initial.tenant_id or previous.invoice_id != period
                or previous.checkout_context.get("provider_period_end") != end.isoformat()):
            raise BillingError(409, "Creem transaction already belongs to a different order or period")
        if previous.status in {"refund_review", "refunded", "refund_pending"} or db.get(CommerceSettlement, txn):
            db.commit()
            return {"order_no": previous.order_no, "replayed": True}
        raise BillingError(409, "Creem transaction requires manual reconciliation")
    if db.scalar(select(Order.id).where(Order.invoice_id == period).execution_options(include_all_tenants=True)):
        raise BillingError(409, "Creem already settled a payment for this subscription period")
    overlapping_period = db.scalar(select(MembershipGrant.id).join(Order, Order.id == MembershipGrant.order_id).where(
        Order.provider == PROVIDER, Order.account_code == account.code, Order.subscription_ref == sub_id,
        MembershipGrant.starts_at < end, MembershipGrant.ends_at > start,
    ).limit(1))
    if overlapping_period is not None:
        raise BillingError(409, "Creem recurring payment overlaps a previously settled period")
    if first:
        order = initial
        if order.status in {"refund_review", "refunded", "refund_pending"}:
            db.commit()
            return {"order_no": order.order_no, "replayed": True}
        if order.status != "created":
            raise BillingError(409, "Original Creem subscription order requires reconciliation")
    else:
        order = Order(
            order_no=secrets.token_hex(12), user_id=initial.user_id, provider=PROVIDER, account_code=initial.account_code,
            product_type="membership", product_snapshot=dict(initial.product_snapshot), plan_version_id=initial.plan_version_id,
            amount_cents=initial.amount_cents, currency=initial.currency, billing_mode="subscription", status="created",
            subscription_ref=sub_id, source_drama_id=initial.source_drama_id, attribution_pid=initial.attribution_pid,
            checkout_context={"creem_initial_order_no": initial.order_no, "gateway_mode": initial.checkout_context["gateway_mode"],
                              "external_product_id": initial.checkout_context["external_product_id"]},
        )
        db.add(order)
    order.invoice_id = period
    order.external_txn_id = txn
    order.checkout_context = {**order.checkout_context, "provider_trade_no": trade, "provider_amount_paid": gross,
                              "provider_tax_minor": tax, "provider_period_end": end.isoformat()}
    db.flush()
    row = _advance_paid_period(db, account, initial, order, sub_id, start, end)
    _remember_state(row, subscription)
    db.flush()
    return commerce.settle_order(db, order, txn, start, end)


def handle_event(db, account, initial_order, event_type, subscription_obj):
    if event_type != "subscription.paid" and event_type not in _LIFECYCLE:
        return {"ignored": event_type}
    sub_id = _id(subscription_obj)
    if not sub_id or not sub_id.startswith("sub_"):
        raise BillingError(409, "Creem subscription reference is invalid")
    order_id, user_id = initial_order.id, initial_order.user_id
    initial = _lock_initial(db, account, order_id, user_id, sub_id)
    if (_id(subscription_obj.get("product")) != initial.checkout_context.get("external_product_id")
            or not creem.mode_matches(account.config["mode"], subscription_obj.get("mode"))):
        raise BillingError(409, "Creem subscription does not match its frozen product")
    if event_type != "subscription.paid":
        row = _subscription_row(db, account, initial, sub_id)
        if row is not None:
            _remember_state(row, subscription_obj)
        db.commit()
        return {"order_no": initial.order_no}

    payment = subscription_obj.get("last_transaction")
    reference = subscription_obj.get("last_transaction_id")
    if not isinstance(payment, dict):
        if not _id(reference):
            raise BillingError(409, "Creem subscription does not yet have a payment")
        payment = _fetch_transaction(account, reference)
    if reference is not None and _id(payment) != reference:
        raise BillingError(409, "Creem subscription transaction references conflict")

    original = _original_transaction(db, account, initial, sub_id)
    first = _id(payment) == original
    proof = _proof(account, initial, sub_id, payment, first=first)
    if not first and initial.status == "created":
        # A renewal may arrive first. Settle the provider order linked to the
        # original checkout using its exact transaction before the later charge.
        original_payment = _fetch_transaction(account, original)
        if _id(original_payment) != original:
            raise BillingError(409, "Creem original transaction reference changed")
        original_proof = _proof(account, initial, sub_id, original_payment, first=True)
        _settle(db, account, initial, subscription_obj, sub_id, original_proof, first=True)
        initial = _lock_initial(db, account, order_id, user_id, sub_id)
    return _settle(db, account, initial, subscription_obj, sub_id, proof, first=first)
