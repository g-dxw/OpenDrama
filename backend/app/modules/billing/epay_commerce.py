"""Epay V1 checkout and settlement backed by the existing commerce ledger."""

import hashlib
import os
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select, text

from app.modules.billing import commerce, epay, gateway_accounts
from app.modules.billing.commerce_models import CommerceSettlement, GatewayAccount, SiteGatewayBinding
from app.modules.billing.models import Order
from app.modules.billing.service import BillingError, billing_mode
from app.modules.billing.site_merchants import site_currency
from app.modules.ops.managed_settings import decrypt_secret, encrypt_secret
from app.modules.tenancy.consumer_scope import current_site, site_context

PROVIDER = "epay"
CURRENCY = "CNY"
METHOD_LABELS = {"alipay": "Alipay", "wxpay": "WeChat Pay", "qqpay": "QQ Wallet"}


def notify_url(code):
    base = os.environ.get("PAYMENT_WEBHOOK_BASE_URL", "https://jjtv-api.8jj.net").rstrip("/")
    return f"{base}/v1/billing/epay/accounts/{code}/webhook"


def account_public(row):
    return {
        "code": row.code, "name": row.name, "status": row.status, "currency": CURRENCY,
        "endpoint": row.config["endpoint"], "merchant_id": row.config["merchant_id"],
        "payment_methods": row.config["payment_methods"], "secret_configured": bool(row.credentials.get("secret_key")),
        "notify_url": notify_url(row.code),
    }


def _credentials(row):
    return {
        "endpoint": row.config["endpoint"], "merchant_id": row.config["merchant_id"],
        "secret": decrypt_secret(row.credentials["secret_key"]) if row.credentials.get("secret_key") else "",
    }


def _verify(credentials):
    try:
        if epay.verify_merchant(**credentials):
            return
    except epay.EpayTransportError:
        raise BillingError(502, "Epay merchant verification is unavailable") from None
    except epay.EpayError:
        pass
    raise BillingError(422, "Epay rejected the merchant credentials")


def save_account(db, code, values, actor, *, create=False):
    gateway_accounts.lock_gateways(db, PROVIDER)
    row = db.get(GatewayAccount, (PROVIDER, code))
    if create and row is not None:
        raise BillingError(409, "Epay account code already exists")
    if not create and row is None:
        raise BillingError(404, "Epay account not found")
    previous = _credentials(row) if row is not None else {}
    config = dict(row.config) if row is not None else {}
    config.update({key: values[key] for key in ("endpoint", "merchant_id", "payment_methods") if key in values})
    try:
        config["endpoint"] = epay.normalize_endpoint(config["endpoint"])
    except epay.EpayConfigurationError as exc:
        raise BillingError(422, str(exc)) from None
    config["payment_methods"] = list(dict.fromkeys(config["payment_methods"]))
    supplied = values.get("secret_key")
    candidate = {
        "endpoint": config["endpoint"], "merchant_id": config["merchant_id"],
        "secret": supplied or previous.get("secret", ""),
    }
    identity = hashlib.sha256((config["endpoint"] + "\n" + config["merchant_id"]).encode()).hexdigest()
    alias = db.scalar(select(GatewayAccount.code).where(
        GatewayAccount.provider == PROVIDER, GatewayAccount.identity == identity, GatewayAccount.code != code,
    ))
    if alias is not None:
        raise BillingError(409, "This Epay merchant already has an account")
    if row is not None and previous != candidate:
        history = db.scalar(select(Order.id).where(Order.provider == PROVIDER, Order.account_code == code)
                            .execution_options(include_all_tenants=True).limit(1))
        if history is not None:
            raise BillingError(409, "Existing orders need their original Epay credentials")
    status = values.get("status") or (row.status if row is not None else "draft")
    if status == "active":
        if not candidate["secret"] or not config["payment_methods"]:
            raise BillingError(422, "Configure the Epay secret and payment methods before activating")
        if row is None or row.status != "active" or previous != candidate:
            _verify(candidate)
    if row is None:
        row = GatewayAccount(provider=PROVIDER, code=code, credentials={})
        db.add(row)
    if supplied and supplied != previous.get("secret"):
        row.credentials = {"secret_key": encrypt_secret(supplied)}
    row.name = values.get("name", row.name)
    row.status = status
    row.identity = identity
    row.config = config
    commerce.audit(db, actor, "gateway.account.save", payload={"provider": PROVIDER, "code": code, "status": status})
    db.commit()
    return account_public(row)


def test_account(db, code):
    row = db.get(GatewayAccount, (PROVIDER, code))
    if row is None:
        raise BillingError(404, "Epay account not found")
    _verify(_credentials(row))
    return account_public(row)


def binding_info(db):
    site = gateway_accounts.native_site(db)
    bound = gateway_accounts.bound_account(db, PROVIDER)
    taken = select(SiteGatewayBinding.account_code).where(
        SiteGatewayBinding.provider == PROVIDER, SiteGatewayBinding.tenant_id != site,
    )
    available = db.scalars(select(GatewayAccount).where(
        GatewayAccount.provider == PROVIDER, GatewayAccount.code.not_in(taken),
    ).order_by(GatewayAccount.code))
    currency = site_currency(db)
    return {
        "account_code": bound.code if bound else None, "currency": currency,
        "configured": bool(bound and bound.status == "active" and currency == CURRENCY),
        "available_accounts": [{"code": row.code, "name": row.name, "status": row.status} for row in available],
    }


def bind(db, code, actor):
    gateway_accounts.bind(db, PROVIDER, code, actor, currency=CURRENCY)
    return binding_info(db)


def channels(db):
    if site_currency(db) != CURRENCY:
        return []
    row = gateway_accounts.bound_account(db, PROVIDER)
    if row is None or row.status != "active":
        return []
    eligible = commerce.sale_allowed(db, "coin_pack") or (
        billing_mode(db) == "one_time" and commerce.sale_allowed(db, "membership")
    )
    if not eligible:
        return []
    return [
        {"code": row.code, "name": METHOD_LABELS.get(method, row.name), "provider": PROVIDER,
         "method": method, "enabled": True, "billing_modes": ["one_time"]}
        for method in row.config["payment_methods"]
    ]


def _return_to_order(url, order_no):
    target = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(target.query, keep_blank_values=True)
             if key not in ("order_no", "payment_provider")]
    query.extend((("order_no", order_no), ("payment_provider", PROVIDER)))
    return urlunsplit(target._replace(query=urlencode(query)))


def checkout(db, *, user_id, product_type, code, idempotency_key, account_code, payment_method,
             success_url, cancel_url, source_drama_id, attribution_pid):
    from app.modules.billing.site_merchants import assert_site_currency
    from app.modules.tenancy.consumer_scope import require_owned
    from app.modules.account.models import User
    from secrets import token_hex

    require_owned(db, User, user_id)
    key = commerce.checkout_key(user_id, idempotency_key)
    commerce.wallet_lock(db, user_id)
    order = db.scalar(select(Order).where(Order.checkout_key == key))
    if order is not None:
        if (order.provider != PROVIDER or order.product_type != product_type or order.product_snapshot.get("code") != code
                or order.account_code != account_code or order.checkout_context.get("payment_method") != payment_method):
            raise BillingError(409, "Idempotency key was used for another checkout")
        if order.status != "created":
            if order.status in {"paid", "refunded", "refund_pending", "refund_review"}:
                raise commerce.checkout_conflict(order, "checkout_already_paid", "This order was already paid. View your orders.", "view_orders")
            raise commerce.checkout_conflict(order, "checkout_recovery_required", "This Epay order needs support review.", "contact_support")
        return commerce._checkout_result(order, True)
    commerce.require_new_sale(db, product_type)
    gateway_accounts.lock_gateways(db, PROVIDER)
    account = gateway_accounts.bound_account(db, PROVIDER)
    if account is None or account.status != "active" or account.code != account_code:
        raise BillingError(403, "Epay account is not enabled for this site")
    if payment_method not in account.config["payment_methods"]:
        raise BillingError(422, "Select an enabled Epay payment method")
    snapshot = commerce.product_snapshot(db, product_type, code)
    if assert_site_currency(db, snapshot["currency"]) != CURRENCY or snapshot["billing_mode"] != "one_time":
        raise BillingError(409, "Epay requires a one-time CNY product")
    if product_type == "membership":
        commerce._membership_refund_checkout_guard(db, user_id)
    commerce.validate_checkout_source(db, source_drama_id)
    success = commerce.return_url(success_url, "success", db)
    commerce.return_url(cancel_url, "cancel", db)
    order_no = token_hex(12)
    try:
        url = epay.checkout_url(
            **_credentials(account), method=payment_method, order_no=order_no, name=snapshot["name"],
            amount_minor=snapshot["price_cents"], notify_url=notify_url(account.code),
            return_url=_return_to_order(success, order_no),
        )
    except epay.EpayError:
        raise BillingError(422, "Epay could not create a checkout for this product") from None
    if len(url) > 2000:
        raise BillingError(422, "Epay checkout return address is too long")
    order = Order(
        order_no=order_no, user_id=user_id, provider=PROVIDER, account_code=account.code,
        product_type=product_type, product_snapshot=snapshot,
        plan_version_id=snapshot["version_id"] if product_type == "membership" else None,
        amount_cents=snapshot["price_cents"], currency=CURRENCY, billing_mode="one_time", status="created",
        checkout_key=key, checkout_url=url, checkout_context={"payment_method": payment_method},
        source_drama_id=source_drama_id, attribution_pid=attribution_pid,
    )
    db.add(order)
    db.commit()
    return commerce._checkout_result(order)


def _apply_payment(db, account, order, payment):
    if (payment.order_no != order.order_no or payment.method != order.checkout_context.get("payment_method")
            or payment.amount_minor != order.amount_cents or order.currency != CURRENCY):
        raise BillingError(409, "Epay payment does not match the order")
    if not payment.paid:
        return
    commerce.wallet_lock(db, order.user_id)
    order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
    txn = "epay:" + hashlib.sha256((account.identity + ":" + payment.trade_no).encode()).hexdigest()
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:txn))"), {"txn": txn})
    external = db.scalar(select(Order.id).where(Order.external_txn_id == txn).execution_options(include_all_tenants=True))
    if (external is not None and external != order.id) or (order.external_txn_id and order.external_txn_id != txn):
        raise BillingError(409, "Epay transaction does not match the stored payment")
    if order.status != "created":
        if order.external_txn_id == txn and db.get(CommerceSettlement, txn) is not None:
            return
        raise BillingError(409, "Epay payment requires manual reconciliation")
    order.external_txn_id = txn
    order.checkout_context = {**order.checkout_context, "provider_trade_no": payment.trade_no}
    db.flush()
    commerce.settle_order(db, order, txn)


def receive(db, code, params):
    account = db.get(GatewayAccount, (PROVIDER, code))
    if account is None:
        raise BillingError(404, "Epay account not found")
    credentials = _credentials(account)
    try:
        payment = epay.verify_callback(params, credentials["merchant_id"], credentials["secret"])
    except epay.EpayError:
        raise BillingError(401, "Invalid Epay payment notification") from None
    order = db.scalar(select(Order).where(
        Order.order_no == payment.order_no, Order.provider == PROVIDER, Order.account_code == account.code,
    ).execution_options(include_all_tenants=True))
    if order is None:
        raise BillingError(404, "Epay order not found")
    with site_context(db, order.tenant_id):
        _apply_payment(db, account, order, payment)
        db.commit()


def reconcile(db, order):
    if order.provider != PROVIDER or order.status != "created":
        return
    order = db.get(Order, order.id, with_for_update=True, populate_existing=True)
    if order.status != "created":
        return
    point = commerce.now()
    if order.checkout_context.get("epay_query_after", 0) > point.timestamp():
        return
    order.checkout_context = {
        **order.checkout_context, "epay_query_after": (point + timedelta(seconds=30)).timestamp(),
    }
    account = db.get(GatewayAccount, (PROVIDER, order.account_code))
    db.commit()
    if account is None:
        raise BillingError(503, "Epay merchant is unavailable")
    try:
        payment = epay.query_order(**_credentials(account), order_no=order.order_no)
    except epay.EpayError:
        raise BillingError(502, "Epay order status is temporarily unavailable") from None
    if payment is not None:
        _apply_payment(db, account, order, payment)
        db.commit()
