"""Waffo Pancake merchant API and environment-scoped webhook adapter."""

from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import http.client
import json
import re
import time
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


_HOST = "api.waffo.ai"
_MODES = {"test": "test", "production": "prod"}
_HOSTED_CHECKOUT = {"pancake.waffo.ai", "checkout.waffo.ai"}
_TIMEOUT = 8.0
_BODY_LIMIT = 1024 * 1024
_CHECKOUT_PATH = "/v1/actions/checkout/create-session"
_CANCEL_PATH = "/v1/actions/subscription-order/cancel-order"
_REFUND_PATH = "/v1/actions/refund-ticket/create-ticket"
_AMOUNT = re.compile(r"[0-9]{1,14}(?:\.[0-9]{1,12})?\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_TAX_CATEGORY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SHORT_ID_BODY = re.compile(r"[A-Za-z0-9]{1,120}\Z")
_EXTERNAL_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_EVENT_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,255}\Z")
_EVENT_TYPE = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_EVENT_TIMESTAMP = re.compile(r"[0-9]{13}\Z")
_SESSION_ID = re.compile(r"cs_[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")

# Platform public keys from the official Go SDK v0.19.0 (c921ad9f701fb59b).
# https://github.com/waffo-com/waffo-pancake-sdk-go/blob/v0.19.0/webhooks.go
_WEBHOOK_KEYS = {
    "test": b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAxnmRY6yMMA3lVqmAU6ZG
b1sjL/+r/z6E+ZjkXaDAKiqOhk9rpazni0bNsGXwmftTPk9jy2wn+j6JHODD/WH/
SCnSfvKkLIjy4Hk7BuCgB174C0ydan7J+KgXLkOwgCAxxB68t2tezldwo74ZpXgn
F49opzMvQ9prEwIAWOE+kV9iK6gx/AckSMtHIHpUesoPDkldpmFHlB2qpf1vsFTZ
5kD6DmGl+2GIVK01aChy2lk8pLv0yUMu18v44sLkO5M44TkGPJD9qG09wrvVG2wp
OTVCn1n5pP8P+HRLcgzbUB3OlZVfdFurn6EZwtyL4ZD9kdkQ4EZE/9inKcp3c1h4
xwIDAQAB
-----END PUBLIC KEY-----""",
    "production": b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAz+xApdTIb4ua+DgZKQ54
iBsD82ybyhGCLRETONW4Jgbb3A8DUM1LqBk6r/CmTOCHqLalTQHNigvP3R5zkDNX
iRJz6gA4MJ/+8K0+mnEE2RISQzN+Qu65TNd6svb+INm/kMaftY4uIXr6y6kchtTJ
dwnQhcKdAL2v7h7IFnkVelQsKxDdb2PqX8xX/qwd01iXvMcpCCaXovUwZsxH2QN5
ZKBTseJivbhUeyJCco4fdUyxOMHe2ybCVhyvim2uxAl1nkvL5L8RCWMCAV55LLo0
9OhmLahz/DYNu13YLVP6dvIT09ZFBYU6Owj1NxdinTynlJCFS9VYwBgmftosSE1U
dwIDAQAB
-----END PUBLIC KEY-----""",
}

_STORE_QUERY = "query MerchantStores { stores { id name status prodEnabled } }"
_CATALOG_QUERY = """
query StoreCatalog($storeId: String!) {
  store(id: $storeId) {
    id
    onetimeProducts { id name status prices { currency priceInfo { amount taxCategory } } }
    subscriptionProducts { id name status billingPeriod prices { currency priceInfo { amount taxCategory } } }
  }
}
"""
_PAYMENT_FIELDS = """
  id orderId status refundStatus orderMerchantExternalId periodNumber createdAt
  amount { display currency }
  snapshotAmountDetails { currency subtotal listTotal discountAmount taxAmount total phase }
  refunds { id status }
"""
_ORDER_FIELDS = {
    "one_time": """
      onetimeProduct { id }
      priceSnapshot { currency subtotal taxAmount total taxCategory }
    """,
    "subscription": """
      subscriptionProduct { id }
      billingPeriod currentPeriodStart currentPeriodEnd currentPeriodNumber canceledAt
      priceSnapshot { currency regularPhase { subtotal taxAmount total taxCategory } }
    """,
}
_REFUND_FIELDS = """
  id paymentId ticketId status orderMerchantExternalId refundTicketMerchantExternalId isAmountMismatch
  amount { display currency }
  requestedAmountDetails { amount currency }
  pspAmountDetails { amount currency }
"""


class PancakeError(ValueError):
    pass


class PancakeConfigurationError(PancakeError):
    pass


class PancakeTransportError(PancakeError):
    pass


class PancakeVerificationError(PancakeError):
    pass


class PancakeHTTPError(PancakeError):
    def __init__(self, status_code: int):
        super().__init__("Waffo Pancake returned an unsuccessful response")
        self.status_code = status_code


def _mode(mode):
    if not isinstance(mode, str) or mode not in _MODES:
        raise PancakeConfigurationError("Invalid Waffo Pancake environment")
    return _MODES[mode]


def _short_id(value, prefix):
    return (isinstance(value, str) and value.startswith(prefix + "_")
            and _SHORT_ID_BODY.fullmatch(value[len(prefix) + 1:]) is not None)


def _id(value, prefix):
    if not _short_id(value, prefix):
        raise PancakeConfigurationError(f"Invalid Waffo Pancake {prefix} identifier")
    return value


def _reference(value):
    if not isinstance(value, str) or not _EXTERNAL_REFERENCE.fullmatch(value):
        raise PancakeConfigurationError("Invalid Waffo Pancake business reference")
    return value


def _amount(amount, currency):
    if (not isinstance(amount, str) or not _AMOUNT.fullmatch(amount) or not amount.strip("0.")
            or not isinstance(currency, str) or not _CURRENCY.fullmatch(currency)):
        raise PancakeConfigurationError("Invalid Waffo Pancake amount or currency")


def _private_key(value):
    try:
        if not isinstance(value, str) or not value or len(value) > 16_384:
            raise ValueError
        normalized = value.strip().replace("\\r\\n", "\n").replace("\\n", "\n").encode("ascii")
        if normalized.startswith(b"-----BEGIN "):
            key = serialization.load_pem_private_key(normalized, password=None)
        else:
            key = serialization.load_der_private_key(base64.b64decode(b"".join(normalized.split()), validate=True), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError
        return key
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise PancakeConfigurationError("Invalid Waffo Pancake RSA private key") from None


def _unique_object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("Duplicate JSON key")
        data[key] = value
    return data


def _invalid_json_constant(_value):
    raise ValueError("Invalid JSON number")


def _parse_object(body):
    data = json.loads(body, object_pairs_hook=_unique_object, parse_constant=_invalid_json_constant)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def _send(*, merchant_id, private_key, mode, path, payload, idempotency_key=None):
    _id(merchant_id, "MER")
    _mode(mode)
    key = _private_key(private_key)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    body_hash = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
    signed = f"POST\n{path}\n{timestamp}\n{body_hash}".encode("ascii")
    signature = key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    headers = {
        "Accept": "application/json", "Content-Type": "application/json", "X-Merchant-Id": merchant_id,
        "X-Timestamp": timestamp, "X-Signature": base64.b64encode(signature).decode("ascii"),
    }
    if idempotency_key is not None:
        headers["X-Idempotency-Key"] = _reference(idempotency_key)
    connection = http.client.HTTPSConnection(_HOST, timeout=_TIMEOUT)
    try:
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise PancakeHTTPError(response.status)
        raw = response.read(_BODY_LIMIT + 1)
        if len(raw) > _BODY_LIMIT:
            raise PancakeTransportError("Waffo Pancake response exceeds the size limit")
        envelope = _parse_object(raw)
    except PancakeError:
        raise
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        raise PancakeTransportError("Waffo Pancake request failed") from None
    finally:
        connection.close()
    if envelope.get("errors") or not isinstance(envelope.get("data"), dict):
        raise PancakeTransportError("Waffo Pancake did not confirm the requested operation")
    return envelope["data"]


def _graphql(credentials, query, variables=None):
    body = {"query": query}
    if variables is not None:
        body["variables"] = variables
    return _send(**credentials, path="/v1/graphql", payload=body)


def _results(payload, field):
    items = payload.get(field)
    if not isinstance(items, list) or not all(isinstance(row, dict) for row in items):
        raise PancakeVerificationError("Waffo Pancake returned invalid query results")
    return items


def _one(payload, field):
    rows = _results(payload, field)
    if len(rows) > 1:
        raise PancakeVerificationError("Waffo Pancake business reference is ambiguous")
    return rows[0] if rows else None


def list_stores(*, merchant_id, private_key, mode):
    credentials = dict(merchant_id=merchant_id, private_key=private_key, mode=mode)
    rows = _results(_graphql(credentials, _STORE_QUERY), "stores")
    if not all(_short_id(row.get("id"), "STO") and isinstance(row.get("name"), str)
               and isinstance(row.get("status"), str) and type(row.get("prodEnabled")) is bool for row in rows):
        raise PancakeVerificationError("Waffo Pancake returned an invalid store catalog")
    return [{"id": row["id"], "name": row["name"], "status": row["status"], "prod_enabled": row["prodEnabled"]}
            for row in rows]


def _catalog_product(item, billing_mode):
    if (not _short_id(item.get("id"), "PROD") or not isinstance(item.get("name"), str)
            or not isinstance(item.get("status"), str)):
        raise PancakeVerificationError("Waffo Pancake returned an invalid product")
    prices = []
    for price in _results(item, "prices"):
        info = price.get("priceInfo")
        if not isinstance(info, dict) or not all(isinstance(v, str) for v in (
            price.get("currency"), info.get("amount"), info.get("taxCategory"),
        )):
            raise PancakeVerificationError("Waffo Pancake returned an invalid product price")
        prices.append({"currency": price["currency"], "amount": info["amount"], "tax_category": info["taxCategory"]})
    return {"id": item["id"], "name": item["name"], "status": item["status"], "billing_mode": billing_mode,
            "billing_period": item.get("billingPeriod"), "prices": prices}


def get_catalog(*, merchant_id, private_key, mode, store_id):
    credentials = dict(merchant_id=merchant_id, private_key=private_key, mode=mode)
    data = _graphql(credentials, _CATALOG_QUERY, {"storeId": _id(store_id, "STO")})
    store = data.get("store")
    if not isinstance(store, dict) or store.get("id") != store_id:
        raise PancakeVerificationError("Waffo Pancake store does not match the requested catalog")
    return [
        _catalog_product(product, billing_mode)
        for field, billing_mode in (("onetimeProducts", "one_time"), ("subscriptionProducts", "subscription"))
        for product in _results(store, field)
    ]


def _https_url(value):
    if not isinstance(value, str) or "\\" in value or any(ord(c) < 33 or c.isspace() for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname and parsed.username is None and parsed.port in (None, 443):
            return parsed
    except ValueError:
        pass
    return None


def _iso_date(value):
    try:
        return isinstance(value, str) and datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def create_checkout(*, merchant_id, private_key, mode, store_id, product_id, order_no, amount, currency,
                    tax_category, success_url, customer_email=None):
    _id(store_id, "STO")
    _amount(amount, currency)
    _reference(order_no)
    if not _https_url(success_url) or not isinstance(tax_category, str) or not _TAX_CATEGORY.fullmatch(tax_category):
        raise PancakeConfigurationError("Invalid Waffo Pancake return URL or product tax category")
    payload = {
        "productId": _id(product_id, "PROD"), "currency": currency,
        "priceSnapshot": {"amount": amount, "taxIncluded": True, "taxCategory": tax_category},
        "successUrl": success_url, "orderMerchantExternalId": order_no,
        "metadata": {"jjtv_order_no": order_no}, "withTrial": False, "allowPromotionCodes": False,
    }
    if customer_email:
        payload["buyerEmail"] = customer_email
    token = hashlib.sha256(f"{merchant_id}:{mode}:{store_id}:{order_no}".encode()).hexdigest()
    data = _send(merchant_id=merchant_id, private_key=private_key, mode=mode, path=_CHECKOUT_PATH, payload=payload,
                 idempotency_key="jjtv-pancake-checkout-" + token)
    session_id, checkout_url, expiry = data.get("sessionId"), data.get("checkoutUrl"), data.get("expiresAt")
    parsed = _https_url(checkout_url)
    if (not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id) or not parsed
            or parsed.hostname not in _HOSTED_CHECKOUT or parsed.path.split("/")[-2:] != ["checkout", session_id]
            or not _iso_date(expiry)):
        raise PancakeVerificationError("Waffo Pancake returned an invalid hosted checkout")
    return {"session_id": session_id, "checkout_url": checkout_url, "expires_at": expiry}


def _matches_order(row, *, mode, store_id, order_no):
    store = row.get("store")
    return (_short_id(row.get("id"), "ORD") and row.get("orderMerchantExternalId") == order_no
            and row.get("testMode") is (mode == "test") and isinstance(store, dict) and store.get("id") == store_id)


def get_order(*, merchant_id, private_key, mode, store_id, order_no, billing_mode):
    if billing_mode not in _ORDER_FIELDS:
        raise PancakeConfigurationError("Invalid Waffo Pancake billing mode")
    field = "onetimeOrders" if billing_mode == "one_time" else "subscriptionOrders"
    query = f"""
query CheckoutOrder($storeId: String!, $reference: String!) {{
  {field}(storeId: $storeId, filter: {{orderMerchantExternalId: {{eq: $reference}}}}, limit: 2) {{
    id status orderMerchantExternalId testMode store {{ id }} updatedAt
    productVersion {{ id }}
    {_ORDER_FIELDS[billing_mode]}
    payments {{ {_PAYMENT_FIELDS} }}
  }}
}}
"""
    credentials = dict(merchant_id=merchant_id, private_key=private_key, mode=mode)
    variables = {"storeId": _id(store_id, "STO"), "reference": _reference(order_no)}
    row = _one(_graphql(credentials, query, variables), field)
    if row is not None and not _matches_order(row, mode=mode, store_id=store_id, order_no=order_no):
        raise PancakeVerificationError("Waffo Pancake order does not match the store, reference and environment")
    return row


def get_payment(*, merchant_id, private_key, mode, store_id, order_no, payment_id):
    query = f"""
query CheckoutPayment($id: String!) {{
  payment(id: $id) {{
    {_PAYMENT_FIELDS}
    onetimeOrder {{ id orderMerchantExternalId testMode store {{ id }} }}
    subscriptionOrder {{ id orderMerchantExternalId testMode store {{ id }} }}
  }}
}}
"""
    credentials = dict(merchant_id=merchant_id, private_key=private_key, mode=mode)
    _id(store_id, "STO")
    _reference(order_no)
    data = _graphql(credentials, query, {"id": _id(payment_id, "PAY")})
    payment = data.get("payment")
    if payment is None:
        return None
    if not isinstance(payment, dict):
        raise PancakeVerificationError("Waffo Pancake returned an invalid payment")
    orders = [payment[key] for key in ("onetimeOrder", "subscriptionOrder") if payment.get(key) is not None]
    if (len(orders) != 1 or not isinstance(orders[0], dict) or payment.get("id") != payment_id
            or not _matches_order(orders[0], mode=mode, store_id=store_id, order_no=order_no)
            or payment.get("orderId") != orders[0].get("id") or payment.get("orderMerchantExternalId") != order_no):
        raise PancakeVerificationError("Waffo Pancake payment does not match the order, store and environment")
    return {**payment, "storeId": store_id, "testMode": mode == "test"}


def cancel_subscription(*, merchant_id, private_key, mode, order_id):
    data = _send(merchant_id=merchant_id, private_key=private_key, mode=mode, path=_CANCEL_PATH,
                 payload={"orderId": _id(order_id, "ORD")})
    if data.get("orderId") != order_id or data.get("status") not in ("canceling", "canceled", "closed", "expired"):
        raise PancakeVerificationError("Waffo Pancake did not confirm subscription cancellation")
    return data


def create_refund_ticket(*, merchant_id, private_key, mode, payment_id, refund_no, amount, currency, reason):
    _reference(refund_no)
    _amount(amount, currency)
    if not isinstance(reason, str) or not reason.strip():
        raise PancakeConfigurationError("Waffo Pancake refund reason is required")
    body = {
        "paymentId": _id(payment_id, "PAY"), "reason": reason,
        "requestedAmount": {"amount": amount, "currency": currency},
        "refundTicketMerchantExternalId": refund_no,
    }
    token = hashlib.sha256(f"{merchant_id}:{mode}:{payment_id}:{refund_no}".encode()).hexdigest()
    data = _send(merchant_id=merchant_id, private_key=private_key, mode=mode, path=_REFUND_PATH, payload=body,
                 idempotency_key="jjtv-pancake-refund-" + token)
    ticket = data.get("ticket")
    if (not isinstance(ticket, dict) or not _short_id(ticket.get("id"), "TKT")
            or ticket.get("subjectId") != payment_id or ticket.get("refundTicketMerchantExternalId") != refund_no):
        raise PancakeVerificationError("Waffo Pancake refund ticket does not match the request")
    return ticket


def get_refund_ticket(*, merchant_id, private_key, mode, payment_id, refund_no):
    """Read the refund request. A ticket alone does not prove funds were returned."""
    query = """
query RefundTicketByReference($reference: String!) {
  refundTickets(filter: {refundTicketMerchantExternalId: {eq: $reference}}, limit: 2) {
    id paymentId status refundTicketMerchantExternalId executedAt
    versionData { requestedAmount { amount currency } }
  }
}
"""
    credentials = dict(merchant_id=merchant_id, private_key=private_key, mode=mode)
    _id(payment_id, "PAY")
    row = _one(_graphql(credentials, query, {"reference": _reference(refund_no)}), "refundTickets")
    if row is not None and (not _short_id(row.get("id"), "TKT") or row.get("paymentId") != payment_id
                            or row.get("refundTicketMerchantExternalId") != refund_no):
        raise PancakeVerificationError("Waffo Pancake refund ticket does not match the stored payment and reference")
    return row


def get_refund(*, merchant_id, private_key, mode, payment_id, refund_no, order_no):
    query = f"""
query RefundByReference($reference: String!) {{
  refunds(filter: {{refundTicketMerchantExternalId: {{eq: $reference}}}}, limit: 2) {{ {_REFUND_FIELDS} }}
}}
"""
    credentials = dict(merchant_id=merchant_id, private_key=private_key, mode=mode)
    _id(payment_id, "PAY")
    _reference(order_no)
    row = _one(_graphql(credentials, query, {"reference": _reference(refund_no)}), "refunds")
    if row is not None and (not (_short_id(row.get("id"), "REF") or _short_id(row.get("id"), "RFD"))
                            or row.get("paymentId") != payment_id or row.get("orderMerchantExternalId") != order_no
                            or row.get("refundTicketMerchantExternalId") != refund_no):
        raise PancakeVerificationError("Waffo Pancake refund does not match the stored payment and reference")
    return row


def verify_webhook(body, signature, mode, store_id):
    expected_mode = _mode(mode)
    _id(store_id, "STO")
    if (not isinstance(body, bytes) or not body or len(body) > _BODY_LIMIT
            or not isinstance(signature, str) or len(signature) > 2048):
        raise PancakeVerificationError("Invalid Waffo Pancake webhook signature or body")
    fields = {}
    for part in signature.split(","):
        name, separator, value = part.strip().partition("=")
        if name in {"t", "v1"}:
            if not separator or name in fields:
                raise PancakeVerificationError("Invalid Waffo Pancake webhook signature header")
            fields[name] = value.strip()
    timestamp = fields.get("t", "")
    if not _EVENT_TIMESTAMP.fullmatch(timestamp) or not fields.get("v1"):
        raise PancakeVerificationError("Invalid Waffo Pancake webhook signature header")
    # Provider retries reuse the original signature for over 30 minutes.
    age_ms = int(time.time() * 1000) - int(timestamp)
    if not -60_000 <= age_ms <= 45 * 60_000:
        raise PancakeVerificationError("Waffo Pancake webhook timestamp is outside the delivery window")
    try:
        key = serialization.load_pem_public_key(_WEBHOOK_KEYS[mode])
        proof = base64.b64decode(fields["v1"], validate=True)
        key.verify(proof, timestamp.encode("ascii") + b"." + body, padding.PKCS1v15(), hashes.SHA256())
        event = _parse_object(body)
    except (InvalidSignature, ValueError, TypeError, RecursionError):
        raise PancakeVerificationError("Invalid Waffo Pancake webhook signature or body") from None
    if (event.get("mode") != expected_mode or event.get("storeId") != store_id
            or not isinstance(event.get("data"), dict) or not _iso_date(event.get("timestamp"))
            or not all(isinstance(event.get(field), str) and _EVENT_REFERENCE.fullmatch(event[field]) for field in ("id", "eventId"))
            or not isinstance(event.get("eventType"), str) or not _EVENT_TYPE.fullmatch(event["eventType"])):
        raise PancakeVerificationError("Waffo Pancake webhook scope or event does not match")
    return event
