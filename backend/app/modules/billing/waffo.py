"""Signed API and webhook calls for Waffo's enterprise acquiring platform."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import http.client
import json
import re
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


# Wire format pinned against waffo-go/v2 v2.3.0 and the public Waffo REST reference.
_HOSTS = {"test": "api-sandbox.waffo.com", "production": "api.waffo.com"}
_CHECKOUT_HOSTS = {
    "test": {"checkout.waffo.com", "cashier.waffo.com", "checkout-sandbox.waffo.com", "cashier-sandbox.waffo.com"},
    "production": {"checkout.waffo.com", "cashier.waffo.com"},
}
_INTERVALS = {"week": ("WEEKLY", "1"), "month": ("MONTHLY", "1"), "year": ("MONTHLY", "12")}
_TIMEOUT = 8
_BODY_LIMIT = 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_AMOUNT = re.compile(r"[0-9]+(?:\.[0-9]+)?\Z")
_EVENT_TYPE = re.compile(r"[A-Z][A-Z0-9_]{0,79}\Z")
_API_CODE = re.compile(r"[A-Z][0-9]{4}\Z")


class WaffoError(ValueError):
    pass


class WaffoConfigurationError(WaffoError):
    pass


class WaffoVerificationError(WaffoError):
    pass


class WaffoTransportError(WaffoError):
    pass


class WaffoAPIError(WaffoError):
    def __init__(self, status_code: int, code=None):
        super().__init__("Waffo did not confirm the requested operation")
        self.status_code = status_code
        self.code = code


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise WaffoConfigurationError("Invalid Waffo identifier")
    return value


def request_reference(order_no):
    """Derive a stable Waffo request id from an already-persisted JJTV reference."""
    return hashlib.sha256(_identifier(order_no).encode("ascii")).hexdigest()[:32]


def _is_merchant(value, merchant_id):
    return type(value) in (str, int) and str(value) == merchant_id


def _amount(value):
    if not isinstance(value, str) or len(value) > 24 or not _AMOUNT.fullmatch(value) or not value.strip("0."):
        raise WaffoConfigurationError("Invalid Waffo amount")
    return value


def _currency(value):
    if not isinstance(value, str) or not _CURRENCY.fullmatch(value):
        raise WaffoConfigurationError("Invalid Waffo currency")
    return value


def _timestamp(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, datetime) and value.utcoffset() is not None:
            return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (OverflowError, ValueError):
        pass
    raise WaffoConfigurationError("Invalid Waffo timestamp")


def _input_text(value, max_length):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise WaffoConfigurationError("Invalid Waffo text field")
    return value


def _key(value, *, private):
    try:
        if not isinstance(value, str) or len(value) > 16_384:
            raise ValueError
        material = value.strip().replace("\\r\\n", "\n").replace("\\n", "\n").encode("ascii")
        is_pem = material.startswith(b"-----BEGIN ")
        if not is_pem:
            material = base64.b64decode(b"".join(material.split()), validate=True)
        if private:
            loader = serialization.load_pem_private_key if is_pem else serialization.load_der_private_key
            key = loader(material, password=None)
            key_type = rsa.RSAPrivateKey
        else:
            loader = serialization.load_pem_public_key if is_pem else serialization.load_der_public_key
            key = loader(material)
            key_type = rsa.RSAPublicKey
        if not isinstance(key, key_type) or key.key_size < 2048:
            raise ValueError
        return key
    except (TypeError, ValueError, UnsupportedAlgorithm):
        kind = "private" if private else "public"
        raise WaffoConfigurationError(f"Invalid Waffo RSA {kind} key") from None


def _sign(body, key):
    return base64.b64encode(key.sign(body, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")


def _verify_signature(body, signature, key):
    try:
        if not isinstance(signature, str) or len(signature) > 2048:
            raise ValueError
        key.verify(base64.b64decode(signature, validate=True), body, padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, TypeError, ValueError):
        raise WaffoVerificationError("Invalid Waffo signature") from None


def _unique_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON key")
        result[name] = value
    return result


def _invalid_constant(_value):
    raise ValueError("Invalid JSON number")


def _parse_object(body):
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_pairs,
                           parse_float=Decimal, parse_constant=_invalid_constant)
        if isinstance(value, dict):
            return value
    except (AttributeError, ValueError, RecursionError):
        pass
    raise WaffoVerificationError("Invalid Waffo JSON response") from None


def _send(path, payload, *, merchant_id, api_key, private_key, public_key, mode):
    _identifier(merchant_id)
    if not isinstance(mode, str) or mode not in _HOSTS:
        raise WaffoConfigurationError("Invalid Waffo environment")
    if not isinstance(api_key, str) or not 1 <= len(api_key) <= 1024 or any(not 33 <= ord(c) <= 126 for c in api_key):
        raise WaffoConfigurationError("Invalid Waffo API key")
    signing_key = _key(private_key, private=True)
    verifying_key = _key(public_key, private=False)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json", "Accept": "application/json", "X-API-KEY": api_key,
        "X-API-VERSION": "1.0.0", "X-SIGNATURE": _sign(body, signing_key),
    }
    connection = http.client.HTTPSConnection(_HOSTS[mode], timeout=_TIMEOUT)
    try:
        connection.request("POST", "/api/v1" + path, body=body, headers=headers)
        response = connection.getresponse()
        status = response.status
        if 300 <= status < 400:
            raise WaffoAPIError(status)
        raw = response.read(_BODY_LIMIT + 1)
        if len(raw) > _BODY_LIMIT:
            raise WaffoTransportError("Waffo response exceeds the size limit")
        signature = response.getheader("X-SIGNATURE")
        if signature is not None:
            _verify_signature(raw, signature, verifying_key)
    except (OSError, http.client.HTTPException):
        raise WaffoTransportError("Waffo request failed") from None
    finally:
        connection.close()

    try:
        envelope = _parse_object(raw)
    except WaffoVerificationError:
        if not 200 <= status < 300:
            raise WaffoAPIError(status) from None
        raise
    code = envelope.get("code")
    if code == "E0001":
        raise WaffoTransportError("Waffo could not confirm the operation status")
    success = code == "0" or (type(code) is int and code == 0)
    if not 200 <= status < 300 or not success:
        raise WaffoAPIError(status, code if isinstance(code, str) and _API_CODE.fullmatch(code) else None)
    if not isinstance(envelope.get("data"), dict):
        raise WaffoVerificationError("Invalid Waffo response data")
    return envelope["data"]


def _inquiry(path, payload, credentials):
    try:
        return _send(path, payload, **credentials)
    except WaffoAPIError as error:
        if error.code == "A0009":
            return None
        raise


def verify_merchant(*, merchant_id, api_key, private_key, public_key, mode):
    try:
        data = _send("/merchantconfig/inquiry", {"merchantId": _identifier(merchant_id)}, merchant_id=merchant_id,
                     api_key=api_key, private_key=private_key, public_key=public_key, mode=mode)
    except WaffoAPIError as error:
        if error.status_code in (401, 403) or error.code in ("A0001", "A0002", "A0004", "A0006"):
            return False
        raise
    return _is_merchant(data.get("merchantId"), merchant_id)


def _url(value, max_length):
    if (not isinstance(value, str) or not value or len(value) > max_length or "\\" in value
            or any(ord(c) < 33 or c.isspace() for c in value)):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "https" and parsed.hostname and parsed.username is None
                and parsed.port in (None, 443)):
            return parsed
    except ValueError:
        pass
    return None


def _customer_checkout(merchant_id, customer_id, customer_email, product_name, notify_url, success_url, cancel_url):
    if not (_url(notify_url, 1024) and _url(success_url, 1024) and _url(cancel_url, 512)):
        raise WaffoConfigurationError("Invalid Waffo callback or return URL")
    customer = {"userId": _identifier(customer_id), "userTerminal": "WEB"}
    if customer_email:
        customer["userEmail"] = _input_text(customer_email, 254)
    return {
        "merchantInfo": {"merchantId": _identifier(merchant_id)}, "userInfo": customer,
        "goodsInfo": {"goodsName": _input_text(product_name, 128)}, "notifyUrl": notify_url,
        "successRedirectUrl": success_url, "failedRedirectUrl": cancel_url, "cancelRedirectUrl": cancel_url,
    }


def _checkout_url(value, mode, *, direct=False):
    candidate = value
    if isinstance(value, str) and len(value) <= 16_384 and value.lstrip().startswith("{"):
        candidate = _parse_object(value.encode("utf-8")).get("webUrl")
    elif not direct:
        candidate = None
    parsed = _url(candidate, 8192)
    if not parsed or parsed.hostname not in _CHECKOUT_HOSTS[mode] or parsed.path in ("", "/"):
        raise WaffoVerificationError("Waffo returned an invalid hosted checkout URL")
    return candidate


def _check_result(data, *, request_field, reference, merchant_field, order_no, provider_field,
                  provider_id=None, merchant_id=None):
    returned_id = data.get(provider_field)
    merchant = data.get("merchantInfo")
    if (not isinstance(returned_id, str) or not _ID.fullmatch(returned_id)
            or (provider_id is not None and returned_id != provider_id)
            or (reference is not None and data.get(request_field) != reference)
            or (order_no is not None and data.get(merchant_field) != order_no)
            or (merchant_id is not None and (not isinstance(merchant, dict)
                                             or not _is_merchant(merchant.get("merchantId"), merchant_id)))):
        raise WaffoVerificationError("Waffo response does not match the requested merchant and reference")


def create_checkout(*, order_no, amount, currency, requested_at, customer_id, customer_email, product_name,
                    notify_url, success_url, cancel_url, **credentials):
    reference = request_reference(order_no)
    payload = _customer_checkout(credentials["merchant_id"], customer_id, customer_email, product_name,
                                 notify_url, success_url, cancel_url)
    payload.update({
        "paymentRequestId": reference, "merchantOrderId": order_no,
        "orderAmount": _amount(amount), "orderCurrency": _currency(currency),
        "orderDescription": product_name, "orderRequestedAt": _timestamp(requested_at),
        "paymentInfo": {"productName": "ONE_TIME_PAYMENT"},
    })
    data = _send("/order/create", payload, **credentials)
    _check_result(data, request_field="paymentRequestId", reference=reference, merchant_field="merchantOrderId",
                  order_no=order_no, provider_field="acquiringOrderId")
    return {**data, "checkout_url": _checkout_url(data.get("orderAction"), credentials["mode"])}


def get_order(*, order_no=None, provider_order_id=None, **credentials):
    reference = request_reference(order_no) if order_no is not None else None
    if provider_order_id is not None:
        payload = {"acquiringOrderId": _identifier(provider_order_id)}
    elif reference is not None:
        payload = {"paymentRequestId": reference}
    else:
        raise WaffoConfigurationError("Waffo order identifier is required")
    data = _inquiry("/order/inquiry", payload, credentials)
    if data is not None:
        _check_result(data, request_field="paymentRequestId", reference=reference, merchant_field="merchantOrderId",
                      order_no=order_no, provider_field="acquiringOrderId", provider_id=provider_order_id,
                      merchant_id=credentials["merchant_id"])
    return data


def create_subscription(*, order_no, amount, currency, requested_at, customer_id, customer_email, product_name,
                        notify_url, success_url, cancel_url, interval, management_url=None, **credentials):
    reference = request_reference(order_no)
    management_url = cancel_url if management_url is None else management_url
    if not isinstance(interval, str) or interval not in _INTERVALS or not _url(management_url, 256):
        raise WaffoConfigurationError("Invalid Waffo subscription interval or management URL")
    period_type, period_interval = _INTERVALS[interval]
    payload = _customer_checkout(credentials["merchant_id"], customer_id, customer_email, product_name,
                                 notify_url, success_url, cancel_url)
    payload.update({
        "subscriptionRequest": reference, "merchantSubscriptionId": order_no,
        "amount": _amount(amount), "currency": _currency(currency), "requestedAt": _timestamp(requested_at),
        "productInfo": {"description": product_name, "periodType": period_type, "periodInterval": period_interval},
        "paymentInfo": {"productName": "SUBSCRIPTION"}, "subscriptionManagementUrl": management_url,
    })
    data = _send("/subscription/create", payload, **credentials)
    _check_result(data, request_field="subscriptionRequest", reference=reference, merchant_field="merchantSubscriptionId",
                  order_no=order_no, provider_field="subscriptionId")
    return {**data, "checkout_url": _checkout_url(data.get("subscriptionAction"), credentials["mode"], direct=True)}


def get_subscription(*, order_no=None, provider_subscription_id=None, **credentials):
    reference = request_reference(order_no) if order_no is not None else None
    if provider_subscription_id is not None:
        payload = {"subscriptionId": _identifier(provider_subscription_id)}
    elif reference is not None:
        payload = {"subscriptionRequest": reference}
    else:
        raise WaffoConfigurationError("Waffo subscription identifier is required")
    data = _inquiry("/subscription/inquiry", {**payload, "paymentDetails": 1}, credentials)
    if data is not None:
        _check_result(data, request_field="subscriptionRequest", reference=reference, merchant_field="merchantSubscriptionId",
                      order_no=order_no, provider_field="subscriptionId", provider_id=provider_subscription_id,
                      merchant_id=credentials["merchant_id"])
    return data


def cancel_subscription(*, provider_subscription_id, requested_at, scheduled_cancel_at=None, **credentials):
    payload = {
        "subscriptionId": _identifier(provider_subscription_id), "merchantId": credentials["merchant_id"],
        "requestedAt": _timestamp(requested_at),
    }
    if scheduled_cancel_at is not None:
        payload["scheduledCancelAt"] = _timestamp(scheduled_cancel_at)
    data = _send("/subscription/cancel", payload, **credentials)
    if data.get("subscriptionId") != provider_subscription_id:
        raise WaffoVerificationError("Waffo cancellation does not match the requested subscription")
    return data


def _check_refund(data, order_no, provider_order_id, refund_no, provider_refund_id=None):
    _check_result(data, request_field="refundRequestId", reference=request_reference(refund_no),
                  merchant_field="merchantRefundOrderId", order_no=refund_no,
                  provider_field="acquiringRefundOrderId", provider_id=provider_refund_id)
    if (data.get("acquiringOrderId") != provider_order_id
            or (data.get("origPaymentRequestId") is not None
                and data["origPaymentRequestId"] != request_reference(order_no))):
        raise WaffoVerificationError("Waffo refund does not match the original order")


def create_refund(*, order_no, provider_order_id, refund_no, amount, reason, notify_url, requested_at, **credentials):
    _identifier(order_no)
    if not _url(notify_url, 256):
        raise WaffoConfigurationError("Invalid Waffo refund callback URL")
    data = _send("/order/refund", {
        "refundRequestId": request_reference(refund_no), "merchantRefundOrderId": refund_no,
        "acquiringOrderId": _identifier(provider_order_id), "merchantId": credentials["merchant_id"],
        "refundAmount": _amount(amount), "refundReason": _input_text(reason, 256),
        "refundNotifyUrl": notify_url, "requestedAt": _timestamp(requested_at),
    }, **credentials)
    _check_refund(data, order_no, provider_order_id, refund_no)
    return data


def get_refund(*, order_no, provider_order_id, refund_no, provider_refund_id=None, **credentials):
    _identifier(order_no)
    _identifier(provider_order_id)
    payload = {"refundRequestId": request_reference(refund_no)}
    if provider_refund_id is not None:
        payload["acquiringRefundOrderId"] = _identifier(provider_refund_id)
    data = _inquiry("/refund/inquiry", payload, credentials)
    if data is not None:
        _check_refund(data, order_no, provider_order_id, refund_no, provider_refund_id)
    return data


def verify_webhook(body, signature, public_key):
    if not isinstance(body, bytes) or not body or len(body) > _BODY_LIMIT:
        raise WaffoVerificationError("Invalid Waffo webhook body")
    _verify_signature(body, signature, _key(public_key, private=False))
    event = _parse_object(body)
    kind = event.get("eventType")
    if not isinstance(kind, str) or not _EVENT_TYPE.fullmatch(kind) or not isinstance(event.get("result"), dict):
        raise WaffoVerificationError("Invalid Waffo webhook event")
    return event


def acknowledgment(private_key):
    body = b'{"message":"success"}'
    return body, _sign(body, _key(private_key, private=True))
