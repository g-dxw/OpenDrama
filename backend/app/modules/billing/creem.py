"""Server-side client for Creem hosted checkout and payment webhooks."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import re
from urllib.parse import urlencode, urlsplit


_API_HOSTS = {"test": "test-api.creem.io", "production": "api.creem.io"}
_RESPONSE_MODES = {"test": {"test", "sandbox"}, "production": {"prod"}}
_CHECKOUT_HOST = "checkout.creem.io"
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_EVENT_TYPE = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_API_KEY = re.compile(r"[!-~]{1,512}\Z")
_SIGNATURE = re.compile(r"[A-Fa-f0-9]{64}\Z")
_TIMEOUT = 8.0
_BODY_LIMIT = 1024 * 1024


class CreemError(ValueError):
    pass


class CreemConfigurationError(CreemError):
    pass


class CreemTransportError(CreemError):
    pass


class CreemVerificationError(CreemError):
    pass


class CreemHTTPError(CreemError):
    def __init__(self, status_code: int):
        super().__init__("Creem API returned an unsuccessful response")
        self.status_code = status_code


def mode_matches(mode: str, remote_mode: object) -> bool:
    return isinstance(remote_mode, str) and remote_mode in _RESPONSE_MODES.get(mode, ())


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_json_constant(_value):
    raise ValueError("Invalid JSON number")


def _parse_object(body: bytes) -> dict:
    value = json.loads(body, object_pairs_hook=_unique_object, parse_constant=_invalid_json_constant)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _request(*, api_key: str, mode: str, method: str, path: str, params=None, payload=None) -> dict:
    host = _API_HOSTS.get(mode)
    if host is None or not isinstance(api_key, str) or not _API_KEY.fullmatch(api_key):
        raise CreemConfigurationError("Invalid Creem API credentials or environment")
    target = f"/v1{path}"
    if params:
        target += f"?{urlencode(params)}"
    headers = {"Accept": "application/json", "x-api-key": api_key}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    connection = http.client.HTTPSConnection(host, timeout=_TIMEOUT)
    try:
        connection.request(method, target, body=body, headers=headers)
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise CreemHTTPError(response.status)
        data = response.read(_BODY_LIMIT + 1)
        if len(data) > _BODY_LIMIT:
            raise CreemTransportError("Creem API response exceeds the size limit")
        return _parse_object(data)
    except CreemError:
        raise
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        raise CreemTransportError("Creem API request failed") from None
    finally:
        connection.close()


def _reference(value: object) -> str:
    if not isinstance(value, str) or not _REFERENCE.fullmatch(value):
        raise CreemConfigurationError("Invalid Creem reference")
    return value


def _entity(data: dict, mode: str, reference: str | None = None) -> dict:
    resource_id = data.get("id")
    if not isinstance(resource_id, str) or not _REFERENCE.fullmatch(resource_id):
        raise CreemVerificationError("Creem returned an invalid resource identifier")
    if not mode_matches(mode, data.get("mode")) or (reference is not None and resource_id != reference):
        raise CreemVerificationError("Creem response does not match the resource and environment requested")
    return data


def _redirect_parts(value: object):
    if not isinstance(value, str) or "\\" in value or any(character.isspace() or ord(character) < 32 for character in value):
        return None
    try:
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.username is not None:
            return None
        parsed.port  # Validate the port syntax before returning the parsed location.
        return parsed
    except ValueError:
        return None


def _check_checkout_url(value: object) -> None:
    parsed = _redirect_parts(value)
    if (
        parsed is None or parsed.scheme != "https" or parsed.hostname != _CHECKOUT_HOST
        or parsed.port not in (None, 443) or parsed.path in ("", "/")
    ):
        raise CreemVerificationError("Creem checkout URL is outside the hosted checkout origin")


def verify_merchant(*, api_key: str, mode: str) -> bool:
    try:
        products = _request(
            api_key=api_key, mode=mode, method="GET", path="/products/search",
            params={"page_number": 1, "page_size": 1},
        )
    except CreemHTTPError as exc:
        if exc.status_code in (401, 403):
            return False
        raise
    items = products.get("items")
    if not isinstance(items, list):
        raise CreemVerificationError("Creem returned an invalid product listing")
    for item in items:
        if not isinstance(item, dict):
            raise CreemVerificationError("Creem returned an invalid product listing")
        _entity(item, mode)
    return True


def get_product(*, api_key: str, mode: str, product_id: str) -> dict:
    reference = _reference(product_id)
    data = _request(api_key=api_key, mode=mode, method="GET", path=f"/products/{reference}")
    return _entity(data, mode, reference)


def create_checkout(
    *, api_key: str, mode: str, product_id: str, order_no: str, success_url: str,
    customer_email: str | None = None,
) -> dict:
    product_ref = _reference(product_id)
    order_ref = _reference(order_no)
    parsed = _redirect_parts(success_url)
    if parsed is None or parsed.scheme not in {"http", "https"}:
        raise CreemConfigurationError("Invalid Creem payment return URL")
    payload = {
        "product_id": product_ref,
        "request_id": order_ref,
        "units": 1,
        "success_url": success_url,
        "metadata": {"jjtv_order_no": order_ref},
    }
    if customer_email:
        payload["customer"] = {"email": customer_email}
    data = _request(api_key=api_key, mode=mode, method="POST", path="/checkouts", payload=payload)
    _entity(data, mode)
    _check_checkout_url(data.get("checkout_url"))
    product = data.get("product", data.get("product_id"))
    returned_product = product.get("id") if isinstance(product, dict) else product
    if returned_product != product_ref or data.get("request_id", order_ref) != order_ref:
        raise CreemVerificationError("Creem checkout does not match the requested order or product")
    return data


def get_checkout(*, api_key: str, mode: str, checkout_id: str) -> dict:
    reference = _reference(checkout_id)
    data = _request(
        api_key=api_key, mode=mode, method="GET", path="/checkouts", params={"checkout_id": reference},
    )
    _entity(data, mode, reference)
    if data.get("checkout_url"):
        _check_checkout_url(data["checkout_url"])
    return data


def get_subscription(*, api_key: str, mode: str, subscription_id: str) -> dict:
    reference = _reference(subscription_id)
    data = _request(
        api_key=api_key, mode=mode, method="GET", path="/subscriptions", params={"subscription_id": reference},
    )
    return _entity(data, mode, reference)


def get_transaction(*, api_key: str, mode: str, transaction_id: str) -> dict:
    reference = _reference(transaction_id)
    data = _request(
        api_key=api_key, mode=mode, method="GET", path="/transactions", params={"transaction_id": reference},
    )
    return _entity(data, mode, reference)


def cancel_subscription(*, api_key: str, mode: str, subscription_id: str, immediate: bool = False) -> dict:
    reference = _reference(subscription_id)
    data = _request(
        api_key=api_key, mode=mode, method="POST", path=f"/subscriptions/{reference}/cancel",
        payload={"mode": "immediate" if immediate else "scheduled", "onExecute": "cancel"},
    )
    return _entity(data, mode, reference)


def refund_transaction(*, api_key: str, mode: str, transaction_id: str) -> str:
    reference = _reference(transaction_id)
    data = _request(api_key=api_key, mode=mode, method="POST", path="/refunds", payload={"transaction_id": reference})
    status = data.get("status")
    if status not in {"pending", "requiresAction", "succeeded", "failed", "canceled"}:
        raise CreemVerificationError("Creem returned an unknown refund status")
    return status


def verify_webhook(body: bytes, signature: str, secret: str) -> dict:
    if (
        not isinstance(body, bytes) or not body or len(body) > _BODY_LIMIT
        or not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature)
        or not isinstance(secret, str) or not secret
    ):
        raise CreemVerificationError("Invalid Creem webhook signature or body")
    digest = hmac.digest(secret.encode("utf-8"), body, hashlib.sha256)
    if not hmac.compare_digest(digest, bytes.fromhex(signature)):
        raise CreemVerificationError("Invalid Creem webhook signature")
    try:
        event = _parse_object(body)
    except (ValueError, RecursionError):
        raise CreemVerificationError("Invalid Creem webhook JSON") from None
    event_id = event.get("id")
    event_type = event.get("eventType")
    if (
        not isinstance(event_id, str) or not _REFERENCE.fullmatch(event_id)
        or not isinstance(event_type, str) or not _EVENT_TYPE.fullmatch(event_type)
        or not isinstance(event.get("object"), dict)
    ):
        raise CreemVerificationError("Invalid Creem webhook event")
    return event
