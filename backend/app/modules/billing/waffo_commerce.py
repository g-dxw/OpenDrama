"""Waffo acquiring orders, strict payment evidence and source-safe refunds."""

import hashlib
import secrets
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select, text

from app.modules.account.models import User
from app.modules.billing import commerce, creem_commerce, gateway_accounts, gateway_money, pancake_commerce, waffo, waffo_accounts
from app.modules.billing.commerce_models import CommerceRefund, CommerceSettlement, GatewayAccount
from app.modules.billing.models import Order
from app.modules.billing.service import INTERVAL_OF, BillingError
from app.modules.billing.site_merchants import assert_site_currency, site_currency
from app.modules.common.models import AuditLog
from app.modules.tenancy.consumer_scope import require_owned, site_context

PROVIDER = waffo_accounts.PROVIDER


def channels(db):
    account = gateway_accounts.bound_account(db, PROVIDER)
    if (account is None or account.status != "active" or not gateway_accounts.accepting_orders(account.config["mode"])
            or account.config["currency"] != site_currency(db)):
        return []
    membership = commerce.sale_allowed(db, "membership")
    modes = ["one_time"] if membership or commerce.sale_allowed(db, "coin_pack") else []
    if membership and account.config["subscriptions_enabled"]:
        modes.append("subscription")
    return [{"provider": PROVIDER, "code": account.code, "name": account.name, "enabled": True,
             "billing_modes": modes}] if modes else []


def account_for_order(db, order):
    account = db.get(GatewayAccount, (PROVIDER, order.account_code))
    if account is None or account.config["mode"] != order.checkout_context.get("gateway_mode") or account.config["currency"] != order.currency:
        raise BillingError(503, "Original Waffo merchant is unavailable")
    if not gateway_accounts.accepting_orders(account.config["mode"]):
        raise BillingError(409, "Waffo test orders are disabled in this environment")
    return account


def _return_to_order(url, number, *, success):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in {"order_no", "payment_provider", "checkout"}]
    query.extend((("checkout", "success" if success else "cancel"), ("order_no", number), ("payment_provider", PROVIDER)))
    return urlunsplit(parts._replace(query=urlencode(query)))


def _result(order, reused):
    return {**commerce._checkout_result(order, reused),
            "session_id": order.subscription_ref or order.checkout_context.get("provider_order_id")}


def guard_other_recurring(db, user_id):
    from app.modules.billing import waffo_subscriptions

    waffo_subscriptions.assert_can_start(db, user_id)
    pending = db.scalars(select(Order).where(
        Order.user_id == user_id, Order.provider == PROVIDER, Order.billing_mode == "subscription",
        Order.status == "created", Order.checkout_key.is_not(None),
    ).order_by(Order.id).execution_options(populate_existing=True))
    for order in pending:
        if order.checkout_url:
            remote = waffo_subscriptions.lookup(account_for_order(db, order), order)
            if remote and remote.get("subscriptionStatus") == "CLOSE" and not any(
                payment.get("orderStatus") == "PAY_SUCCESS" for payment in remote.get("paymentDetails") or ()
            ):
                order.status = "failed"
                order.checkout_context = {**order.checkout_context, "checkout_expired": True}
                db.flush()
                continue
            if remote and remote.get("subscriptionStatus") == "ACTIVE":
                raise commerce.checkout_conflict(order, "checkout_payment_pending", "The Waffo payment is processing.", "wait_for_payment")
        action = "resume_only" if order.checkout_url else "resume_or_cancel"
        raise commerce.checkout_conflict(order, "pending_subscription_checkout",
            "Resolve your existing Waffo checkout before choosing another subscription.", action, True)


def checkout(db, *, user_id, email, product_type, code, idempotency_key, account_code,
             success_url, cancel_url, source_drama_id, attribution_pid):
    user = require_owned(db, User, user_id)
    key = commerce.checkout_key(user_id, idempotency_key)
    commerce.wallet_lock(db, user_id)
    order = db.scalar(select(Order).where(Order.checkout_key == key))
    reused = order is not None
    if order is not None:
        if (order.provider, order.account_code, order.product_type, order.product_snapshot.get("code")) != (
            PROVIDER, account_code, product_type, code,
        ):
            raise BillingError(409, "Idempotency key was used for another checkout")
    else:
        commerce.require_new_sale(db, product_type)
        gateway_accounts.lock_gateways(db, PROVIDER)
        account = gateway_accounts.bound_account(db, PROVIDER)
        if account is None or account.code != account_code or account.status != "active":
            raise BillingError(403, "Waffo account is not enabled for this site")
        config = account.config
        if not gateway_accounts.accepting_orders(config["mode"]):
            raise BillingError(403, "Waffo test checkout is disabled in this environment")
        snapshot = commerce.product_snapshot(db, product_type, code)
        if assert_site_currency(db, snapshot["currency"]) != config["currency"]:
            raise BillingError(409, "Waffo merchant currency does not match the product")
        billing = snapshot["billing_mode"]
        if product_type == "membership":
            commerce._membership_refund_checkout_guard(db, user_id)
        if billing == "subscription":
            if not config["subscriptions_enabled"]:
                raise BillingError(409, "This Waffo account is only enabled for one-time payment")
            guard_other_recurring(db, user_id)
            pancake_commerce.guard_other_recurring(db, user_id)
            creem_commerce.guard_other_recurring(db, user_id)
            creem_commerce._assert_stripe_reservation_released(db, user_id)
        commerce.validate_checkout_source(db, source_drama_id)
        number = secrets.token_hex(12)
        request = {
            "email": email, "customer_id": getattr(user, "public_id", None) or str(user_id),
            "success_url": _return_to_order(commerce.return_url(success_url, "success", db), number, success=True),
            "cancel_url": _return_to_order(commerce.return_url(cancel_url, "cancel", db), number, success=False),
            "notify_url": gateway_accounts.notify_url(PROVIDER, account.code),
            "requested_at": commerce.now().isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        order = Order(
            order_no=number, user_id=user_id, provider=PROVIDER, account_code=account.code,
            product_type=product_type, product_snapshot=snapshot, billing_mode=billing, status="created",
            plan_version_id=snapshot["version_id"] if product_type == "membership" else None,
            amount_cents=snapshot["price_cents"], currency=snapshot["currency"], checkout_key=key,
            source_drama_id=source_drama_id, attribution_pid=attribution_pid,
            checkout_context={"gateway_mode": config["mode"], "request": request,
                              "subscription_interval": INTERVAL_OF.get(code, "month") if billing == "subscription" else None},
        )
        db.add(order)
        db.commit()
        commerce.wallet_lock(db, user_id)
        order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
    if order.status != "created":
        if order.status == "failed" and order.checkout_context.get("checkout_expired"):
            raise commerce.checkout_conflict(order, "checkout_expired", "This checkout expired. Start a new purchase.", "new_purchase")
        raise commerce.checkout_conflict(order, "checkout_already_paid", "Check your order before paying again.", "view_orders")
    if order.checkout_url:
        return _result(order, True)
    account = account_for_order(db, order)
    context = order.checkout_context
    request = context["request"]
    fields = {
        **waffo_accounts.credentials(account),
        "order_no": order.order_no, "amount": gateway_money.as_major(order.amount_cents, order.currency),
        "currency": order.currency, "requested_at": request["requested_at"], "customer_id": request["customer_id"],
        "customer_email": request["email"], "product_name": order.product_snapshot["name"],
        "notify_url": request["notify_url"], "success_url": request["success_url"], "cancel_url": request["cancel_url"],
    }
    try:
        if order.billing_mode == "subscription":
            management = urlsplit(request["success_url"])
            query = [(k, v) for k, v in parse_qsl(management.query, keep_blank_values=True)
                     if k not in {"order_no", "payment_provider", "checkout"}]
            result = waffo.create_subscription(
                **fields, interval=context["subscription_interval"],
                management_url=urlunsplit(management._replace(query=urlencode(query), fragment="account/orders")),
            )
            order.subscription_ref = result["subscriptionId"]
        else:
            result = waffo.create_checkout(**fields)
            order.checkout_context = {**context, "provider_order_id": result["acquiringOrderId"]}
    except waffo.WaffoError:
        raise BillingError(502, "Waffo checkout is temporarily unavailable") from None
    url = result["checkout_url"]
    if len(url) > 2000:
        raise BillingError(502, "Waffo returned an invalid checkout address")
    order.checkout_url = url
    db.commit()
    return _result(order, reused)


def acquisition_key(account, acquiring_id):
    return "waffo:" + hashlib.sha256((account.identity + ":" + acquiring_id).encode()).hexdigest()


def amount_minor(value, currency, *, allow_zero=False):
    try:
        return gateway_money.as_minor(value, currency, allow_zero=allow_zero)
    except ValueError:
        raise BillingError(409, "Waffo returned an invalid monetary amount") from None


def _merchant_value(obj):
    info = obj.get("merchantInfo")
    merchant = info.get("merchantId") if isinstance(info, dict) else None
    return str(merchant) if type(merchant) in (str, int) else None


def _id(value):
    return value if isinstance(value, str) and 0 < len(value) <= 100 else None


def _find_order(db, account, *filters):
    return db.scalar(select(Order).where(Order.provider == PROVIDER, Order.account_code == account.code, *filters)
                     .order_by(Order.id).limit(1).execution_options(include_all_tenants=True))


def _remote_order(account, order):
    try:
        return waffo.get_order(**waffo_accounts.credentials(account), order_no=order.order_no)
    except waffo.WaffoError:
        raise BillingError(502, "Waffo order lookup is temporarily unavailable") from None


def _validate_order(account, order, result):
    acquisition = _id(result.get("acquiringOrderId"))
    if (not acquisition or order.checkout_context.get("provider_order_id") not in (None, acquisition)
            or result.get("merchantOrderId") != order.order_no
            or result.get("paymentRequestId") != waffo.request_reference(order.order_no)
            or _merchant_value(result) != account.config["merchant_id"] or result.get("orderCurrency") != order.currency
            or amount_minor(result.get("orderAmount"), order.currency) != order.amount_cents):
        raise BillingError(409, "Waffo result does not match the frozen order, merchant and amount")
    return acquisition


def _apply_one_time(db, account, order, result):
    acquisition = _validate_order(account, order, result)
    observed = result.get("orderStatus")
    if observed not in {"PAY_SUCCESS", "ORDER_CLOSE"}:
        return
    commerce.wallet_lock(db, order.user_id)
    order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
    _validate_order(account, order, result)
    if observed == "ORDER_CLOSE":
        if order.status == "created":
            order.status = "failed"
            order.checkout_context = {**order.checkout_context, "provider_order_id": acquisition, "checkout_expired": True}
            db.commit()
        return
    txn = acquisition_key(account, acquisition)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:txn))"), {"txn": txn})
    other = _find_order(db, account, Order.id != order.id, Order.external_txn_id == txn)
    if other is not None or order.external_txn_id not in (None, txn):
        raise BillingError(409, "Waffo acquisition is linked to another order")
    if order.status != "created":
        if order.external_txn_id == txn and (db.get(CommerceSettlement, txn) is not None or order.status == "refund_review"):
            return
        raise BillingError(409, "Waffo order requires manual reconciliation")
    tax = amount_minor(result.get("orderTaxAmount") or "0", order.currency, allow_zero=True)
    order.external_txn_id = txn
    order.checkout_context = {**order.checkout_context, "provider_order_id": acquisition, "provider_trade_no": acquisition,
                              "provider_amount_paid": order.amount_cents + tax, "provider_tax_minor": tax}
    db.flush()
    commerce.settle_order(db, order, txn)


def receive(db, code, body, signature):
    account = db.get(GatewayAccount, (PROVIDER, code))
    if account is None:
        raise BillingError(404, "Waffo account not found")
    creds = waffo_accounts.credentials(account)
    try:
        event = waffo.verify_webhook(body, signature, creds["public_key"])
    except waffo.WaffoError:
        raise BillingError(401, "Invalid Waffo payment notification") from None
    kind, result = event["eventType"], event["result"]
    if kind in {"REFUND_NOTIFICATION", "CHARGEBACK_NOTIFICATION"}:
        _refund_notice(db, account, kind, result)
    elif kind == "PAYMENT_NOTIFICATION" and not result.get("subscriptionInfo"):
        number = _id(result.get("merchantOrderId"))
        order = _find_order(db, account, Order.order_no == number, Order.billing_mode == "one_time") if number else None
        if order:
            with site_context(db, order.tenant_id):
                account_for_order(db, order)
                _apply_one_time(db, account, order, result)
    elif kind in {"SUBSCRIPTION_STATUS_NOTIFICATION", "SUBSCRIPTION_PERIOD_CHANGED_NOTIFICATION"}:
        from app.modules.billing import waffo_subscriptions

        number, subscription = _id(result.get("merchantSubscriptionId")), _id(result.get("subscriptionId"))
        order = _find_order(db, account, Order.order_no == number, Order.checkout_key.is_not(None)) if number else None
        if order is None and subscription:
            order = _find_order(db, account, Order.subscription_ref == subscription, Order.checkout_key.is_not(None))
        if order:
            with site_context(db, order.tenant_id):
                account_for_order(db, order)
                waffo_subscriptions.handle_event(db, account, order, kind, result)
    db.commit()
    return waffo.acknowledgment(creds["private_key"])


def reconcile(db, order):
    if order.provider != PROVIDER or order.status != "created":
        return
    order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
    now = commerce.now()
    if order.status != "created" or order.checkout_context.get("waffo_query_after", 0) > now.timestamp():
        return
    account = account_for_order(db, order)
    order.checkout_context = {**order.checkout_context, "waffo_query_after": (now + timedelta(seconds=30)).timestamp()}
    db.commit()
    if order.billing_mode == "subscription":
        from app.modules.billing import waffo_subscriptions
        return waffo_subscriptions.reconcile(db, account, order)
    remote = _remote_order(account, order)
    if remote is not None:
        _apply_one_time(db, account, order, remote)


def _refund_no(order):
    return "jjtv-refund-" + order.order_no


def _refund_result(order, result):
    context = order.checkout_context
    if (result.get("merchantRefundOrderId") != _refund_no(order)
            or result.get("refundRequestId") != waffo.request_reference(_refund_no(order))
            or result.get("acquiringOrderId") != context.get("provider_order_id")
            or result.get("origPaymentRequestId") not in (None, waffo.request_reference(order.order_no))
            or result.get("refundCurrency") not in (None, order.currency)
            or amount_minor(result.get("refundAmount"), order.currency) != order.amount_cents):
        raise BillingError(409, "Waffo refund does not match its original order and requested amount")
    return result.get("refundStatus"), _id(result.get("acquiringRefundOrderId"))


def _refund_notice(db, account, kind, result):
    chargeback = kind == "CHARGEBACK_NOTIFICATION"
    acquiring = _id(result.get("originalOrderId" if chargeback else "acquiringOrderId"))
    if acquiring is None:
        return
    if chargeback and str(result.get("merchantId")) != account.config["merchant_id"]:
        raise BillingError(409, "Waffo chargeback merchant does not match the account")
    txn = acquisition_key(account, acquiring)
    order = _find_order(db, account, Order.external_txn_id == txn)
    if order is None:
        order = _find_order(db, account, Order.checkout_context["provider_order_id"].astext == acquiring,
                            Order.billing_mode == "one_time")
    terminal = chargeback or result.get("refundStatus") in {"ORDER_FULLY_REFUNDED", "ORDER_PARTIALLY_REFUNDED"}
    remote = None
    if order is None and terminal:
        try:
            remote = waffo.get_order(**waffo_accounts.credentials(account), provider_order_id=acquiring)
        except waffo.WaffoError:
            raise BillingError(502, "Waffo original payment could not be verified") from None
        number = _id(remote.get("merchantOrderId")) if remote else None
        if number:
            order = _find_order(db, account, Order.order_no == number, Order.billing_mode == "one_time")
    if order is None:
        return
    with site_context(db, order.tenant_id):
        account_for_order(db, order)
        commerce.wallet_lock(db, order.user_id)
        order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
        payment_request_field = "originalPaymentRequestId" if chargeback else "origPaymentRequestId"
        if order.billing_mode == "one_time" and (
            result.get("merchantOrderId") not in (None, order.order_no)
            or result.get(payment_request_field) not in (None, waffo.request_reference(order.order_no))
        ):
            raise BillingError(409, "Waffo refund or chargeback identifies another order")
        refund = db.scalar(select(CommerceRefund).where(CommerceRefund.order_id == order.id))
        if refund is not None and refund.status == "pending":
            if kind == "REFUND_NOTIFICATION":
                try:
                    status, identifier = _refund_result(order, result)
                except BillingError:
                    if not terminal:
                        return
                    status, identifier = None, None
                _finalize_refund(db, refund, status, identifier)
                if terminal and refund.status == "pending":
                    _mark_refund_review(db, order, refund, kind)
            else:
                _mark_refund_review(db, order, refund, kind)
        elif refund is not None and refund.status in {"failed", "canceled"} and terminal:
            _mark_refund_review(db, order, refund, kind)
        elif refund is None and terminal and order.status in {"created", "paid"}:
            if order.external_txn_id is None:
                if order.checkout_context.get("provider_order_id") != acquiring and (
                    not remote or _validate_order(account, order, remote) != acquiring
                ):
                    raise BillingError(409, "Waffo reversal has no verified original payment")
                db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:txn))"), {"txn": txn})
                other = _find_order(db, account, Order.id != order.id, Order.external_txn_id == txn)
                if other is not None:
                    raise BillingError(409, "Waffo acquisition is linked to another order")
                order.external_txn_id = txn
                order.checkout_context = {**order.checkout_context, "provider_order_id": acquiring, "provider_trade_no": acquiring,
                                          "provider_amount_paid": order.amount_cents}
            order.status = "refund_review"
            db.add(CommerceRefund(order_id=order.id, idempotency_key=f"waffo-review:{order.order_no}",
                                  status="manual_review", reason="Provider refund or chargeback",
                                  error="Verify the Waffo refund, dispute and consumption before changing entitlements"))
        db.commit()


def _mark_refund_review(db, order, refund, kind):
    db.add(AuditLog(actor_type="system", action="waffo.reversal.review", target_type="order", target_id=order.id,
                    payload={"event": kind, "previous_status": refund.status}))
    refund.status = "manual_review"
    refund.error = "Waffo reported a partial refund, chargeback or conflicting payment; reconcile before modifying entitlements"
    order.status = "refund_review"


def _finalize_refund(db, refund, status, identifier):
    state = {"ORDER_FULLY_REFUNDED": "succeeded", "ORDER_REFUND_FAILED": "failed"}.get(status)
    if state is not None and identifier:
        if refund.refund_id not in (None, identifier):
            raise BillingError(409, "Waffo refund identity changed")
        refund.refund_id = identifier
        commerce.finalize_refund(db, refund, state)


def request_refund(db, order, wallet, refund, actor, reason):
    account = account_for_order(db, order)
    creds = waffo_accounts.credentials(account)
    provider_id = order.checkout_context["provider_order_id"]
    reference = _refund_no(order)
    if refund is None:
        if order.status != "paid" or db.get(CommerceSettlement, order.external_txn_id) is None:
            raise BillingError(409, "Waffo order has not been settled")
        remote = _remote_order(account, order)
        if not remote or _validate_order(account, order, remote) != provider_id or remote.get("orderStatus") != "PAY_SUCCESS":
            raise BillingError(409, "Waffo original charge is unavailable or already refunded")
        refund = commerce.freeze_refund_sources(db, order, wallet, actor, reason)
        try:
            waffo.create_refund(**creds, order_no=order.order_no, provider_order_id=provider_id, refund_no=reference,
                                amount=gateway_money.as_major(order.amount_cents, order.currency), reason=reason[:256],
                                notify_url=gateway_accounts.notify_url(PROVIDER, account.code),
                                requested_at=commerce.aware(refund.created_at).isoformat(timespec="seconds").replace("+00:00", "Z"))
        except waffo.WaffoError:
            return commerce.uncertain_refund(db, order, refund, "Waffo refund outcome is unknown; retry will only query")
    else:
        db.commit()
    try:
        remote = waffo.get_refund(**creds, order_no=order.order_no, provider_order_id=provider_id,
                                  refund_no=reference, provider_refund_id=refund.refund_id)
        if remote is None:
            return commerce.uncertain_refund(db, order, refund, "Waffo has not confirmed the refund; sources remain frozen")
        status, identifier = _refund_result(order, remote)
    except (waffo.WaffoError, BillingError):
        return commerce.uncertain_refund(db, order, refund, "Waffo refund evidence is not available; sources remain frozen")
    order, refund = commerce.locked_refund(db, order, refund)
    if refund.status == "pending":
        if status == "ORDER_PARTIALLY_REFUNDED":
            _mark_refund_review(db, order, refund, "REFUND_INQUIRY")
        else:
            _finalize_refund(db, refund, status, identifier)
    db.commit()
    return commerce.refund_out(refund)
