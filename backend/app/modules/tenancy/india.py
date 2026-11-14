"""Fixed India API connector. No database, credentials persistence, or URL proxying.

Delegated requests carry server-derived tenant permissions and a short-lived,
request-bound proof. The optional legacy mode uses encrypted upstream business
credentials, with passwords sent only to its dedicated client login endpoint.
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import hashlib
import hmac
from pathlib import Path
import os
import re
import time
from typing import Any
from uuid import UUID, uuid4
from urllib.parse import urlsplit, urlunsplit

import httpx

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{20,500}\Z")
_SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,79}\Z")
_STAFF_ROLES = frozenset({"admin", "editor", "reviewer", "support", "finance", "growth"})
DELEGATED_PERMISSIONS = frozenset({"dramas.view", "dramas.edit", "dramas.upload", "dramas.publish",
                                   "dramas.pricing", "reviews.view", "reviews.decide"})


class IndiaError(Exception):
    """A safe public error; never includes an upstream body, URL or credential."""

    def __init__(self, code: str, status_code: int = 502, *, upstream_code: str | None = None,
                 request_id: str | None = None, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.upstream_code = upstream_code
        self.request_id = request_id
        self.retryable = retryable


@dataclass(frozen=True)
class IndiaReply:
    data: Any
    request_id: str | None


@dataclass(frozen=True)
class _Action:
    method: str
    path: str
    authenticated: bool = True
    query: frozenset[str] = frozenset()


_LIST_QUERY = frozenset({"limit", "cursor", "q", "status"})
_ACTIONS = {
    "me": _Action("GET", "me"),
    "login": _Action("POST", "auth/jjtv/login", False),
    "refresh": _Action("POST", "auth/jjtv/refresh", False),
    "logout": _Action("POST", "auth/jjtv/logout", False),
    "series_list": _Action("GET", "admin/series", query=_LIST_QUERY | {"rights_state", "review_queue"}),
    "series_detail": _Action("GET", "admin/series/{id}"),
    "series_create": _Action("POST", "admin/series"),
    "series_edit": _Action("PATCH", "admin/series/{id}"),
    "episodes_create": _Action("POST", "admin/series/{id}/episodes"),
    "episode_upload_init": _Action("POST", "admin/episodes/{id}/uploads"),
    "upload_complete": _Action("POST", "admin/uploads/{id}/complete"),
    "jobs_list": _Action("GET", "admin/jobs", query=_LIST_QUERY),
    "job_detail": _Action("GET", "admin/jobs/{id}"),
    "job_retry": _Action("POST", "admin/jobs/{id}/retry"),
    "subtitles": _Action("POST", "admin/episodes/{id}/subtitles"),
    "submit": _Action("POST", "admin/series/{id}/submit"),
    "review": _Action("POST", "admin/series/{id}/review"),
    "publish": _Action("POST", "admin/series/{id}/publish"),
    "unpublish": _Action("POST", "admin/series/{id}/unpublish"),
    "rights_list": _Action("GET", "admin/series/{id}/rights/evidence"),
    "rights_upload_init": _Action("POST", "admin/series/{id}/rights/evidence"),
    "cover_upload_init": _Action("POST", "admin/series/{id}/cover/uploads"),
    "series_publications": _Action("GET", "admin/series/{id}/publications"),
}
INDIA_ACTIONS = frozenset(_ACTIONS)
_UPLOADS = {
    "video": ("admin/uploads/{id}/content", MAX_UPLOAD_BYTES,
              frozenset({"video/mp4", "video/quicktime", "video/webm", "video/x-matroska", "application/octet-stream"})),
    "rights": ("admin/rights/evidence/{id}/content", 10 * 1024 * 1024,
               frozenset({"application/pdf", "image/png", "image/jpeg", "text/plain", "application/octet-stream"})),
    "cover": ("admin/covers/{id}/content", 5 * 1024 * 1024,
              frozenset({"image/png", "image/jpeg", "application/octet-stream"})),
}
_INIT_LIMITS = {"episode_upload_init": MAX_UPLOAD_BYTES, "rights_upload_init": 10 * 1024 * 1024,
                "cover_upload_init": 5 * 1024 * 1024}


def safe_id(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise IndiaError("india_invalid_resource", 400)
    return value


def normalize_api_base(value: str | None) -> str:
    try:
        url = urlsplit(value.strip() if isinstance(value, str) else "")
        if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
                or url.query or url.fragment or url.path.rstrip("/") not in {"", "/v1"}
                or any(ord(char) < 33 or ord(char) == 127 for char in (value or ""))):
            raise ValueError()
        _ = url.port  # Validate malformed ports before making a request.
        return urlunsplit((url.scheme, url.netloc, "/v1/", "", ""))
    except (ValueError, TypeError):
        raise IndiaError("india_not_configured", 503) from None


def _credential(value: str | None, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not _CREDENTIAL.fullmatch(value):
        raise IndiaError("india_auth_required", 409)
    return value


def _idempotency_headers(value: str | None) -> dict[str, str]:
    if value is None:
        return {}
    try:
        if not isinstance(value, str) or len(value) != 36 or str(UUID(value)) != value.lower():
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise IndiaError("india_invalid_idempotency_key", 400) from None
    return {"Idempotency-Key": value.lower()}


def _request_id(value: Any) -> str | None:
    return value if isinstance(value, str) and _SAFE_ID.fullmatch(value) else None


def normalize_staff(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("role") not in _STAFF_ROLES:
        raise IndiaError("india_staff_required", 403)
    actor = {"id": safe_id(value.get("id")), "name": str(value.get("name") or "")[:200], "role": value["role"]}
    if isinstance(value.get("permissions"), list):
        actor["permissions"] = [p for p in value["permissions"] if isinstance(p, str) and re.fullmatch(r"[a-z][a-z0-9:_-]{0,100}", p)]
    return actor


def normalize_login(value: Any) -> dict[str, Any]:
    """Recognize the actual upstream password/MFA shapes without fabricating expiry."""
    if not isinstance(value, Mapping):
        raise IndiaError("india_invalid_response")
    if value.get("mfa_required") is True:
        raise IndiaError("india_client_contract_invalid")
    if value.get("auth_method") != "jjtv_client_password" or value.get("mfa_verified") is not False:
        raise IndiaError("india_client_contract_invalid")
    try:
        access, refresh = _credential(value.get("access_token")), _credential(value.get("refresh_token"))
    except IndiaError:
        # Malformed upstream success is not evidence that a saved connection expired.
        raise IndiaError("india_invalid_response") from None
    result = {"state": "connected", "access_token": access, "refresh_token": refresh,
              "user": normalize_staff(value.get("user"))}
    for key in ("expires_at", "refresh_expires_at"):
        try:
            expiry = value[key]
            parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00")) if isinstance(expiry, str) else None
            if parsed is None or parsed.tzinfo is None:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise IndiaError("india_invalid_response") from None
        result[key] = expiry
    return result


def normalize_refresh(value: Any, previous_user: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IndiaError("india_invalid_response")
    result = normalize_login({**value, "user": value.get("user", previous_user)})
    if result["user"]["id"] != previous_user["id"]:
        raise IndiaError("india_invalid_response")
    return result


def client_secret() -> str:
    """Read an independent, server-only client key; never fall back to web MFA."""
    if os.environ.get("INDIA_JJTV_CLIENT_ENABLED", "").lower() != "true":
        raise IndiaError("india_client_unavailable", 503)
    try:
        path = Path(os.environ.get("INDIA_JJTV_CLIENT_SECRET_FILE", ""))
        if not path.is_absolute() or not path.is_file() or path.stat().st_size > 256:
            raise ValueError()
        secret = path.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", secret):
            raise ValueError()
        return secret
    except (OSError, ValueError, UnicodeError):
        raise IndiaError("india_client_unavailable", 503) from None


def client_fingerprint() -> str:
    return hashlib.sha256(client_secret().encode("ascii")).hexdigest()


def delegated_enabled() -> bool:
    # A mistyped enable flag must select the fail-closed delegated path, rather
    # than quietly re-enable individual password connections.
    return os.environ.get("INDIA_JJTV_DELEGATED_ENABLED", "").strip().lower() not in {"", "false", "off"}


def delegated_secret() -> str:
    """Dedicated server signing key; enabling delegation never falls back to staff credentials."""
    if os.environ.get("INDIA_JJTV_DELEGATED_ENABLED", "").strip().lower() != "true":
        raise IndiaError("india_delegated_unavailable", 503)
    try:
        path = Path(os.environ.get("INDIA_JJTV_DELEGATED_SECRET_FILE", ""))
        if (not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > 256
                or os.name == "posix" and path.stat().st_mode & 0o077):
            raise ValueError()
        secret = path.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", secret):
            raise ValueError()
        return secret
    except (OSError, ValueError, UnicodeError):
        raise IndiaError("india_delegated_unavailable", 503) from None


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


@asynccontextmanager
async def _stream_request(client: httpx.AsyncClient, request: httpx.Request):
    response = await client.send(request, stream=True)
    try:
        yield response
    finally:
        await response.aclose()


class IndiaClient:
    """Each operation owns its HTTP client; construction accepts server configuration only."""

    def __init__(self, *, operator_id: str, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 permissions: list[str] | tuple[str, ...] | None = None, actor_scope: str = "tenant"):
        self.base_url = normalize_api_base(base_url if base_url is not None else os.environ.get("INDIA_API_BASE_URL"))
        self._transport = transport
        self._operator_id = safe_id(operator_id)
        self._delegated = permissions is not None
        if self._delegated:
            if (actor_scope not in {"tenant", "platform"} or not isinstance(permissions, (list, tuple))
                    or any(not isinstance(value, str) or value not in DELEGATED_PERMISSIONS for value in permissions)):
                raise IndiaError("india_delegated_identity_invalid", 400)
            self._permissions = sorted(set(permissions))
            self._actor_scope = actor_scope
            self._delegated_secret = delegated_secret()
        else:
            self._client_secret = client_secret()

    def _proof(self, request: httpx.Request, *, upload: bool) -> str:
        now = int(time.time())
        claims = {"v": 1, "iss": "jjtv", "aud": "india-content", "tenant_id": "india-short-drama",
                  "operator_id": self._operator_id, "actor_scope": self._actor_scope,
                  "permissions": self._permissions, "iat": now, "exp": now + 30, "jti": str(uuid4()),
                  "method": request.method, "target": request.url.raw_path.decode("ascii"),
                  "body_sha256": "STREAMING" if upload else hashlib.sha256(request.content).hexdigest(),
                  "content_type": request.headers.get("content-type", ""),
                  "content_length": request.headers.get("content-length", ""),
                  "idempotency_key": request.headers.get("idempotency-key", "")}
        encoded = _base64url(json.dumps(claims, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
        signature = hmac.new(self._delegated_secret.encode("ascii"), encoded.encode("ascii"), hashlib.sha256).digest()
        return encoded + "." + _base64url(signature)

    async def call(self, action: str, *, access_token: str | None = None, resource_id: str | None = None,
                   payload: Mapping[str, Any] | None = None, params: Mapping[str, Any] | None = None,
                   idempotency_key: str | None = None) -> IndiaReply:
        idempotency_headers = _idempotency_headers(idempotency_key)
        spec = _ACTIONS.get(action)
        if spec is None:
            raise IndiaError("india_action_not_allowed", 400)
        if self._delegated and action in {"me", "login", "refresh", "logout"}:
            raise IndiaError("india_action_not_allowed", 400)
        if "{id}" in spec.path:
            path = spec.path.format(id=safe_id(resource_id))
        elif resource_id is not None:
            raise IndiaError("india_invalid_resource", 400)
        else:
            path = spec.path
        if self._delegated:
            if access_token is not None:
                raise IndiaError("india_delegated_identity_invalid", 400)
        else:
            _credential(access_token, required=spec.authenticated)
        query: dict[str, str | int | bool] = {}
        for key, value in (params or {}).items():
            if key not in spec.query or not isinstance(value, (str, int, bool)) or isinstance(value, str) and len(value) > 2048:
                raise IndiaError("india_invalid_query", 400)
            query[key] = value
        if payload is not None and not isinstance(payload, Mapping):
            raise IndiaError("india_invalid_payload", 400)
        if spec.method == "GET" and payload is not None:
            raise IndiaError("india_invalid_payload", 400)
        body = None if spec.method == "GET" else dict(payload or {})
        if action in _INIT_LIMITS:
            size = body.get("size_bytes")
            if isinstance(size, bool) or not isinstance(size, int) or size < 1:
                raise IndiaError("india_upload_size_invalid", 400)
            if size > _INIT_LIMITS[action]:
                raise IndiaError("india_upload_too_large", 413)
        if body is not None:
            try:
                if len(json.dumps(body, ensure_ascii=False, allow_nan=False).encode()) > 256 * 1024:
                    raise IndiaError("india_payload_too_large", 413)
            except (TypeError, ValueError):
                raise IndiaError("india_invalid_payload", 400) from None
        auth_stage = action if action == "login" else None
        return await self._send(spec.method, path, access_token=access_token, payload=body, params=query,
                                headers=idempotency_headers, auth_stage=auth_stage)

    async def upload(self, kind: str, upload_id: str, *, access_token: str | None = None, chunks: AsyncIterable[bytes],
                     size: int, content_type: str, idempotency_key: str | None = None) -> IndiaReply:
        idempotency_headers = _idempotency_headers(idempotency_key)
        spec = _UPLOADS.get(kind)
        if spec is None:
            raise IndiaError("india_action_not_allowed", 400)
        path, limit, content_types = spec
        if self._delegated:
            if access_token is not None:
                raise IndiaError("india_delegated_identity_invalid", 400)
        else:
            _credential(access_token)
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise IndiaError("india_upload_size_invalid", 400)
        if size > limit:
            raise IndiaError("india_upload_too_large", 413)
        if not isinstance(content_type, str) or any(ord(c) < 32 or ord(c) == 127 for c in content_type):
            raise IndiaError("india_upload_type_invalid", 400)
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime not in content_types:
            raise IndiaError("india_upload_type_invalid", 400)

        async def bounded():
            received = 0
            async for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise IndiaError("india_upload_invalid_chunk", 400)
                received += len(chunk)
                if received > limit:
                    raise IndiaError("india_upload_too_large", 413)
                if received > size:
                    raise IndiaError("india_upload_size_mismatch", 400)
                for offset in range(0, len(chunk), 65536):
                    yield chunk[offset:offset + 65536]
            if received != size:
                raise IndiaError("india_upload_size_mismatch", 400)

        return await self._send("PUT", path.format(id=safe_id(upload_id)), access_token=access_token,
                                content=bounded(), headers={"Content-Type": mime, "Content-Length": str(size), **idempotency_headers}, upload=True)

    async def _send(self, method: str, path: str, *, access_token: str | None = None,
                    payload: dict | None = None, params: dict | None = None,
                    content: AsyncIterable[bytes] | None = None, headers: dict | None = None,
                    upload: bool = False, auth_stage: str | None = None) -> IndiaReply:
        request_headers = {"Accept": "application/json", "User-Agent": "JJTV-India-Connector/2.0", **(headers or {})}
        if not self._delegated:
            request_headers.update({"X-JJTV-Client-Id": "jjtv", "X-JJTV-Client-Secret": self._client_secret,
                                    "X-JJTV-Operator-Id": self._operator_id})
        if access_token:
            request_headers["Authorization"] = "Bearer " + _credential(access_token)
        timeout = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
        try:
            async with asyncio.timeout(300 if upload else 45):
                async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout, follow_redirects=False,
                                             trust_env=False, transport=self._transport) as client:
                    options = {"headers": request_headers, "params": params}
                    if content is not None:
                        options["content"] = content
                    elif payload is not None:
                        options["json"] = payload
                    upstream_request = client.build_request(method, path, **options)
                    if self._delegated:
                        upstream_request.headers["X-JJTV-Delegated-Proof"] = self._proof(upstream_request, upload=upload)
                    async with _stream_request(client, upstream_request) as response:
                        if 300 <= response.status_code < 400:
                            raise IndiaError("india_redirect_rejected")
                        limit = MAX_JSON_BYTES if response.is_success else 65536
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(data) + len(chunk) > limit:
                                raise IndiaError("india_invalid_response")
                            data.extend(chunk)
                        try:
                            envelope = json.loads(data)
                        except (ValueError, UnicodeDecodeError):
                            envelope = None
                        request_id = _request_id(envelope.get("request_id")) if isinstance(envelope, dict) else None
                        request_id = request_id or _request_id(response.headers.get("x-request-id"))
                        if not response.is_success:
                            upstream = envelope.get("code") if isinstance(envelope, dict) else None
                            upstream = upstream if isinstance(upstream, str) and _SAFE_CODE.fullmatch(upstream) else None
                            status = response.status_code
                            public_status = 409 if status == 401 else status if 400 <= status < 500 else 502
                            code = ("india_delegated_unavailable" if self._delegated else "india_auth_required") if status == 401 else "india_forbidden" if status == 403 else "india_upstream_error"
                            if self._delegated and status == 401:
                                public_status = 503
                            if status == 429:
                                code = "india_rate_limited"
                            elif self._delegated and upstream in {"JJTV_DELEGATED_DISABLED", "JJTV_DELEGATED_UNAVAILABLE", "JJTV_DELEGATED_PROOF_INVALID", "JJTV_DELEGATED_PROOF_REPLAYED"}:
                                code, public_status = "india_delegated_unavailable", 503
                            elif upstream in {"JJTV_CLIENT_DISABLED", "JJTV_CLIENT_UNAVAILABLE", "JJTV_CLIENT_PROOF_INVALID"}:
                                code, public_status = "india_client_unavailable", 503
                            elif status == 401 and auth_stage == "login":
                                code, public_status = "india_login_rejected", 400
                            raise IndiaError(code, public_status,
                                             upstream_code=upstream, request_id=request_id, retryable=status in {429, 502, 503, 504})
                        if not isinstance(envelope, dict) or "data" not in envelope or request_id is None:
                            raise IndiaError("india_invalid_response")
                        return IndiaReply(envelope["data"], request_id)
        except IndiaError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise IndiaError("india_timeout", 504, retryable=True) from None
        except httpx.RequestError:
            raise IndiaError("india_unavailable", 502, retryable=True) from None
