"""Creem checkout, signed delivery, and authoritative order recovery."""

import hashlib
import secrets
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select, text

from app.modules.account.models import User
from app.modules.billing import commerce, creem, creem_accounts, gateway_accounts
from app.modules.billing.commerce_models import CommerceRefund, CommerceSettlement, GatewayAccount
from app.modules.billing.models import Order
from app.modules.billing.service import INTERVAL_OF, BillingError, billing_mode
from app.modules.billing.site_merchants import assert_site_currency
from app.modules.tenancy.consumer_scope import require_owned, site_context

PROVIDER = creem_accounts.PROVIDER


def channels(db):
    merchant = gateway_accounts.bound_account(db, PROVIDER)
    if merchant is None or merchant.status != "active" or not gateway_accounts.accepting_orders(merchant.config["mode"]):
        return []
    sale_mode = billing_mode(db)
    allowed = {kind: commerce.sale_allowed(db, kind) for kind in ("membership", "coin_pack")}
    products = [
        {field: product[field] for field in ("product_type", "product_code", "billing_mode")}
        for product in creem_accounts.catalog(db, merchant)
        if product["external_product_id"] and allowed[product["product_type"]]
        and (product["product_type"] == "coin_pack" or product["billing_mode"] == sale_mode)
    ]
    if not products:
        return []
    return [{"provider": PROVIDER, "code": merchant.code, "name": merchant.name, "enabled": True,
             "billing_modes": sorted({item["billing_mode"] for item in products}), "products": products}]


def _account_for_order(db, order):
    account = db.get(GatewayAccount, (PROVIDER, order.account_code))
    if account is None or account.config["mode"] != order.checkout_context.get("gateway_mode"):
        raise BillingError(503, "Creem order's original account is unavailable")
    if not gateway_accounts.accepting_orders(account.config["mode"]):
        raise BillingError(409, "Creem test orders are disabled in this environment")
    return account


def _return_to_order(url, order_no):
    target = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(target.query, keep_blank_values=True)
             if k not in {"order_no", "payment_provider"}]
    query.extend((("order_no", order_no), ("payment_provider", PROVIDER)))
    return urlunsplit(target._replace(query=urlencode(query)))


def _checkout_result(order, reused):
    return {**commerce._checkout_result(order, reused),
            "session_id": order.checkout_context.get("provider_checkout_id")}


def _remote_checkout(account, order):
    try:
        return creem.get_checkout(**creem_accounts.api_credentials(account),
                                  checkout_id=order.checkout_context["provider_checkout_id"])
    except creem.CreemError:
        raise BillingError(502, "Creem order status is temporarily unavailable") from None


def guard_other_recurring(db, user_id):
    """Keep a Creem checkout reservation from racing a different gateway."""
    from app.modules.billing import creem_subscriptions

    creem_subscriptions.assert_can_start(db, user_id)
    pending = db.scalars(select(Order).where(
        Order.user_id == user_id, Order.provider == PROVIDER, Order.billing_mode == "subscription",
        Order.status == "created", Order.checkout_key.is_not(None),
    ).order_by(Order.id).execution_options(populate_existing=True))
    for order in pending:
        if order.checkout_context.get("provider_checkout_id"):
            remote = _remote_checkout(_account_for_order(db, order), order)
            if remote.get("status") == "expired":
                order.status = "failed"
                order.checkout_context = {**order.checkout_context, "checkout_expired": True}
                db.flush()
                continue
            if remote.get("status") in {"completed", "processing"}:
                raise commerce.checkout_conflict(order, "checkout_payment_pending",
                    "The Creem payment is processing. Check your orders before starting another subscription.", "wait_for_payment")
        action = "resume_only" if order.checkout_url else "resume_or_cancel"
        raise commerce.checkout_conflict(order, "pending_subscription_checkout",
            "Resolve your existing Creem checkout before choosing another subscription.", action, True)


def _assert_stripe_reservation_released(db, user_id):
    pending = db.scalars(select(Order).where(
        Order.user_id == user_id, Order.provider == "stripe", Order.billing_mode == "subscription",
        Order.product_type == "membership",
    ).order_by(Order.id).execution_options(populate_existing=True))
    for order in pending:
        if commerce._checkout_pending(db, order) and commerce._checkout_remote_state(db, order) != "expired":
            raise commerce.checkout_conflict(order, "pending_subscription_checkout",
                "Finish or cancel your existing Stripe checkout before starting another subscription.", "resume_or_cancel", True)
    commerce.assert_no_renewing_subscription(db, user_id)


def checkout(db, *, user_id, email, product_type, code, idempotency_key, account_code,
             success_url, cancel_url, source_drama_id, attribution_pid):
    require_owned(db, User, user_id)
    key = commerce.checkout_key(user_id, idempotency_key)
    commerce.wallet_lock(db, user_id)
    order = db.scalar(select(Order).where(Order.checkout_key == key))
    reused = order is not None
    if order is not None:
        if (order.provider, order.product_type, order.product_snapshot.get("code"), order.account_code) != (
            PROVIDER, product_type, code, account_code,
        ):
            raise BillingError(409, "Idempotency key was used for another checkout")
    else:
        commerce.require_new_sale(db, product_type)
        gateway_accounts.lock_gateways(db, PROVIDER)
        account = gateway_accounts.bound_account(db, PROVIDER)
        if account is None or account.code != account_code or account.status != "active":
            raise BillingError(403, "Creem account is not enabled for this site")
        mode = account.config["mode"]
        if not gateway_accounts.accepting_orders(mode):
            raise BillingError(403, "Creem test checkout is disabled in this environment")
        snapshot = commerce.product_snapshot(db, product_type, code)
        assert_site_currency(db, snapshot["currency"])
        bill = snapshot["billing_mode"]
        if product_type == "membership":
            commerce._membership_refund_checkout_guard(db, user_id)
        if bill == "subscription":
            guard_other_recurring(db, user_id)
            _assert_stripe_reservation_released(db, user_id)
        commerce.validate_checkout_source(db, source_drama_id)
        binding = creem_accounts.product_binding(db, account.code, product_type, snapshot["version_id"], bill)
        if binding is None:
            raise BillingError(409, "This product version has no Creem mapping")
        interval = INTERVAL_OF.get(code, "month") if bill == "subscription" else None
        creem_accounts.verify_product(account, binding.external_product_id, snapshot["price_cents"],
                                      snapshot["currency"], bill, interval)
        success = commerce.return_url(success_url, "success", db)
        commerce.return_url(cancel_url, "cancel", db)
        number = secrets.token_hex(12)
        order = Order(
            order_no=number, user_id=user_id, provider=PROVIDER, account_code=account.code,
            product_type=product_type, product_snapshot=snapshot,
            plan_version_id=snapshot["version_id"] if product_type == "membership" else None,
            amount_cents=snapshot["price_cents"], currency=snapshot["currency"], billing_mode=bill, status="created",
            checkout_key=key, source_drama_id=source_drama_id, attribution_pid=attribution_pid,
            checkout_context={
                "gateway_mode": mode, "external_product_id": binding.external_product_id,
                "request": {"success_url": _return_to_order(success, number), "email": email},
            },
        )
        db.add(order)
        db.commit()
        commerce.wallet_lock(db, user_id)
        order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
    if order.status != "created":
        if order.status == "failed" and order.checkout_context.get("checkout_expired"):
            raise commerce.checkout_conflict(order, "checkout_expired", "This checkout expired. Start a new purchase.", "new_purchase")
        raise commerce.checkout_conflict(order, "checkout_already_paid", "Check the order before paying again.", "view_orders")
    account = _account_for_order(db, order)
    if order.checkout_url:
        if order.billing_mode == "subscription":
            remote = _remote_checkout(account, order)
            if remote.get("status") == "expired":
                order.status = "failed"
                order.checkout_context = {**order.checkout_context, "checkout_expired": True}
                db.commit()
                raise commerce.checkout_conflict(order, "checkout_expired", "This checkout expired. Start a new purchase.", "new_purchase")
            if remote.get("status") in {"completed", "processing"}:
                raise commerce.checkout_conflict(order, "checkout_payment_pending", "The Creem payment is processing.", "wait_for_payment")
        return _checkout_result(order, True)
    context = order.checkout_context
    request = context["request"]
    try:
        provider_checkout = creem.create_checkout(
            **creem_accounts.api_credentials(account), product_id=context["external_product_id"],
            order_no=order.order_no, success_url=request["success_url"], customer_email=request["email"],
        )
    except creem.CreemError:
        raise BillingError(502, "Creem checkout is temporarily unavailable") from None
    if len(provider_checkout["checkout_url"]) > 2000:
        raise BillingError(502, "Creem returned an invalid checkout address")
    order.checkout_url = provider_checkout["checkout_url"]
    order.checkout_context = {**context, "provider_checkout_id": provider_checkout["id"]}
    db.commit()
    return _checkout_result(order, reused)


def merchant_transaction_key(account, trade_no):
    return "creem:" + hashlib.sha256((account.identity + ":" + trade_no).encode()).hexdigest()


def _remote_id(value):
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, str) and 0 < len(value) <= 128 else None


def _metadata_order(obj):
    metadata = obj.get("metadata")
    return metadata.get("jjtv_order_no") if isinstance(metadata, dict) else None


def _merchant_order(db, account, *conditions):
    return db.scalar(select(Order).where(
        Order.provider == PROVIDER, Order.account_code == account.code, *conditions,
    ).order_by(Order.id).limit(1).execution_options(include_all_tenants=True))


def _checkout_order(db, account, obj):
    number = _metadata_order(obj)
    requested = obj.get("request_id")
    if number and requested and number != requested:
        raise BillingError(409, "Creem checkout references conflict")
    known = _merchant_order(db, account, Order.order_no == (number or requested)) if number or requested else None
    reference = _remote_id(obj)
    if known is None and reference:
        known = _merchant_order(db, account, Order.checkout_context["provider_checkout_id"].astext == reference)
    return known


def _validate_checkout(account, order, checkout):
    reference = _remote_id(checkout)
    context = order.checkout_context
    stored = context.get("provider_checkout_id")
    if (not reference or (stored and stored != reference)
            or not creem.mode_matches(context["gateway_mode"], checkout.get("mode"))
            or account.config["mode"] != context["gateway_mode"]
            or _remote_id(checkout.get("product", checkout.get("product_id"))) != context["external_product_id"]
            or (_metadata_order(checkout) not in (None, order.order_no))
            or (checkout.get("request_id") not in (None, order.order_no))
            or checkout.get("units", 1) != 1):
        raise BillingError(409, "Creem checkout does not match the order")
    return reference


def _validate_charge(account, order, checkout):
    charge = checkout.get("order")
    if checkout.get("status") != "completed" or not isinstance(charge, dict) or charge.get("status") != "paid":
        raise BillingError(409, "Creem checkout has no verified paid order")
    amount, gross, tax = charge.get("amount"), charge.get("amount_paid"), charge.get("tax_amount", 0)
    trade = _remote_id(charge.get("transaction"))
    if (not trade or not trade.startswith("tran_") or type(amount) is not int or amount != order.amount_cents
            or _remote_id(charge.get("product")) != order.checkout_context["external_product_id"]
            or not creem.mode_matches(account.config["mode"], charge.get("mode"))
            or charge.get("type") != ("recurring" if order.billing_mode == "subscription" else "onetime")
            or str(charge.get("currency", "")).upper() != order.currency
            or type(gross) is not int or type(tax) is not int or tax < 0 or gross <= 0
            or gross not in {amount, amount + tax} or charge.get("amount_due") != gross
            or charge.get("discount_amount", 0) not in (None, 0)):
        raise BillingError(409, "Creem charge does not match the frozen amount, currency and product")
    return trade, gross, tax


def _fulfill_checkout(db, account, order, checkout):
    reference = _validate_checkout(account, order, checkout)
    trade, gross, tax = _validate_charge(account, order, checkout)
    commerce.wallet_lock(db, order.user_id)
    order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
    _validate_checkout(account, order, checkout)
    context = order.checkout_context
    if order.billing_mode == "subscription":
        subscription = _remote_id(checkout.get("subscription"))
        provider_order = _remote_id(checkout["order"])
        if (not subscription or not subscription.startswith("sub_") or len(subscription) > 100
                or (order.subscription_ref and order.subscription_ref != subscription)):
            raise BillingError(409, "Creem checkout has an unexpected subscription")
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:reference))"),
                   {"reference": f"creem-subscription:{account.identity}:{subscription}"})
        other = _merchant_order(db, account, Order.subscription_ref == subscription,
                                Order.checkout_key.is_not(None), Order.id != order.id)
        if other is not None:
            raise BillingError(409, "Creem subscription is already linked to another order")
        original = context.get("initial_transaction_id")
        if (not provider_order or (original and original != trade)
                or context.get("provider_initial_order_id") not in (None, provider_order)):
            raise BillingError(409, "Creem checkout has an unexpected initial transaction")
        order.subscription_ref = subscription
        order.checkout_context = {**context, "provider_checkout_id": reference, "initial_transaction_id": trade,
                                  "provider_initial_order_id": provider_order}
        db.commit()
        return {"order_no": order.order_no, "pending": order.status == "created"}
    txn = merchant_transaction_key(account, trade)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:txn))"), {"txn": txn})
    other = _merchant_order(db, account, Order.external_txn_id == txn)
    if (other is not None and other.id != order.id) or (order.external_txn_id and order.external_txn_id != txn):
        raise BillingError(409, "Creem transaction already has a different order")
    if order.status != "created":
        if order.external_txn_id == txn and (db.get(CommerceSettlement, txn) is not None or order.status == "refund_review"):
            return {"order_no": order.order_no, "replayed": True}
        raise BillingError(409, "Creem order requires manual reconciliation")
    order.external_txn_id = txn
    order.checkout_context = {**context, "provider_checkout_id": reference, "provider_trade_no": trade,
                              "provider_amount_paid": gross, "provider_tax_minor": tax}
    db.flush()
    return commerce.settle_order(db, order, txn)


def _subscription_event(db, account, kind, obj):
    from app.modules.billing import creem_subscriptions

    sub = _remote_id(obj)
    number = _metadata_order(obj)
    initial = _merchant_order(
        db, account, Order.checkout_key.is_not(None), Order.billing_mode == "subscription",
        Order.subscription_ref == sub,
    ) if sub else None
    if initial and number and initial.order_no != number:
        raise BillingError(409, "Creem subscription references conflict")
    if initial is None and number:
        initial = _merchant_order(db, account, Order.order_no == number, Order.checkout_key.is_not(None))
    if initial is None:
        return {"ignored": kind}
    with site_context(db, initial.tenant_id):
        _account_for_order(db, initial)
        return creem_subscriptions.handle_event(db, account, initial, kind, obj)


def _refund_notice(db, account, kind, obj):
    transaction = obj.get("transaction")
    trade = _remote_id(transaction)
    if not trade or not isinstance(transaction, dict):
        return {"ignored": kind}
    if kind == "refund.created" and obj.get("status") != "succeeded":
        return {"ignored": kind}
    txn = merchant_transaction_key(account, trade)
    order = _merchant_order(db, account, Order.external_txn_id == txn)
    if order is None and isinstance(obj.get("checkout"), dict):
        order = _checkout_order(db, account, obj["checkout"])
    if order is None:
        return {"ignored": kind}
    with site_context(db, order.tenant_id):
        _account_for_order(db, order)
        commerce.wallet_lock(db, order.user_id)
        order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
        if (not creem.mode_matches(account.config["mode"], transaction.get("mode"))
                or str(transaction.get("currency", "")).upper() != order.currency
                or transaction.get("amount") != order.amount_cents
                or order.checkout_context.get("provider_amount_paid") not in (None, transaction.get("amount_paid"))):
            raise BillingError(409, "Creem refund or dispute does not match the stored payment")
        if order.external_txn_id not in (None, txn):
            raise BillingError(409, "Creem refund notification identifies another transaction")
        if order.external_txn_id is None:
            checkout = obj["checkout"]
            _validate_checkout(account, order, checkout)
            original_trade, gross, tax = _validate_charge(account, order, {**checkout, "order": obj.get("order")})
            if original_trade != trade:
                raise BillingError(409, "Creem refund has no proven original transaction")
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:txn))"), {"txn": txn})
            other = _merchant_order(db, account, Order.external_txn_id == txn, Order.id != order.id)
            if other is not None:
                raise BillingError(409, "Creem transaction already has a different order")
            order.external_txn_id = txn
            order.checkout_context = {**order.checkout_context, "provider_trade_no": trade,
                                      "provider_amount_paid": gross, "provider_tax_minor": tax}
        previous = db.scalar(select(CommerceRefund).where(CommerceRefund.order_id == order.id))
        if previous is not None and previous.status == "pending":
            if (kind == "refund.created" and _confirmed_full_refund(order, transaction)
                    and obj.get("refund_amount") == order.checkout_context.get("provider_amount_paid")
                    and str(obj.get("refund_currency", "")).upper() == order.currency):
                previous.refund_id = _remote_id(obj)
                commerce.finalize_refund(db, previous, "succeeded")
                db.commit()
                return {"refund_status": previous.status, "order_no": order.order_no}
            previous.error = "Creem refund or dispute requires review; original sources remain frozen"
        elif previous is None and order.status in {"created", "paid"}:
            order.status = "refund_review"
            db.add(CommerceRefund(order_id=order.id, idempotency_key=f"creem-review:{order.order_no}",
                                  refund_id=_remote_id(obj), status="manual_review", reason="Provider refund or chargeback",
                                  error="Verify the Creem refund, dispute and consumption before changing entitlements"))
        db.commit()
    return {"manual_review": True, "order_no": order.order_no}


def receive(db, code, body, signature):
    account = db.get(GatewayAccount, (PROVIDER, code))
    if account is None:
        raise BillingError(404, "Creem account not found")
    try:
        event = creem.verify_webhook(body, signature, creem_accounts.credential(account, "webhook_secret"))
    except creem.CreemError:
        raise BillingError(401, "Invalid Creem payment notification") from None
    kind, obj = event["eventType"], event["object"]
    mode = account.config["mode"]
    if not creem.mode_matches(mode, obj.get("mode")) or not gateway_accounts.accepting_orders(mode):
        raise BillingError(409, "Creem event environment does not match the account")
    if kind == "checkout.completed":
        order = _checkout_order(db, account, obj)
        if order is not None:
            with site_context(db, order.tenant_id):
                _account_for_order(db, order)
                return _fulfill_checkout(db, account, order, obj)
    elif kind.startswith("subscription."):
        return _subscription_event(db, account, kind, obj)
    elif kind in {"refund.created", "dispute.created"}:
        return _refund_notice(db, account, kind, obj)
    return {"ignored": kind}


def reconcile(db, order):
    if order.provider != PROVIDER or order.status != "created":
        return
    order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
    context = order.checkout_context
    now = commerce.now()
    if (order.status != "created" or not context.get("provider_checkout_id")
            or context.get("creem_query_after", 0) > now.timestamp()):
        return
    account = _account_for_order(db, order)
    order.checkout_context = {**context, "creem_query_after": (now + timedelta(seconds=30)).timestamp()}
    db.commit()
    checkout = _remote_checkout(account, order)
    if checkout.get("status") != "completed":
        return
    _fulfill_checkout(db, account, order, checkout)
    if order.billing_mode == "subscription":
        from app.modules.billing import creem_subscriptions

        try:
            remote = creem.get_subscription(**creem_accounts.api_credentials(account), subscription_id=order.subscription_ref)
        except creem.CreemError:
            raise BillingError(502, "Creem subscription status is temporarily unavailable") from None
        if isinstance(remote.get("last_transaction"), dict) or remote.get("last_transaction_id"):
            creem_subscriptions.handle_event(db, account, order, "subscription.paid", remote)


def _confirmed_full_refund(order, transaction):
    charged = order.checkout_context.get("provider_amount_paid")
    return (type(charged) is int and charged > 0 and transaction.get("status") == "refunded"
            and transaction.get("refunded_amount") == charged)


def _refund_transaction(account, order):
    trade = order.checkout_context["provider_trade_no"]
    try:
        transaction = creem.get_transaction(**creem_accounts.api_credentials(account), transaction_id=trade)
    except creem.CreemError:
        raise BillingError(502, "Creem transaction lookup is unavailable") from None
    if (transaction.get("id") != trade or transaction.get("amount") != order.amount_cents
            or transaction.get("amount_paid") != order.checkout_context.get("provider_amount_paid")
            or str(transaction.get("currency", "")).upper() != order.currency
            or not creem.mode_matches(account.config["mode"], transaction.get("mode"))):
        raise BillingError(409, "Creem transaction does not match the stored payment")
    return transaction


def request_refund(db, order, wallet, refund, actor, reason):
    merchant = _account_for_order(db, order)
    if refund is None:
        if order.status != "paid" or db.get(CommerceSettlement, order.external_txn_id) is None:
            raise BillingError(409, "Creem order has not been settled")
        if type(order.checkout_context.get("provider_amount_paid")) is not int or order.checkout_context["provider_amount_paid"] <= 0:
            raise BillingError(409, "Creem order lacks a verified charge amount")
        before = _refund_transaction(merchant, order)
        if before.get("status") != "paid" or before.get("refunded_amount") not in (0, None):
            raise BillingError(409, "Creem transaction has a prior refund or dispute; review manually")
        refund = commerce.freeze_refund_sources(db, order, wallet, actor, reason)
        try:
            result = creem.refund_transaction(
                **creem_accounts.api_credentials(merchant), transaction_id=order.checkout_context["provider_trade_no"],
            )
        except creem.CreemError:
            return commerce.uncertain_refund(db, order, refund, "Creem refund outcome is unknown; retry will only query the transaction")
        if result in {"failed", "canceled"}:
            order, refund = commerce.locked_refund(db, order, refund)
            commerce.finalize_refund(db, refund, result)
            db.commit()
            return commerce.refund_out(refund)
    else:
        db.commit()
    # The authenticated Creem API identifies the exact original transaction.
    # A repeated admin action re-reads it instead of creating another refund.
    try:
        transaction = _refund_transaction(merchant, order)
    except BillingError:
        return commerce.uncertain_refund(db, order, refund, "Creem transaction lookup failed; original sources remain frozen")
    if not _confirmed_full_refund(order, transaction):
        return commerce.uncertain_refund(db, order, refund, "Creem has not verified a full refund; original sources remain frozen")
    order, refund = commerce.locked_refund(db, order, refund)
    commerce.finalize_refund(db, refund, "succeeded")
    db.commit()
    return commerce.refund_out(refund)
