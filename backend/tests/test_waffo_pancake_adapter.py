import base64
from collections import deque
import hashlib
import io
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import pytest

from app.modules.billing import waffo_pancake as pancake


NOW = 1_795_144_320
STORE = "STO_3bVzrkD0FJjFdZNLk8Ualx"
PRODUCT = "PROD_4cWAslE1GKkGeaOMl9Vbmy"
PAYMENT = "PAY_6eYCunG3IMmIgcQOnaXdoA"
ORDER = "ORD_5dXBtmF2HLlHfbPNm0Wcnz"
SESSION = "cs_550e8400-e29b-41d4-a716-446655440000"
REFERENCE = "JJTV-2026-00891"
REFUND = "JJTV-REF-00891"
TICKET = "TKT_3bVzrkD0FJjFdZNLk8Ualx"
TIMESTAMP = "2026-11-20T00:00:00.000Z"
NEXT_MONTH = "2026-12-20T00:00:00.000Z"


@pytest.fixture(scope="module")
def keys():
    return {mode: rsa.generate_private_key(public_exponent=65537, key_size=2048) for mode in ("test", "production")}


@pytest.fixture
def credentials(keys):
    pem = keys["test"].private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()).decode()
    return {"merchant_id": "MER_2aUyqjCzEIiEcYMKj7TZtw", "private_key": pem, "mode": "test"}


@pytest.fixture
def api(monkeypatch):
    calls = []
    replies = deque()

    class Response(io.BytesIO):
        def __init__(self, status, raw):
            super().__init__(raw if isinstance(raw, bytes) else json.dumps(raw).encode())
            self.status = status

    class Connection:
        def __init__(self, host, timeout):
            assert host == "api.waffo.ai"
            assert 0 < timeout <= 10

        def request(self, method, path, body, headers):
            calls.append((method, path, body, headers))

        def getresponse(self):
            reply = replies.popleft()
            if isinstance(reply, Exception):
                raise reply
            return Response(*reply)

        def close(self):
            pass

    monkeypatch.setattr(pancake.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(pancake.time, "time", lambda: NOW)
    return calls, replies


def _response(replies, data):
    replies.append((200, {"data": data}))


def _hosted_checkout_data():
    return {
        "sessionId": SESSION, "expiresAt": NEXT_MONTH,
        "checkoutUrl": f"https://checkout.waffo.ai/my-store/checkout/{SESSION}",
    }


def _checkout_args():
    return {
        "store_id": STORE, "product_id": PRODUCT, "order_no": REFERENCE,
        "amount": "29.00", "currency": "USD", "tax_category": "digital_goods",
        "success_url": "https://jjtv.example/orders/return?order=JJTV-2026-00891", "customer_email": "buyer@example.com",
    }


def test_exact_rsa_checkout_request_and_stable_idempotency(keys, credentials, api):
    calls, replies = api
    data = _hosted_checkout_data()
    for _ in range(3):
        _response(replies, data)

    for _ in range(2):
        assert pancake.create_checkout(**credentials, **_checkout_args()) == {
            "session_id": SESSION, "checkout_url": data["checkoutUrl"], "expires_at": NEXT_MONTH,
        }
    pancake.create_checkout(**credentials, **{**_checkout_args(), "order_no": REFERENCE + "-NEXT"})
    method, path, body, headers = calls[0]
    assert (method, path) == ("POST", "/v1/actions/checkout/create-session")
    assert headers["X-Merchant-Id"] == credentials["merchant_id"]
    assert headers["X-Timestamp"] == str(NOW)
    assert "X-Environment" not in headers
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    signed = f"POST\n{path}\n{NOW}\n{digest}".encode()
    keys["test"].public_key().verify(base64.b64decode(headers["X-Signature"]), signed,
                                     padding.PKCS1v15(), hashes.SHA256())
    assert json.loads(body) == {
        "productId": PRODUCT, "currency": "USD", "priceSnapshot": {
            "amount": "29.00", "taxIncluded": True, "taxCategory": "digital_goods",
        },
        "metadata": {"jjtv_order_no": REFERENCE}, "orderMerchantExternalId": REFERENCE,
        "successUrl": _checkout_args()["success_url"], "buyerEmail": "buyer@example.com",
        "allowPromotionCodes": False, "withTrial": False,
    }
    assert calls[1][3]["X-Idempotency-Key"] == headers["X-Idempotency-Key"]
    assert calls[2][3]["X-Idempotency-Key"] != headers["X-Idempotency-Key"]


@pytest.mark.parametrize("url", [
    f"http://checkout.waffo.ai/store/checkout/{SESSION}",
    f"https://checkout.waffo.ai.attacker.invalid/store/checkout/{SESSION}",
    f"https://checkout.waffo.ai@attacker.invalid/store/checkout/{SESSION}",
    f"https://checkout.waffo.ai:8443/store/checkout/{SESSION}",
    f"https://checkout.waffo.ai/store/change/{SESSION}",
    f"https://checkout.waffo.ai/store/checkout/{SESSION}-other",
])
def test_non_checkout_or_untrusted_redirect_rejected(url, credentials, api):
    _, replies = api
    _response(replies, {**_hosted_checkout_data(), "checkoutUrl": url})
    with pytest.raises(pancake.PancakeVerificationError):
        pancake.create_checkout(**credentials, **_checkout_args())


@pytest.mark.parametrize("patch", [
    {"mode": "prod"}, {"private_key": "canary-private-key"}, {"merchant_id": "MER_bad\nX-Extra: a"},
])
def test_invalid_credentials_fail_before_io(patch, credentials, api):
    calls, _ = api
    with pytest.raises(pancake.PancakeConfigurationError) as exc:
        pancake.list_stores(**{**credentials, **patch})
    assert "canary" not in str(exc.value)
    assert not calls


def test_merchant_probe_accepts_base64_der(keys, credentials, api):
    _, replies = api
    der = keys["test"].private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption())
    _response(replies, {"stores": []})
    assert pancake.list_stores(**{**credentials, "private_key": base64.b64encode(der).decode()}) == []


@pytest.mark.parametrize("reply, error", [
    ((302, b"not parsed"), pancake.PancakeHTTPError),
    ((401, b"{\"message\":\"canary-private-value\"}"), pancake.PancakeHTTPError),
    ((200, {"data": {"stores": []}, "errors": [{"message": "canary-private-value"}]}), pancake.PancakeTransportError),
    ((200, b"{}{}"), pancake.PancakeTransportError),
    ((200, b"x" * (1024 * 1024 + 1)), pancake.PancakeTransportError),
    (OSError("canary-private-value"), pancake.PancakeTransportError),
])
def test_transport_errors_are_sanitized_and_do_not_retry(reply, error, credentials, api):
    calls, replies = api
    replies.append(reply)
    with pytest.raises(error) as exc:
        pancake.list_stores(**credentials)
    assert "canary-private-value" not in str(exc.value)
    if isinstance(exc.value, pancake.PancakeHTTPError):
        assert exc.value.status_code == reply[0]
    assert len(calls) == 1


def test_store_catalog_is_normalized_and_scoped(credentials, api):
    calls, replies = api
    product = {
        "id": PRODUCT, "name": "JJTV Premium", "status": "active",
        "prices": [{"currency": "USD", "priceInfo": {"amount": "29.00", "taxCategory": "digital_goods"}}],
    }
    _response(replies, {"stores": [{"id": STORE, "name": "JJTV", "status": "active", "prodEnabled": True}]})
    assert pancake.list_stores(**credentials) == [{"id": STORE, "name": "JJTV", "status": "active", "prod_enabled": True}]
    _response(replies, {"store": {"id": STORE, "onetimeProducts": [product],
                                 "subscriptionProducts": [{**product, "billingPeriod": "monthly"}]}})
    items = pancake.get_catalog(**credentials, store_id=STORE)
    assert [(item["billing_mode"], item["billing_period"]) for item in items] == [("one_time", None), ("subscription", "monthly")]
    assert items[0]["prices"] == [{"currency": "USD", "amount": "29.00", "tax_category": "digital_goods"}]
    assert json.loads(calls[1][2])["variables"] == {"storeId": STORE}
    _response(replies, {"store": {"id": "STO_other"}})
    with pytest.raises(pancake.PancakeVerificationError):
        pancake.get_catalog(**credentials, store_id=STORE)


def _order(**fields):
    return {"id": ORDER, "orderMerchantExternalId": REFERENCE, "testMode": True, "store": {"id": STORE}, **fields}


def _payment(**fields):
    return {
        "id": PAYMENT, "orderId": ORDER, "orderMerchantExternalId": REFERENCE, "status": "succeeded",
        "periodNumber": 2, "refunds": [], "amount": {"display": "29.00", "currency": "USD"},
        "snapshotAmountDetails": {"currency": "USD", "subtotal": "26.10", "taxAmount": "2.90", "total": "29.00"},
        **fields,
    }


def test_subscription_order_query_keeps_period_and_money(credentials, api):
    calls, replies = api
    payment = _payment()
    row = _order(subscriptionProduct={"id": PRODUCT}, currentPeriodNumber=2, currentPeriodStart=TIMESTAMP,
                 currentPeriodEnd=NEXT_MONTH, payments=[payment])
    _response(replies, {"subscriptionOrders": [row]})
    assert pancake.get_order(**credentials, store_id=STORE, order_no=REFERENCE, billing_mode="subscription") == row
    request = json.loads(calls[0][2])
    assert request["variables"] == {"storeId": STORE, "reference": REFERENCE}
    assert "currentPeriodStart currentPeriodEnd currentPeriodNumber" in request["query"]
    assert "amount { display currency }" in request["query"]


@pytest.mark.parametrize("rows", [
    [_order(), _order()], [_order(testMode=False)], [_order(store={"id": "STO_other"})],
    [_order(orderMerchantExternalId="other")], [_order(testMode="true")],
])
def test_order_lookup_rejects_ambiguity_or_scope_mismatch(rows, credentials, api):
    _, replies = api
    _response(replies, {"onetimeOrders": rows})
    with pytest.raises(pancake.PancakeVerificationError):
        pancake.get_order(**credentials, store_id=STORE, order_no=REFERENCE, billing_mode="one_time")


def test_payment_query_binds_mode_order_and_store_and_preserves_charged_amount(credentials, api):
    calls, replies = api
    row = _payment(subscriptionOrder=_order(), onetimeOrder=None, amount={"display": "24.50", "currency": "USD"})
    _response(replies, {"payment": row})
    assert pancake.get_payment(**credentials, store_id=STORE, order_no=REFERENCE, payment_id=PAYMENT) == {
        **row, "storeId": STORE, "testMode": True,
    }
    assert json.loads(calls[0][2])["variables"] == {"id": PAYMENT}
    _response(replies, {"payment": {**row, "orderId": "ORD_other"}})
    with pytest.raises(pancake.PancakeVerificationError):
        pancake.get_payment(**credentials, store_id=STORE, order_no=REFERENCE, payment_id=PAYMENT)


def test_refund_create_recovery_and_executed_result_are_separate(credentials, api):
    calls, replies = api
    created = {"id": TICKET, "subjectId": PAYMENT, "status": "processing", "refundTicketMerchantExternalId": REFUND}
    for _ in range(2):
        _response(replies, {"ticket": created})
        assert pancake.create_refund_ticket(**credentials, payment_id=PAYMENT, refund_no=REFUND,
                                            amount="29.00", currency="USD", reason="Requested") == created
    assert calls[0][1] == "/v1/actions/refund-ticket/create-ticket"
    assert calls[0][3]["X-Idempotency-Key"] == calls[1][3]["X-Idempotency-Key"]
    assert json.loads(calls[0][2])["requestedAmount"] == {"amount": "29.00", "currency": "USD"}
    ticket = {**created, "paymentId": PAYMENT, "versionData": {"requestedAmount": {"amount": "29.00", "currency": "USD"}}}
    _response(replies, {"refundTickets": [ticket]})
    assert pancake.get_refund_ticket(**credentials, payment_id=PAYMENT, refund_no=REFUND) == ticket
    _response(replies, {"refundTickets": [{**ticket, "paymentId": "PAY_other"}]})
    with pytest.raises(pancake.PancakeVerificationError):
        pancake.get_refund_ticket(**credentials, payment_id=PAYMENT, refund_no=REFUND)
    _response(replies, {"refunds": []})
    assert pancake.get_refund(**credentials, payment_id=PAYMENT, refund_no=REFUND, order_no=REFERENCE) is None
    result = {
        "id": "RFD_Aaa", "ticketId": TICKET, "paymentId": PAYMENT, "status": "succeeded",
        "refundTicketMerchantExternalId": REFUND, "orderMerchantExternalId": REFERENCE,
        "amount": {"display": "29.00", "currency": "USD"}, "pspAmountDetails": {"amount": "234.75", "currency": "HKD"},
    }
    _response(replies, {"refunds": [result]})
    assert pancake.get_refund(**credentials, payment_id=PAYMENT, refund_no=REFUND, order_no=REFERENCE) == result


@pytest.mark.parametrize("status", ["canceling", "canceled", "closed", "expired"])
def test_cancel_uses_documented_order_action(credentials, api, status):
    calls, replies = api
    data = {"orderId": ORDER, "status": status}
    _response(replies, data)
    assert pancake.cancel_subscription(**credentials, order_id=ORDER) == data
    assert calls[0][1] == "/v1/actions/subscription-order/cancel-order"
    assert json.loads(calls[0][2]) == {"orderId": ORDER}


@pytest.fixture
def webhook(keys, monkeypatch):
    monkeypatch.setattr(pancake.time, "time", lambda: NOW)
    monkeypatch.setattr(pancake, "_WEBHOOK_KEYS", {
        mode: key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        for mode, key in keys.items()
    })
    event = {
        "id": PAYMENT, "eventId": PAYMENT, "eventType": "subscription.payment_succeeded",
        "mode": "test", "timestamp": TIMESTAMP, "storeId": STORE, "data": {
            "currency": "USD", "orderId": ORDER, "orderMerchantExternalId": REFERENCE, "paymentId": PAYMENT,
            "periodNumber": 2, "chargedAmount": "29.00", "paymentStatus": "succeeded",
        },
    }

    def sign(body, *, signed_at=NOW * 1000, mode="test"):
        timestamp = str(signed_at)
        signature = keys[mode].sign(timestamp.encode() + b"." + body, padding.PKCS1v15(), hashes.SHA256())
        return f"t={timestamp},v1={base64.b64encode(signature).decode()}"

    return event, sign


def test_webhook_raw_signature_and_exact_scope(webhook):
    event, sign = webhook
    body = json.dumps(event, indent=2).encode()
    signature = sign(body, signed_at=(NOW - 31 * 60) * 1000)
    assert pancake.verify_webhook(body, signature, "test", STORE) == event
    with pytest.raises(pancake.PancakeVerificationError):
        pancake.verify_webhook(body + b"\n", signature, "test", STORE)
    with pytest.raises(pancake.PancakeVerificationError):
        pancake.verify_webhook(body, signature, "test", "STO_other")
    with pytest.raises(pancake.PancakeVerificationError):
        pancake.verify_webhook(body, signature, "production", STORE)
    production = json.dumps({**event, "mode": "prod"}).encode()
    assert pancake.verify_webhook(production, sign(production, mode="production"), "production", STORE)["mode"] == "prod"


@pytest.mark.parametrize("offset", [-45 * 60_000 - 1, 60_001])
def test_webhook_rejects_stale_or_premature_signatures(webhook, offset):
    event, sign = webhook
    body = json.dumps(event).encode()
    with pytest.raises(pancake.PancakeVerificationError):
        pancake.verify_webhook(body, sign(body, signed_at=NOW * 1000 + offset), "test", STORE)


def test_signed_duplicate_json_or_repeated_signature_fields_are_rejected(webhook):
    event, sign = webhook
    original = json.dumps(event).encode()
    duplicate = original[:-1] + b', "mode":"prod"}'
    with pytest.raises(pancake.PancakeVerificationError):
        pancake.verify_webhook(duplicate, sign(duplicate), "test", STORE)
    with pytest.raises(pancake.PancakeVerificationError):
        pancake.verify_webhook(original, sign(original) + f",t={NOW * 1000}", "test", STORE)
