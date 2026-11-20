import hmac
import json
from collections import deque

import pytest

from app.modules.billing import creem


KEY = "test-creem-api-key"
SECRET = "test-creem-webhook-secret"


@pytest.fixture
def gateway(monkeypatch):
    replies = deque()
    requests = []

    class Response:
        def __init__(self, status, payload):
            self.status = status
            self.payload = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload

        def read(self, size):
            return self.payload[:size]

    class Connection:
        def __init__(self, host, *, timeout):
            self.host = host
            assert 0 < timeout <= 10

        def request(self, method, target, *, body, headers):
            requests.append((self.host, method, target, json.loads(body) if body else None, headers))

        def getresponse(self):
            status, payload = replies.popleft()
            if isinstance(payload, OSError):
                raise payload
            return Response(status, payload)

        def close(self):
            pass

    monkeypatch.setattr(creem.http.client, "HTTPSConnection", Connection)
    return replies, requests


def test_merchant_probe_is_read_only_and_handles_an_empty_store(gateway):
    replies, requests = gateway
    replies.extend([(200, {"items": [], "pagination": {}}), (403, {"error": f"invalid {KEY}"})])
    assert creem.verify_merchant(api_key=KEY, mode="test") is True
    assert creem.verify_merchant(api_key=KEY, mode="production") is False
    assert requests == [
        (host, "GET", "/v1/products/search?page_number=1&page_size=1", None, {"Accept": "application/json", "x-api-key": KEY})
        for host in ("test-api.creem.io", "api.creem.io")
    ]


@pytest.mark.parametrize(
    ("operation", "field", "reference", "url"),
    [
        (creem.get_product, "product_id", "prod_1", "/v1/products/prod_1"),
        (creem.get_checkout, "checkout_id", "ch_1", "/v1/checkouts?checkout_id=ch_1"),
        (creem.get_subscription, "subscription_id", "sub_1", "/v1/subscriptions?subscription_id=sub_1"),
        (creem.get_transaction, "transaction_id", "tran_1", "/v1/transactions?transaction_id=tran_1"),
    ],
)
def test_lookups_validate_environment_and_requested_identifier(gateway, operation, field, reference, url):
    replies, requests = gateway
    correct = {"id": reference, "mode": "sandbox"}
    replies.extend([(200, correct), (200, {**correct, "mode": "prod"}), (200, {**correct, "id": "other"})])
    args = {"api_key": KEY, "mode": "test", field: reference}
    assert operation(**args) == correct
    with pytest.raises(creem.CreemVerificationError, match="resource and environment"):
        operation(**args)
    with pytest.raises(creem.CreemVerificationError, match="resource and environment"):
        operation(**args)
    assert all(call[1:4] == ("GET", url, None) for call in requests)


def checkout_response(**updates):
    return {
        "id": "ch_123",
        "mode": "test",
        "status": "pending",
        "checkout_url": "https://checkout.creem.io/ch_123",
        "product": "prod_1",
        "request_id": "jjtv_123",
        **updates,
    }


def create_checkout(**updates):
    options = {
        "api_key": KEY, "mode": "test", "product_id": "prod_1", "order_no": "jjtv_123",
        "success_url": "https://watch.example.com/account?order_no=jjtv_123", **updates,
    }
    return creem.create_checkout(**options)


def test_checkout_sends_stable_local_order_correlation(gateway):
    replies, requests = gateway
    expected = checkout_response(product={"id": "prod_1", "price": 500})
    replies.append((200, expected))
    assert create_checkout(customer_email="buyer@example.com") == expected
    assert requests == [
        (
            "test-api.creem.io", "POST", "/v1/checkouts",
            {
                "product_id": "prod_1", "request_id": "jjtv_123", "units": 1,
                "success_url": "https://watch.example.com/account?order_no=jjtv_123",
                "customer": {"email": "buyer@example.com"}, "metadata": {"jjtv_order_no": "jjtv_123"},
            },
            {"Accept": "application/json", "x-api-key": KEY, "Content-Type": "application/json"},
        ),
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://checkout.creem.io/ch_123", "https://checkout.creem.io.example.com/ch_123",
        "https://checkout.creem.io@another.example/ch_123", "https://user@checkout.creem.io/ch_123",
        "https://checkout.creem.io:8443/ch_123", "https://checkout.creem.io\\@another.example/ch_123",
        "https://checkout.creem.io/", "javascript:alert(1)",
    ],
)
def test_checkout_rejects_unsupported_redirects(gateway, url):
    replies, _ = gateway
    replies.append((200, checkout_response(checkout_url=url)))
    with pytest.raises(creem.CreemVerificationError, match="hosted checkout origin"):
        create_checkout()


@pytest.mark.parametrize("changed", [{"request_id": "jjtv_456"}, {"product": "prod_2"}, {"mode": "prod"}])
def test_checkout_rejects_mismatched_order_product_or_environment(gateway, changed):
    replies, _ = gateway
    replies.append((200, checkout_response(**changed)))
    with pytest.raises(creem.CreemVerificationError):
        create_checkout()


def test_subscription_cancellation_is_explicitly_scheduled(gateway):
    replies, requests = gateway
    expected = {"id": "sub_1", "mode": "prod", "status": "scheduled_cancel"}
    replies.append((200, expected))
    assert creem.cancel_subscription(api_key=KEY, mode="production", subscription_id="sub_1") == expected
    assert requests[0][:4] == (
        "api.creem.io", "POST", "/v1/subscriptions/sub_1/cancel", {"mode": "scheduled", "onExecute": "cancel"},
    )


@pytest.mark.parametrize("code", [302, 429, 500])
def test_mutation_errors_never_retry_redirect_or_expose_provider_data(gateway, code):
    replies, requests = gateway
    replies.append((code, {"error": f"Private provider details {KEY}"}))
    with pytest.raises(creem.CreemHTTPError) as caught:
        create_checkout()
    assert caught.value.status_code == code
    assert KEY not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize("payload", [OSError(f"connection details {KEY}"), b"{" + b" " * (1024 * 1024)])
def test_transport_errors_are_bounded_and_sanitized(gateway, payload):
    replies, requests = gateway
    replies.append((200, payload))
    with pytest.raises(creem.CreemTransportError) as caught:
        create_checkout()
    assert KEY not in str(caught.value)
    assert len(requests) == 1


def test_header_and_path_controls_fail_before_transport(gateway):
    _replies, requests = gateway
    with pytest.raises(creem.CreemConfigurationError):
        creem.verify_merchant(api_key="key\r\nInjected: value", mode="test")
    with pytest.raises(creem.CreemConfigurationError):
        creem.get_product(api_key=KEY, mode="test", product_id="../prod_1")
    with pytest.raises(creem.CreemConfigurationError):
        creem.verify_merchant(api_key=KEY, mode="https://another.example")
    assert requests == []


def signature(body):
    return hmac.digest(SECRET.encode(), body, "sha256").hex()


def test_webhook_signs_exact_raw_body_and_retains_tax_and_transaction_fields():
    event = {
        "id": "evt_1", "eventType": "subscription.paid", "created_at": 1730000000000,
        "object": {
            "id": "sub_1", "mode": "test",
            "last_transaction": {"id": "tran_1", "amount": 500, "tax_amount": 100, "amount_paid": 600},
        },
    }
    body = json.dumps(event, indent=2).encode()
    assert creem.verify_webhook(body, signature(body).upper(), SECRET) == event
    with pytest.raises(creem.CreemVerificationError, match="signature"):
        creem.verify_webhook(body + b"\n", signature(body), SECRET)
    with pytest.raises(creem.CreemVerificationError, match="signature"):
        creem.verify_webhook(body, signature(body), "different-webhook-secret")


@pytest.mark.parametrize(
    "body",
    [
        b'{"id":"evt_1","eventType":"checkout.completed","object":{},"object":{}}',
        b'{"id":"evt_1","eventType":"checkout.completed","object":{"amount":NaN}}',
        b'{"id":"evt_1","eventType":"checkout.completed","object":[]}',
        b'{"id":99,"eventType":"checkout.completed","object":{}}',
    ],
)
def test_webhook_rejects_ambiguous_or_invalid_signed_envelopes(body):
    with pytest.raises(creem.CreemVerificationError):
        creem.verify_webhook(body, signature(body), SECRET)
