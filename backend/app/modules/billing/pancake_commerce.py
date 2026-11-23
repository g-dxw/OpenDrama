"""Waffo Pancake checkout and confirmed payments against stored JJTV orders."""

import hashlib
import secrets
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select, text

from app.modules.account.models import User
from app.modules.billing import commerce, creem_commerce, gateway_accounts, gateway_money, gateway_products, pancake_accounts, waffo_pancake as pancake
from app.modules.billing.commerce_models import CommerceRefund, CommerceSettlement, GatewayAccount
from app.modules.billing.models import Order
from app.modules.billing.service import INTERVAL_OF, BillingError, billing_mode
from app.modules.billing.site_merchants import assert_site_currency
from app.modules.tenancy.consumer_scope import require_owned, site_context

PROVIDER = pancake_accounts.PROVIDER


def channels(db):
    account = gateway_accounts.bound_account(db, PROVIDER)
    if account is None or account.status != "active" or not gateway_accounts.accepting_orders(account.config["mode"]):
        return []
    mode = billing_mode(db)
    enabled = {kind: commerce.sale_allowed(db, kind) for kind in ("coin_pack", "membership")}
    products = [
        {field: row[field] for field in ("product_type", "product_code", "billing_mode")}
        for row in gateway_products.catalog(db, PROVIDER, account)
        if row["external_product_id"] and enabled[row["product_type"]]
        and (row["product_type"] == "coin_pack" or row["billing_mode"] == mode)
    ]
    if not products:
        return []
    return [{"provider": PROVIDER, "code": account.code, "name": account.name, "enabled": True,
             "billing_modes": sorted({x["billing_mode"] for x in products}), "products": products}]


def account_for_order(db, order):
    account = db.get(GatewayAccount, (PROVIDER, order.account_code))
    context = order.checkout_context
    if account is None or (account.config["mode"], account.config["store_id"]) != (
        context.get("gateway_mode"), context.get("store_id"),
    ):
        raise BillingError(503, "Original Waffo Pancake merchant is unavailable")
    if not gateway_accounts.accepting_orders(account.config["mode"]):
        raise BillingError(409, "Waffo Pancake test orders are disabled in this environment")
    return account


def _return_to_order(url, number):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in {"order_no", "payment_provider"}]
    query.extend((("order_no", number), ("payment_provider", PROVIDER)))
    return urlunsplit(parts._replace(query=urlencode(query)))


def _result(order, reused):
    return {**commerce._checkout_result(order, reused), "session_id": order.checkout_context.get("provider_checkout_id")}


def guard_other_recurring(db, user_id):
    from app.modules.billing import pancake_subscriptions

    pancake_subscriptions.assert_can_start(db, user_id)
    pending = db.scalars(select(Order).where(
        Order.user_id == user_id, Order.provider == PROVIDER, Order.billing_mode == "subscription",
        Order.status == "created", Order.checkout_key.is_not(None),
    ).order_by(Order.id).execution_options(populate_existing=True))
    for order in pending:
        if order.checkout_url:
            remote = remote_order(db, account_for_order(db, order), order)
            if remote and remote.get("status") == "canceled" and not remote.get("payments"):
                order.status = "failed"
                order.checkout_context = {**order.checkout_context, "checkout_expired": True}
                db.flush()
                continue
            if remote and remote.get("status") in {"active", "canceling", "past_due"}:
                raise commerce.checkout_conflict(order, "checkout_payment_pending",
                    "The Waffo Pancake payment is processing.", "wait_for_payment")
        raise commerce.checkout_conflict(order, "pending_subscription_checkout",
            "Resolve your existing Waffo Pancake checkout before choosing another subscription.",
            "resume_only" if order.checkout_url else "resume_or_cancel", True)


def checkout(db, *, user_id, email, product_type, code, idempotency_key, account_code,
             success_url, cancel_url, source_drama_id, attribution_pid):
    require_owned(db, User, user_id)
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
            raise BillingError(403, "Waffo Pancake account is not enabled for this site")
        config = account.config
        if not gateway_accounts.accepting_orders(config["mode"]):
            raise BillingError(403, "Waffo Pancake test checkout is disabled in this environment")
        snapshot = commerce.product_snapshot(db, product_type, code)
        assert_site_currency(db, snapshot["currency"])
        bill = snapshot["billing_mode"]
        if product_type == "membership":
            commerce._membership_refund_checkout_guard(db, user_id)
        if bill == "subscription":
            from app.modules.billing import waffo_commerce
            guard_other_recurring(db, user_id)
            creem_commerce.guard_other_recurring(db, user_id)
            waffo_commerce.guard_other_recurring(db, user_id)
            creem_commerce._assert_stripe_reservation_released(db, user_id)
        commerce.validate_checkout_source(db, source_drama_id)
        binding = gateway_products.binding(db, PROVIDER, account.code, product_type, snapshot["version_id"], bill)
        if binding is None:
            raise BillingError(409, "This product version has no Waffo Pancake mapping")
        price = pancake_accounts.verify_product(
            account, binding.external_product_id, snapshot["price_cents"], snapshot["currency"], bill,
            INTERVAL_OF.get(code, "month"),
        )
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
                "gateway_mode": config["mode"], "store_id": config["store_id"],
                "external_product_id": binding.external_product_id, "tax_category": price["tax_category"],
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
    account = account_for_order(db, order)
    if order.checkout_url:
        return _result(order, True)
    context = order.checkout_context
    try:
        result = pancake.create_checkout(
            **pancake_accounts.credentials(account), store_id=context["store_id"], product_id=context["external_product_id"],
            order_no=order.order_no, amount=gateway_money.as_major(order.amount_cents, order.currency), currency=order.currency,
            tax_category=context["tax_category"], success_url=context["request"]["success_url"],
            customer_email=context["request"]["email"],
        )
    except pancake.PancakeError:
        raise BillingError(502, "Waffo Pancake checkout is temporarily unavailable") from None
    if len(result["checkout_url"]) > 2000:
        raise BillingError(502, "Waffo Pancake returned an invalid checkout address")
    order.checkout_url = result["checkout_url"]
    order.checkout_context = {**context, "provider_checkout_id": result["session_id"], "provider_checkout_expires_at": result.get("expires_at")}
    db.commit()
    return _result(order, reused)


def payment_key(account, payment_id):
    return "pancake:" + hashlib.sha256((account.identity + ":" + payment_id).encode()).hexdigest()


def _id(obj):
    if isinstance(obj, dict):
        obj = obj.get("id")
    return obj if isinstance(obj, str) and 0 < len(obj) <= 100 else None


def _find_order(db, account, *conditions):
    return db.scalar(select(Order).where(Order.provider == PROVIDER, Order.account_code == account.code, *conditions)
                     .order_by(Order.id).limit(1).execution_options(include_all_tenants=True))


def remote_order(db, account, order):
    context = order.checkout_context
    try:
        remote = pancake.get_order(**pancake_accounts.credentials(account), store_id=context["store_id"],
                                   order_no=order.order_no, billing_mode=order.billing_mode)
    except pancake.PancakeError:
        raise BillingError(502, "Waffo Pancake order lookup is temporarily unavailable") from None
    if remote is None:
        return None
    product = "subscriptionProduct" if order.billing_mode == "subscription" else "onetimeProduct"
    reference = _id(remote)
    if (not reference or not reference.startswith("ORD_") or remote.get("orderMerchantExternalId") != order.order_no
            or remote.get("storeId", context["store_id"]) != context["store_id"]
            or _id(remote.get("store", context["store_id"])) != context["store_id"]
            or ("testMode" in remote and remote["testMode"] != (context["gateway_mode"] == "test"))
            or _id(remote.get(product)) != context["external_product_id"]
            or context.get("provider_order_id") not in (None, reference)):
        raise BillingError(409, "Waffo Pancake order does not match the frozen checkout")
    return remote


def verified_payment(account, order, remote, payment_id, *, permit_refunded=False):
    try:
        payment = pancake.get_payment(
            **pancake_accounts.credentials(account), store_id=order.checkout_context["store_id"],
            order_no=order.order_no, payment_id=payment_id,
        )
    except pancake.PancakeError:
        raise BillingError(502, "Waffo Pancake payment lookup is temporarily unavailable") from None
    if not payment or _id(payment) != payment_id or payment.get("orderId") != _id(remote):
        raise BillingError(409, "Waffo Pancake payment does not belong to the order")
    snapshot = payment.get("snapshotAmountDetails") or {}
    actual = payment.get("amount") or {}
    if (payment.get("status") != "succeeded" or snapshot.get("currency") != order.currency
            or actual.get("currency") != order.currency
            or pancake_accounts.amount_minor(snapshot.get("total"), order.currency) != order.amount_cents
            or pancake_accounts.amount_minor(actual.get("display"), order.currency) != order.amount_cents
            or pancake_accounts.amount_minor(snapshot.get("discountAmount", "0"), order.currency, allow_zero=True) != 0):
        raise BillingError(409, "Waffo Pancake payment does not match the frozen amount and currency")
    has_refund = payment.get("refundStatus") in {"refunded", "partially_refunded"} or any(
        refund.get("status") == "succeeded" for refund in payment.get("refunds") or ()
    )
    if not permit_refunded and has_refund:
        raise BillingError(409, "Waffo Pancake payment has a refund that requires review")
    return payment


def _credit_once(db, account, order, remote, payment_id):
    commerce.wallet_lock(db, order.user_id)
    order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
    txn = payment_key(account, payment_id)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:txn))"), {"txn": txn})
    other = _find_order(db, account, Order.external_txn_id == txn, Order.id != order.id)
    if other is not None or order.external_txn_id not in (None, txn):
        raise BillingError(409, "Waffo Pancake payment is linked to another order")
    if order.status != "created":
        if order.external_txn_id == txn and db.get(CommerceSettlement, txn) is not None:
            return {"order_no": order.order_no, "replayed": True}
        raise BillingError(409, "Waffo Pancake order requires manual reconciliation")
    proof = verified_payment(account, order, remote, payment_id)
    tax = pancake_accounts.amount_minor(proof["snapshotAmountDetails"].get("taxAmount", "0"), order.currency, allow_zero=True)
    if tax > order.amount_cents:
        raise BillingError(409, "Waffo Pancake payment tax exceeds the order amount")
    order.external_txn_id = txn
    order.checkout_context = {**order.checkout_context, "provider_order_id": _id(remote), "provider_trade_no": payment_id,
                              "provider_amount_paid": order.amount_cents, "provider_tax_minor": tax}
    db.flush()
    return commerce.settle_order(db, order, txn)


def receive(db, code, body, signature):
    account = db.get(GatewayAccount, (PROVIDER, code))
    if account is None:
        raise BillingError(404, "Waffo Pancake account not found")
    config = account.config
    try:
        event = pancake.verify_webhook(body, signature, config["mode"], config["store_id"])
    except pancake.PancakeError:
        raise BillingError(401, "Invalid Waffo Pancake notification") from None
    kind, data = event["eventType"], event["data"]
    if kind.startswith("refund."):
        return _refund_notice(db, account, event)
    number, reference = data.get("orderMerchantExternalId"), data.get("orderId")
    order = _find_order(db, account, Order.checkout_key.is_not(None), Order.order_no == number) if number else None
    if order is None and reference:
        order = _find_order(db, account, Order.checkout_key.is_not(None), Order.subscription_ref == reference)
    if order is None:
        return {"ignored": kind}
    with site_context(db, order.tenant_id):
        account_for_order(db, order)
        if kind.startswith("subscription."):
            from app.modules.billing import pancake_subscriptions
            return pancake_subscriptions.handle_event(db, account, order, event)
        if kind == "order.completed" and order.billing_mode == "one_time":
            proof = data.get("paymentId")
            if (not isinstance(proof, str) or not proof.startswith("PAY_") or data.get("paymentStatus") != "succeeded"):
                raise BillingError(409, "Waffo Pancake notification has no succeeded payment")
            if (data.get("currency", order.currency) != order.currency
                    or (data.get("chargedAmount") is not None
                        and pancake_accounts.amount_minor(data["chargedAmount"], order.currency) != order.amount_cents)):
                raise BillingError(409, "Waffo Pancake notification does not match the frozen amount")
            remote = remote_order(db, account, order)
            if not remote or _id(remote) != reference:
                raise BillingError(409, "Waffo Pancake notification does not match the queried order")
            return _credit_once(db, account, order, remote, proof)
    return {"ignored": kind}


def reconcile(db, order):
    if order.provider != PROVIDER or order.status != "created":
        return
    order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
    now = commerce.now()
    if order.status != "created" or order.checkout_context.get("pancake_query_after", 0) > now.timestamp():
        return
    account = account_for_order(db, order)
    order.checkout_context = {**order.checkout_context, "pancake_query_after": (now + timedelta(seconds=30)).timestamp()}
    db.commit()
    remote = remote_order(db, account, order)
    if remote is None:
        return
    if order.billing_mode == "subscription":
        from app.modules.billing import pancake_subscriptions
        return pancake_subscriptions.reconcile(db, account, order, remote)
    paid = [_id(item) for item in remote.get("payments", []) if item.get("status") == "succeeded"]
    if len(paid) != 1 or not paid[0]:
        return
    return _credit_once(db, account, order, remote, paid[0])


def _refund_notice(db, account, event):
    kind, data = event["eventType"], event["data"]
    payment = data.get("paymentId")
    if not isinstance(payment, str) or not payment.startswith("PAY_"):
        return {"ignored": kind}
    order = _find_order(db, account, Order.external_txn_id == payment_key(account, payment))
    if order is None:
        return {"ignored": kind}
    with site_context(db, order.tenant_id):
        account_for_order(db, order)
        commerce.wallet_lock(db, order.user_id)
        order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
        if data.get("orderId") != order.checkout_context.get("provider_order_id"):
            raise BillingError(409, "Waffo Pancake refund notification identifies another order")
        previous = db.scalar(select(CommerceRefund).where(CommerceRefund.order_id == order.id))
        if previous is not None and previous.status == "pending":
            if (data.get("refundTicketMerchantExternalId") == _refund_reference(order)
                    and data.get("currency") == order.currency
                    and pancake_accounts.amount_minor(data.get("refundedAmount"), order.currency) == order.amount_cents
                    and data.get("refundStatus") in {"succeeded", "failed"} and kind == "refund." + data["refundStatus"]):
                previous.refund_id = _id(event)
                commerce.finalize_refund(db, previous, data["refundStatus"])
            else:
                previous.error = "Waffo Pancake refund notification did not match the requested full refund"
        elif previous is None and kind == "refund.succeeded" and order.status == "paid":
            order.status = "refund_review"
            db.add(CommerceRefund(order_id=order.id, idempotency_key=f"pancake-review:{order.order_no}",
                                  status="manual_review", reason="Provider refund",
                                  error="Verify the Waffo Pancake refund and consumption before changing entitlements"))
        db.commit()
    return {"order_no": order.order_no}


def _refund_reference(order):
    return "jjtv-refund-" + order.order_no


def request_refund(db, order, wallet, refund, actor, reason):
    account = account_for_order(db, order)
    credentials = pancake_accounts.credentials(account)
    payment_id = order.checkout_context["provider_trade_no"]
    reference = _refund_reference(order)
    if refund is None:
        if order.status != "paid" or db.get(CommerceSettlement, order.external_txn_id) is None:
            raise BillingError(409, "Waffo Pancake order has not been settled")
        remote = remote_order(db, account, order)
        if remote is None:
            raise BillingError(409, "Waffo Pancake original order was not found")
        verified_payment(account, order, remote, payment_id)
        refund = commerce.freeze_refund_sources(db, order, wallet, actor, reason)
        try:
            pancake.create_refund_ticket(
                **credentials, payment_id=payment_id, refund_no=reference, amount=gateway_money.as_major(order.amount_cents, order.currency),
                currency=order.currency, reason=reason,
            )
        except pancake.PancakeError:
            return commerce.uncertain_refund(db, order, refund, "Waffo Pancake refund outcome is unknown; retry will only query")
    else:
        db.commit()
    try:
        evidence = pancake.get_refund(**credentials, payment_id=payment_id, refund_no=reference, order_no=order.order_no)
        if evidence is None:
            ticket = pancake.get_refund_ticket(**credentials, payment_id=payment_id, refund_no=reference)
        else:
            ticket = None
    except pancake.PancakeError:
        return commerce.uncertain_refund(db, order, refund, "Waffo Pancake refund lookup failed; original sources remain frozen")
    if evidence is not None and evidence.get("status") in {"succeeded", "failed"}:
        paid = evidence.get("amount") or {}
        psp = evidence.get("pspAmountDetails") or {}
        currency = paid.get("currency", psp.get("currency"))
        amount = paid.get("display", psp.get("amount"))
        if (evidence.get("isAmountMismatch") is True or currency != order.currency
                or amount is None or pancake_accounts.amount_minor(amount, order.currency) != order.amount_cents):
            return commerce.uncertain_refund(db, order, refund, "Waffo Pancake refund amount is not verified")
        status, refund_id = evidence["status"], _id(evidence)
    elif ticket is not None and ticket.get("status") == "rejected":
        amount = (ticket.get("versionData") or {}).get("requestedAmount") or {}
        if (amount.get("currency") != order.currency or amount.get("amount") is None
                or pancake_accounts.amount_minor(amount["amount"], order.currency) != order.amount_cents):
            return commerce.uncertain_refund(db, order, refund, "Waffo Pancake rejected ticket does not match the refund")
        status, refund_id = "failed", None
    else:
        return commerce.uncertain_refund(db, order, refund, "Waffo Pancake has not confirmed the refund; sources remain frozen")
    order, refund = commerce.locked_refund(db, order, refund)
    if refund.status == "pending":
        refund.refund_id = refund_id
        commerce.finalize_refund(db, refund, status)
    db.commit()
    return commerce.refund_out(refund)
