"""Adapter for the shared-secret Epay V1 payment protocol."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit, urlunsplit


_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_NUMERIC_LABEL = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)\Z")
_BASE_PATH = re.compile(r"[A-Za-z0-9._~/-]*\Z")
_REFERENCE = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_METHOD = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_AMOUNT = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,2})?\Z")
_PARAMETER_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_PARAMETER_BOUNDARY = re.compile(r"&[A-Za-z0-9_-]{1,64}=")
_SIGNATURE = re.compile(r"[0-9A-Fa-f]{32}\Z")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_QUERY_TIMEOUT = 5.0
_RESPONSE_LIMIT = 64 * 1024


class EpayError(ValueError):
    pass


class EpayConfigurationError(EpayError):
    pass


class EpayTransportError(EpayError):
    pass


class EpayVerificationError(EpayError):
    pass


@dataclass(frozen=True)
class EpayPayment:
    order_no: str
    trade_no: str
    method: str
    amount_minor: int
    paid: bool


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address):
        if address.scope_id or address.sixtofour or address.teredo or address in _NAT64:
            return False
        address = address.ipv4_mapped or address
    return address.is_global and not (address.is_multicast or address.is_reserved)


def _public_host(host: str) -> bool:
    try:
        return _public_address(host)
    except ValueError:
        labels = host.split(".")
        return (
            len(host) <= 253
            and len(labels) >= 2
            and labels[-1] not in {"localhost", "local", "internal"}
            and not _NUMERIC_LABEL.fullmatch(labels[-1])
            and all(_DOMAIN_LABEL.fullmatch(label) for label in labels)
        )


def normalize_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or not endpoint or any(not 33 <= ord(character) <= 126 for character in endpoint):
        raise EpayConfigurationError("Epay endpoint must be a public HTTPS base URL")
    try:
        parsed = urlsplit(endpoint)
        host = (parsed.hostname or "").rstrip(".")
        if (
            parsed.scheme == "https"
            and parsed.port in (None, 443)
            and parsed.username is None
            and not any(marker in endpoint for marker in ("?", "#", "\\"))
            and _public_host(host)
            and _BASE_PATH.fullmatch(parsed.path)
            and not any(segment in {".", ".."} for segment in parsed.path.split("/"))
        ):
            authority = f"[{host}]" if ":" in host else host
            return urlunsplit(("https", authority, parsed.path.rstrip("/"), "", ""))
    except ValueError:
        pass
    raise EpayConfigurationError("Epay endpoint must be a public HTTPS base URL")


def _sign(params: Mapping[str, str], secret: str) -> str:
    canonical = "&".join(
        f"{key}={value}"
        for key, value in sorted(params.items())
        if key not in {"sign", "sign_type"} and value
    )
    return hashlib.md5((canonical + secret).encode("utf-8")).hexdigest()


def _cny_amount(amount_minor: int) -> str:
    if type(amount_minor) is not int or not 0 < amount_minor < 10**14:
        raise EpayConfigurationError("Invalid Epay amount")
    major, minor = divmod(amount_minor, 100)
    return f"{major}.{minor:02}"


def _minor_amount(amount: str) -> int:
    if not _AMOUNT.fullmatch(amount):
        raise EpayVerificationError("Invalid Epay payment amount")
    major, _, fraction = amount.partition(".")
    amount_minor = int(major) * 100 + int(fraction.ljust(2, "0"))
    if amount_minor <= 0:
        raise EpayVerificationError("Invalid Epay payment amount")
    return amount_minor


def _valid_callback_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.username is None
    except ValueError:
        return False


def checkout_url(
    *, endpoint: str, merchant_id: str, secret: str, method: str, order_no: str,
    name: str, amount_minor: int, notify_url: str, return_url: str,
) -> str:
    if not _REFERENCE.fullmatch(merchant_id) or not secret:
        raise EpayConfigurationError("Invalid Epay merchant credentials")
    if not _REFERENCE.fullmatch(order_no) or not _METHOD.fullmatch(method):
        raise EpayConfigurationError("Invalid Epay checkout reference or method")
    if not (_valid_callback_url(notify_url) and _valid_callback_url(return_url)):
        raise EpayConfigurationError("Invalid Epay callback or return URL")
    # A V1 callback echoes the name. Keep it within the provider's UTF-8 field
    # limit and prevent parameter separators in the name from obscuring the signature.
    signed_name = _PARAMETER_BOUNDARY.sub(lambda match: "＆" + match.group()[1:], name)
    signed_name = signed_name.encode("utf-8")[:127].decode("utf-8", errors="ignore").strip()
    params = {
        "pid": merchant_id,
        "type": method,
        "out_trade_no": order_no,
        "name": signed_name or "JJTV",
        "money": _cny_amount(amount_minor),
        "notify_url": notify_url,
        "return_url": return_url,
        "sign_type": "MD5",
    }
    params["sign"] = _sign(params, secret)
    return f"{normalize_endpoint(endpoint)}/submit.php?{urlencode(params)}"


def _payment(params: Mapping[str, str], paid: bool) -> EpayPayment:
    order_no = params.get("out_trade_no", "")
    trade_no = params.get("trade_no", "")
    method = params.get("type", "")
    if not _REFERENCE.fullmatch(order_no) or not _REFERENCE.fullmatch(trade_no) or not _METHOD.fullmatch(method):
        raise EpayVerificationError("Invalid Epay payment reference or method")
    return EpayPayment(order_no, trade_no, method, _minor_amount(params.get("money", "")), paid)


def verify_callback(params: Mapping[str, str], merchant_id: str, secret: str) -> EpayPayment:
    sign = params.get("sign", "")
    # V1 signs the same way in both directions. Checkout-only fields would
    # let a signed browser request be interpreted as a payment notification.
    if (
        not secret
        or not _SIGNATURE.fullmatch(sign)
        or params.get("sign_type", "").upper() != "MD5"
        or params.get("pid") != merchant_id
        or "notify_url" in params
        or "return_url" in params
        or any(not _PARAMETER_NAME.fullmatch(key) or _PARAMETER_BOUNDARY.search(value) for key, value in params.items())
        or not hmac.compare_digest(sign.lower(), _sign(params, secret))
    ):
        raise EpayVerificationError("Epay callback signature verification failed")
    return _payment(params, params.get("trade_status") == "TRADE_SUCCESS")


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    """Connect to vetted addresses while verifying TLS for the original host."""

    def connect(self) -> None:
        addresses = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        if not addresses or any(not _public_address(address[0]) for _, _, _, _, address in addresses):
            raise EpayTransportError("Epay gateway resolved to a non-public address")
        deadline = time.monotonic() + self.timeout
        for family, kind, protocol, _, address in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            connection = socket.socket(family, kind, protocol)
            try:
                connection.settimeout(remaining)
                connection.connect(address)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                connection.settimeout(remaining)
                self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
                return
            except OSError:
                continue
            finally:
                if self.sock is None:
                    connection.close()
        raise EpayTransportError("Epay gateway TLS connection failed")


def _read_query(endpoint: str, params: Mapping[str, str]) -> dict:
    parsed = urlsplit(normalize_endpoint(endpoint))
    connection = _PublicHTTPSConnection(parsed.netloc, timeout=_QUERY_TIMEOUT)
    try:
        connection.request(
            "GET", f"{parsed.path}/api.php?{urlencode(params)}", headers={"Accept": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise EpayTransportError("Epay gateway query failed")
        body = response.read(_RESPONSE_LIMIT + 1)
        if len(body) > _RESPONSE_LIMIT:
            raise EpayTransportError("Epay gateway query response exceeds the size limit")
        payload = json.loads(body, parse_float=str)
    except EpayTransportError:
        raise
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        raise EpayTransportError("Epay gateway query failed") from None
    finally:
        connection.close()
    if not isinstance(payload, dict):
        raise EpayTransportError("Invalid Epay gateway query response")
    return payload


def verify_merchant(*, endpoint: str, merchant_id: str, secret: str) -> bool:
    if not _REFERENCE.fullmatch(merchant_id) or not secret:
        raise EpayConfigurationError("Invalid Epay merchant credentials")
    payload = _read_query(endpoint, {"act": "query", "pid": merchant_id, "key": secret})
    return (
        str(payload.get("code")) == "1"
        and str(payload.get("pid")) == merchant_id
        and str(payload.get("active")) == "1"
    )


def query_order(*, endpoint: str, merchant_id: str, secret: str, order_no: str) -> EpayPayment | None:
    if not _REFERENCE.fullmatch(merchant_id) or not secret:
        raise EpayConfigurationError("Invalid Epay merchant credentials")
    if not _REFERENCE.fullmatch(order_no):
        raise EpayConfigurationError("Invalid Epay order reference")
    payload = _read_query(endpoint, {"act": "order", "pid": merchant_id, "key": secret, "out_trade_no": order_no})
    if str(payload.get("code")) != "1":
        return None
    status = str(payload.get("status"))
    if str(payload.get("pid")) != merchant_id or payload.get("out_trade_no") != order_no or status not in {"0", "1"}:
        raise EpayVerificationError("Epay query does not match the requested order")
    params = {key: str(payload[key]) for key in ("out_trade_no", "trade_no", "type", "money") if key in payload}
    return _payment(params, status == "1")
