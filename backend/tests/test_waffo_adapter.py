import base64
from collections import deque
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
import io
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import pytest

from app.modules.billing import waffo


MERCHANT_ID = "1000000201"
ORDER_NO = "JJTV-ORDER-5620261120"
REMOTE_ORDER = "ACQ001"
REMOTE_SUBSCRIPTION = "WAFFOSUB0001"
REFUND_NO = "JJTV-REF-1120"
REMOTE_REFUND = "REFWAFFO001"
REQUEST_AT = datetime(2026, 11, 20, 0, 1, 12, 120000, UTC)
CHECKOUT_URL = "https://cashier.waffo.com/order/acq-1?lang=zh"
NOTIFY_URL = "https://billing.example.test/v1/callback/waffo/merchant"
RETURN_URL = "https://play.example.test/profile/orders?id=JJTV-ORDER-5620261120"
MANAGEMENT_URL = "https://play.example.test/membership"


@pytest.fixture(scope="module")
def keys():
    return [rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2)]


def _public_key(key):
    return base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )).decode()


@pytest.fixture
def credentials(keys):
    merchant_key, provider_key = keys
    return {
        "merchant_id": MERCHANT_ID, "api_key": "secret-api-key",
        "private_key": merchant_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()).decode(),
        "public_key": _public_key(provider_key), "mode": "test",
    }


def _signature(body, key):
    return base64.b64encode(key.sign(body, padding.PKCS1v15(), hashes.SHA256())).decode()


@pytest.fixture
def http(monkeypatch, keys):
    calls, queued = [], deque()

    class Response(io.BytesIO):
        def __init__(self, status, body, signature):
            super().__init__(body)
            self.status = status
            self.signature = signature

        def getheader(self, name):
            assert name == "X-SIGNATURE"
            return self.signature

    class Connection:
        def __init__(self, host, timeout):
            self.host = host
            assert 0 < timeout <= 10

        def request(self, method, path, body, headers):
            calls.append((self.host, method, path, body, headers))

        def getresponse(self):
            reply = queued.popleft()
            if isinstance(reply, Exception):
                raise reply
            return reply

        def close(self):
            pass

    def reply(data=None, *, status=200, code="0", body=None, signed=True, wrong_signature=False):
        if body is None:
            body = json.dumps({"code": code, "data": data, "msg": "secret-remote-response"}, indent=2).encode()
        proof = _signature(body, keys[0] if wrong_signature else keys[1]) if signed else None
        queued.append(Response(status, body, proof))

    monkeypatch.setattr(waffo.http.client, "HTTPSConnection", Connection)
    return calls, queued, reply


def _checkout_args():
    return dict(order_no=ORDER_NO, amount="29.00", currency="USD", requested_at=REQUEST_AT,
                customer_id="user-123", customer_email="buyer@example.test", product_name="会员月卡",
                notify_url=NOTIFY_URL, success_url=RETURN_URL, cancel_url=RETURN_URL)


def _created_order(**fields):
    return {
        "paymentRequestId": waffo.request_reference(ORDER_NO), "merchantOrderId": ORDER_NO,
        "acquiringOrderId": REMOTE_ORDER, "orderStatus": "AUTHORIZATION_REQUIRED",
        "orderAction": json.dumps({"actionType": "REDIRECT", "webUrl": CHECKOUT_URL}),
        **fields,
    }


def _paid_order(**fields):
    return {
        **_created_order(orderStatus="PAY_SUCCESS"), "merchantInfo": {"merchantId": MERCHANT_ID},
        "orderAmount": "29.00", "orderCurrency": "USD", "userCurrency": "HKD", "finalDealAmount": "228.89",
        **fields,
    }


def test_checkout_uses_stable_business_refs_and_signs_exact_utf8(keys, credentials, http):
    calls, _, reply = http
    reply(_created_order())
    reply(_created_order(), code=0)
    for _ in range(2):
        result = waffo.create_checkout(**credentials, **_checkout_args())
        assert result["checkout_url"] == CHECKOUT_URL
        assert result["paymentRequestId"] == waffo.request_reference(ORDER_NO)
        assert result["acquiringOrderId"] == REMOTE_ORDER
    host, method, path, body, headers = calls[0]
    assert (host, method, path) == ("api-sandbox.waffo.com", "POST", "/api/v1/order/create")
    assert headers["X-API-KEY"] == credentials["api_key"]
    assert headers["X-API-VERSION"] == "1.0.0"
    keys[0].public_key().verify(base64.b64decode(headers["X-SIGNATURE"]), body,
                                padding.PKCS1v15(), hashes.SHA256())
    assert "会员月卡".encode() in body
    submitted = json.loads(body)
    assert submitted["paymentRequestId"] == waffo.request_reference(ORDER_NO)
    assert 0 < len(submitted["paymentRequestId"]) <= 32
    assert submitted["merchantOrderId"] == ORDER_NO
    assert submitted["orderRequestedAt"] == "2026-11-20T00:01:12.120Z"
    assert submitted["orderAmount"] == "29.00"
    assert submitted["orderCurrency"] == "USD"
    assert submitted["paymentInfo"] == {"productName": "ONE_TIME_PAYMENT"}
    assert submitted["merchantInfo"] == {"merchantId": MERCHANT_ID}
    assert submitted["userInfo"] == {"userId": "user-123", "userEmail": "buyer@example.test", "userTerminal": "WEB"}
    assert submitted["notifyUrl"] == NOTIFY_URL
    assert calls[1][3] == body


@pytest.mark.parametrize("url", [
    "http://cashier.waffo.com/order/1", "https://cashier.waffo.com.redirect.invalid/order/1",
    "https://fake@cashier.waffo.com/order/1", "https://checkout.waffo.com:8080/order/1",
    "https://checkout.waffo.com", "https://checkout-sandbox.waffo.com/order/1", "weixin://pay/1",
])
def test_live_hosted_checkout_rejects_other_destinations(url, credentials, http):
    _, _, reply = http
    reply(_created_order(orderAction=json.dumps({"webUrl": url})))
    live = {**credentials, "mode": "production"}
    with pytest.raises(waffo.WaffoVerificationError):
        waffo.create_checkout(**live, **_checkout_args())


def test_merchant_verification_rejects_mismatch_and_reports_auth_failure(credentials, http):
    calls, _, reply = http
    live = {**credentials, "mode": "production"}
    reply({"merchantId": int(MERCHANT_ID)})
    assert waffo.verify_merchant(**live) is True
    assert calls[0][:3] == ("api.waffo.com", "POST", "/api/v1/merchantconfig/inquiry")
    assert json.loads(calls[0][3]) == {"merchantId": MERCHANT_ID}
    reply({"merchantId": "different"})
    assert waffo.verify_merchant(**credentials) is False
    reply(status=401, code="A0001", signed=False)
    assert waffo.verify_merchant(**credentials) is False


def test_merchant_and_webhook_keys_accept_der_and_escaped_pem(keys, credentials, http):
    _, _, reply = http
    private_der = keys[0].private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
    public_pem = keys[1].public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    params = {**credentials, "private_key": base64.b64encode(private_der).decode(),
              "public_key": public_pem.replace("\n", "\\n")}
    reply({"merchantId": MERCHANT_ID}, signed=False)
    assert waffo.verify_merchant(**params)


@pytest.mark.parametrize("changes", [
    {"mode": "sandbox"}, {"api_key": "secret-api-key\r\nextra"},
    {"private_key": "secret-merchant-key"}, {"public_key": "secret-public-key"},
])
def test_invalid_credentials_fail_before_io_without_disclosure(changes, credentials, http):
    calls, _, _ = http
    with pytest.raises(waffo.WaffoConfigurationError) as exc:
        waffo.verify_merchant(**{**credentials, **changes})
    assert "secret-" not in str(exc.value)
    assert not calls


def test_order_inquiry_scope_and_absence(credentials, http):
    calls, _, reply = http
    reply(_paid_order())
    assert waffo.get_order(**credentials, order_no=ORDER_NO) == _paid_order()
    assert json.loads(calls[0][3]) == {"paymentRequestId": waffo.request_reference(ORDER_NO)}
    reply(_paid_order())
    assert waffo.get_order(**credentials, provider_order_id=REMOTE_ORDER, order_no=ORDER_NO) == _paid_order()
    assert json.loads(calls[1][3]) == {"acquiringOrderId": REMOTE_ORDER}
    reply(code="A0009", status=400)
    assert waffo.get_order(**credentials, order_no=ORDER_NO) is None


@pytest.mark.parametrize("changes", [
    {"merchantInfo": {"merchantId": "different"}}, {"merchantOrderId": "different"},
    {"paymentRequestId": "different"}, {"acquiringOrderId": "different"},
])
def test_order_inquiry_refuses_cross_scope_result(changes, credentials, http):
    _, _, reply = http
    reply(_paid_order(**changes))
    with pytest.raises(waffo.WaffoVerificationError):
        waffo.get_order(**credentials, order_no=ORDER_NO, provider_order_id=REMOTE_ORDER)


def test_uncertain_transport_and_wrong_signature_do_not_retry_or_expose_values(credentials, http):
    calls, queued, reply = http
    queued.append(TimeoutError("secret-socket-debug"))
    with pytest.raises(waffo.WaffoTransportError) as exc:
        waffo.create_checkout(**credentials, **_checkout_args())
    assert "secret-" not in str(exc.value)
    assert len(calls) == 1
    reply(code="E0001")
    with pytest.raises(waffo.WaffoTransportError):
        waffo.create_checkout(**credentials, **_checkout_args())
    reply(_created_order(), wrong_signature=True)
    with pytest.raises(waffo.WaffoVerificationError):
        waffo.create_checkout(**credentials, **_checkout_args())
    reply(status=302, signed=False)
    with pytest.raises(waffo.WaffoAPIError) as exc:
        waffo.create_checkout(**credentials, **_checkout_args())
    assert exc.value.status_code == 302
    assert len(calls) == 4


@pytest.mark.parametrize("status, body", [
    (200, b'{"code":"0","code":"A0001","data":{}}'), (200, b'{"code":"0","data":{"field":NaN}}'),
    (200, b'{"code":"0","data":{"x":"\xff"}}'), (200, b'invalid' * 160_000),
])
def test_strict_and_bounded_response_parsing(status, body, credentials, http):
    calls, _, reply = http
    reply(status=status, body=body)
    with pytest.raises((waffo.WaffoVerificationError, waffo.WaffoTransportError)):
        waffo.verify_merchant(**credentials)
    assert len(calls) == 1


def test_malformed_code_is_never_success(credentials, http):
    _, _, reply = http
    reply({"merchantId": MERCHANT_ID}, code=False)
    with pytest.raises(waffo.WaffoAPIError):
        waffo.verify_merchant(**credentials)


def _created_subscription(**fields):
    return {
        "subscriptionRequest": waffo.request_reference(ORDER_NO), "merchantSubscriptionId": ORDER_NO,
        "subscriptionId": REMOTE_SUBSCRIPTION, "subscriptionStatus": "AUTHORIZATION_REQUIRED",
        "subscriptionAction": CHECKOUT_URL,
        **fields,
    }


@pytest.mark.parametrize("interval, unit, increment", [
    ("week", "WEEKLY", "1"), ("month", "MONTHLY", "1"), ("year", "MONTHLY", "12"),
])
def test_subscription_checkout_freezes_no_trial_cadence_and_validates_url(interval, unit, increment, credentials, http):
    calls, _, reply = http
    reply(_created_subscription(subscriptionAction=json.dumps({"webUrl": CHECKOUT_URL})))
    result = waffo.create_subscription(**credentials, **_checkout_args(), interval=interval, management_url=MANAGEMENT_URL)
    assert result["checkout_url"] == CHECKOUT_URL
    assert result["subscriptionId"] == REMOTE_SUBSCRIPTION
    assert calls[0][2] == "/api/v1/subscription/create"
    obj = json.loads(calls[0][3])
    assert obj["productInfo"] == {"description": "会员月卡", "periodType": unit, "periodInterval": increment}
    assert obj["subscriptionRequest"] == waffo.request_reference(ORDER_NO)
    assert obj["subscriptionManagementUrl"] == MANAGEMENT_URL
    assert obj["paymentInfo"] == {"productName": "SUBSCRIPTION"}


def test_subscription_inquiry_requires_matching_remote_and_returns_completed_charge_history(credentials, http):
    calls, _, reply = http
    status = _created_subscription(subscriptionStatus="ACTIVE", productInfo={"currentPeriod": "2"},
                                   merchantInfo={"merchantId": MERCHANT_ID}, paymentDetails=[{
                                       "acquiringOrderId": REMOTE_ORDER, "orderStatus": "PAY_SUCCESS", "period": "2",
                                       "orderAmount": "29.00", "orderCurrency": "USD",
                                   }])
    reply(status)
    assert waffo.get_subscription(**credentials, order_no=ORDER_NO, provider_subscription_id=REMOTE_SUBSCRIPTION) == status
    assert json.loads(calls[0][3]) == {"subscriptionId": REMOTE_SUBSCRIPTION, "paymentDetails": 1}
    reply({**status, "subscriptionRequest": "different"})
    with pytest.raises(waffo.WaffoVerificationError):
        waffo.get_subscription(**credentials, order_no=ORDER_NO, provider_subscription_id=REMOTE_SUBSCRIPTION)


def test_subscription_cancellation_preserves_documented_time_field(credentials, http):
    calls, _, reply = http
    remote = {"subscriptionId": REMOTE_SUBSCRIPTION, "orderStatus": "ACTIVE", "scheduledCancelAt": "2026-12-01T00:01:00Z"}
    reply(remote)
    when = datetime(2026, 12, 1, 8, 1, tzinfo=timezone(timedelta(hours=8)))
    assert waffo.cancel_subscription(**credentials, provider_subscription_id=REMOTE_SUBSCRIPTION,
                                     requested_at=REQUEST_AT, scheduled_cancel_at=when) == remote
    assert calls[0][2] == "/api/v1/subscription/cancel"
    assert json.loads(calls[0][3])["scheduledCancelAt"] == "2026-12-01T00:01:00.000Z"
    reply({**remote, "subscriptionId": "different"})
    with pytest.raises(waffo.WaffoVerificationError):
        waffo.cancel_subscription(**credentials, provider_subscription_id=REMOTE_SUBSCRIPTION, requested_at=REQUEST_AT)


def _refund(**fields):
    return {
        "refundRequestId": waffo.request_reference(REFUND_NO), "merchantRefundOrderId": REFUND_NO,
        "acquiringRefundOrderId": REMOTE_REFUND, "acquiringOrderId": REMOTE_ORDER,
        "refundAmount": "29.00", "refundStatus": "REFUND_IN_PROGRESS", **fields,
    }


def test_refund_submit_then_inquiry_verifies_amount_reference_and_payment(credentials, http):
    calls, _, reply = http
    for _ in range(2):
        reply(_refund())
        result = waffo.create_refund(**credentials, order_no=ORDER_NO, provider_order_id=REMOTE_ORDER,
                                     refund_no=REFUND_NO, amount="29.00", reason="Customer request",
                                     notify_url=NOTIFY_URL, requested_at=REQUEST_AT)
        assert result["refundStatus"] == "REFUND_IN_PROGRESS"
    assert calls[0][2] == "/api/v1/order/refund"
    assert calls[0][3] == calls[1][3]
    submitted = json.loads(calls[0][3])
    assert submitted["refundNotifyUrl"] == NOTIFY_URL
    assert submitted["refundAmount"] == "29.00"
    assert submitted["refundRequestId"] != waffo.request_reference(ORDER_NO)
    terminal = _refund(origPaymentRequestId=waffo.request_reference(ORDER_NO), refundStatus="ORDER_FULLY_REFUNDED")
    reply(terminal)
    assert waffo.get_refund(**credentials, order_no=ORDER_NO, provider_order_id=REMOTE_ORDER,
                            refund_no=REFUND_NO, provider_refund_id=REMOTE_REFUND) == terminal
    assert calls[2][2] == "/api/v1/refund/inquiry"
    reply({**terminal, "origPaymentRequestId": "wrong"})
    with pytest.raises(waffo.WaffoVerificationError):
        waffo.get_refund(**credentials, order_no=ORDER_NO, provider_order_id=REMOTE_ORDER, refund_no=REFUND_NO)


def test_exact_signed_webhook_and_acknowledgment(credentials, keys):
    merchant_key, provider_key = keys
    body = b'{ "eventType": "PAYMENT_NOTIFICATION", "result": { "orderAmount": 29.00 } }'
    signature = _signature(body, provider_key)
    verified = waffo.verify_webhook(body, signature, credentials["public_key"])
    assert verified == {"eventType": "PAYMENT_NOTIFICATION", "result": {"orderAmount": Decimal("29.00")}}
    with pytest.raises(waffo.WaffoVerificationError):
        waffo.verify_webhook(body + b"\n", signature, credentials["public_key"])
    with pytest.raises(waffo.WaffoVerificationError):
        waffo.verify_webhook(body, signature, _public_key(merchant_key))
    ack, ack_signature = waffo.acknowledgment(credentials["private_key"])
    assert ack == b'{"message":"success"}'
    merchant_key.public_key().verify(base64.b64decode(ack_signature), ack, padding.PKCS1v15(), hashes.SHA256())


@pytest.mark.parametrize("body", [
    b'{"eventType":"PAYMENT_NOTIFICATION","result":{"orderId":"a","orderId":"b"}}',
    b'{"eventType":"PAYMENT_NOTIFICATION","result":{"orderId":"\xff"}}',
    b'{"eventType":"PAYMENT_NOTIFICATION","result":[]}',
])
def test_valid_signatures_cannot_bypass_webhook_json_validation(body, credentials, keys):
    with pytest.raises(waffo.WaffoVerificationError):
        waffo.verify_webhook(body, _signature(body, keys[1]), credentials["public_key"])
