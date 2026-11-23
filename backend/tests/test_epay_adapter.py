import hashlib
import socket
from urllib.parse import parse_qs, urlsplit

import pytest

from app.modules.billing import epay


ENDPOINT = "https://gateway.example.com/payments"
MERCHANT = "1001"
SECRET = "test-merchant-key"


def test_checkout_uses_epay_v1_browser_redirect():
    url = epay.checkout_url(
        endpoint=f"{ENDPOINT}/",
        merchant_id=MERCHANT,
        secret=SECRET,
        method="alipay",
        order_no="jj123",
        name="VIP A & B",
        amount_minor=5990,
        notify_url="https://api.example.com/notify",
        return_url="https://watch.example.com/membership?ok=1",
    )

    parsed = urlsplit(url)
    assert parsed.path == "/payments/submit.php"
    unsigned = (
        "money=59.90&name=VIP A & B&notify_url=https://api.example.com/notify"
        "&out_trade_no=jj123&pid=1001&return_url=https://watch.example.com/membership?ok=1&type=alipay"
    )
    assert parse_qs(parsed.query) == {
        "money": ["59.90"],
        "name": ["VIP A & B"],
        "notify_url": ["https://api.example.com/notify"],
        "out_trade_no": ["jj123"],
        "pid": [MERCHANT],
        "return_url": ["https://watch.example.com/membership?ok=1"],
        "type": ["alipay"],
        "sign_type": ["MD5"],
        "sign": [hashlib.md5((unsigned + SECRET).encode()).hexdigest()],
    }
    assert SECRET not in url


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://gateway.example.com", "https://127.0.0.1", "https://0x7f000001",
        "https://[::ffff:127.0.0.1]", "https://[ff02::1]", "https://[64:ff9b::7f00:1]",
        "https://gateway.example.com:1443", "https://user@gateway.example.com",
        "https://gateway.example.com/payments?api=1", "https://gateway.example.com/other/../pay",
    ],
)
def test_rejects_untrusted_endpoints(endpoint):
    with pytest.raises(epay.EpayConfigurationError):
        epay.normalize_endpoint(endpoint)


def callback(status="TRADE_SUCCESS", amount="59.9"):
    unsigned = (
        f"money={amount}&name=VIP A & B&out_trade_no=jj123&pid=1001"
        f"&trade_no=provider-42&trade_status={status}&type=alipay"
    )
    return {
        "pid": MERCHANT,
        "out_trade_no": "jj123",
        "name": "VIP A & B",
        "trade_no": "provider-42",
        "trade_status": status,
        "money": amount,
        "type": "alipay",
        "param": "",
        "sign_type": "MD5",
        "sign": hashlib.md5((unsigned + SECRET).encode()).hexdigest(),
    }


def test_callback_verifies_signed_amount_order_method_and_status():
    params = callback()
    assert epay.verify_callback(params, MERCHANT, SECRET) == epay.EpayPayment(
        order_no="jj123", trade_no="provider-42", method="alipay", amount_minor=5990, paid=True,
    )
    pending = callback(status="WAIT_BUYER_PAY")
    assert not epay.verify_callback(pending, MERCHANT, SECRET).paid

    for key, replacement in {
        "out_trade_no": "jj124", "trade_no": "provider-43", "money": "0.01",
        "pid": "1002", "type": "wxpay", "sign_type": "RSA", "trade_status": "WAIT_BUYER_PAY",
    }.items():
        with pytest.raises(epay.EpayVerificationError):
            epay.verify_callback({**params, key: replacement}, MERCHANT, SECRET)


def test_callback_rejects_checkout_only_fields_even_if_signed():
    params = callback()
    params.update(notify_url="https://api.example.com/notify", return_url="https://watch.example.com/membership")
    params["sign"] = epay._sign(params, SECRET)

    with pytest.raises(epay.EpayVerificationError):
        epay.verify_callback(params, MERCHANT, SECRET)


@pytest.mark.parametrize("amount", ["0", "0.00", "001.234", "1e0", "NaN", "1.000", "-1", "1,20"])
def test_callback_rejects_invalid_signed_amount(amount):
    with pytest.raises(epay.EpayVerificationError, match="Invalid Epay payment amount"):
        epay.verify_callback(callback(amount=amount), MERCHANT, SECRET)


def test_merchant_test_returns_only_verification_and_requires_matching_active_account(monkeypatch):
    response = {"code": 1, "pid": 1001, "active": 1, "key": SECRET}

    def read_query(endpoint, fields):
        assert endpoint == ENDPOINT
        assert fields == {"act": "query", "pid": MERCHANT, "key": SECRET}
        return response

    monkeypatch.setattr(epay, "_read_query", read_query)
    credentials = {"endpoint": ENDPOINT, "merchant_id": MERCHANT, "secret": SECRET}
    assert epay.verify_merchant(**credentials) is True
    response["pid"] = 1002
    assert epay.verify_merchant(**credentials) is False
    response.update(pid=1001, active=0)
    assert epay.verify_merchant(**credentials) is False


def test_query_normalizes_only_matching_orders(monkeypatch):
    response = {
        "code": "1", "pid": 1001, "status": "0", "out_trade_no": "jj123",
        "trade_no": "provider-42", "type": "alipay", "money": "59.90",
    }

    def read_query(endpoint, fields):
        assert endpoint == ENDPOINT
        assert fields == {"act": "order", "pid": MERCHANT, "key": SECRET, "out_trade_no": "jj123"}
        return response

    monkeypatch.setattr(epay, "_read_query", read_query)
    args = {"endpoint": ENDPOINT, "merchant_id": MERCHANT, "secret": SECRET, "order_no": "jj123"}
    assert epay.query_order(**args) == epay.EpayPayment("jj123", "provider-42", "alipay", 5990, False)
    response["status"] = 1
    assert epay.query_order(**args).paid is True
    response["out_trade_no"] = "jj124"
    with pytest.raises(epay.EpayVerificationError, match="does not match"):
        epay.query_order(**args)
    response["code"] = -1
    assert epay.query_order(**args) is None


def test_query_rejects_mixed_private_dns_results_before_connecting(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.1", 443)),
        ],
    )

    def unexpected_socket(*args, **kwargs):
        pytest.fail("DNS must be vetted before making a socket connection")

    monkeypatch.setattr(socket, "socket", unexpected_socket)
    with pytest.raises(epay.EpayTransportError, match="non-public address"):
        epay.verify_merchant(endpoint=ENDPOINT, merchant_id=MERCHANT, secret=SECRET)


def test_query_pins_connection_and_keeps_original_tls_hostname(monkeypatch):
    host = "gateway.example.com"
    expected_address = ("1.1.1.1", 443)

    def getaddrinfo(hostname, port, **kwargs):
        assert (hostname, port) == (host, 443)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", expected_address)]

    class DummySocket:
        def settimeout(self, timeout):
            assert 0 < timeout <= 5

        def connect(self, target):
            assert target == expected_address

    class DummyContext:
        def wrap_socket(self, connection, *, server_hostname):
            assert connection is direct_socket
            assert server_hostname == host
            return tls_socket

    direct_socket = DummySocket()
    tls_socket = object()
    connection = epay._PublicHTTPSConnection(host, timeout=5)
    connection._context = DummyContext()
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "socket", lambda *args: direct_socket)

    connection.connect()
    assert connection.sock is tls_socket


@pytest.mark.parametrize("failure", ["redirect", "transport"])
def test_query_never_follows_redirects_or_exposes_credentials(monkeypatch, failure):
    requests = []

    class DummyConnection:
        def __init__(self, host, *, timeout):
            assert (host, timeout) == ("gateway.example.com", 5)

        def request(self, method, path, **kwargs):
            requests.append((method, path))
            if failure == "transport":
                raise OSError(path)

        def getresponse(self):
            return type("Redirect", (), {"status": 302, "headers": {"Location": "https://another.example.com"}})()

        def close(self):
            pass

    monkeypatch.setattr(epay, "_PublicHTTPSConnection", DummyConnection)
    with pytest.raises(epay.EpayTransportError) as caught:
        epay.verify_merchant(endpoint=ENDPOINT, merchant_id=MERCHANT, secret=SECRET)
    assert SECRET not in str(caught.value)
    assert requests == [("GET", f"/payments/api.php?act=query&pid=1001&key={SECRET}")]
