"""Fixed India API connector. No database, credentials persistence, or URL proxying.

The caller supplies the current JJTV operator's encrypted-at-rest upstream business
credentials. Passwords and TOTP values are used only in their respective requests.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Mapping
from dataclasses import dataclass
import json
import os
import re
from typing import Any
from uuid import UUID
from urllib.parse import urlsplit, urlunsplit

import httpx

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{20,500}\Z")
_SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,79}\Z")
_STAFF_ROLES = frozenset({"admin", "editor", "reviewer", "support", "finance", "growth"})


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
    "configuration": _Action("GET", "configuration", False),
    "me": _Action("GET", "me"),
    "login": _Action("POST", "auth/admin/login", False),
    "mfa_verify": _Action("POST", "auth/admin/mfa", False),
    "mfa_cancel": _Action("POST", "auth/admin/mfa/cancel", False),
    "mfa_enrollment": _Action("POST", "auth/admin/mfa/enrollment", False),
    "refresh": _Action("POST", "auth/refresh", False),
    "logout": _Action("POST", "auth/logout", False),
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
        challenge = value.get("challenge_token")
        if not isinstance(challenge, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge) or not isinstance(value.get("expires_at"), str):
            raise IndiaError("india_invalid_response")
        return {"state": "mfa_required", "challenge_token": challenge, "expires_at": value["expires_at"],
                "enrollment_required": value.get("enrollment_required") is True}
    access, refresh = _credential(value.get("access_token")), _credential(value.get("refresh_token"))
    result = {"state": "connected", "access_token": access, "refresh_token": refresh,
              "user": normalize_staff(value.get("user"))}
    for key in ("expires_at", "refresh_expires_at"):
        if isinstance(value.get(key), str):
            result[key] = value[key]
    return result


class IndiaClient:
    """Each operation owns its HTTP client; construction accepts server configuration only."""

    def __init__(self, *, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = normalize_api_base(base_url if base_url is not None else os.environ.get("INDIA_API_BASE_URL"))
        self._transport = transport

    async def call(self, action: str, *, access_token: str | None = None, resource_id: str | None = None,
                   payload: Mapping[str, Any] | None = None, params: Mapping[str, Any] | None = None,
                   idempotency_key: str | None = None) -> IndiaReply:
        idempotency_headers = _idempotency_headers(idempotency_key)
        spec = _ACTIONS.get(action)
        if spec is None:
            raise IndiaError("india_action_not_allowed", 400)
        if "{id}" in spec.path:
            path = spec.path.format(id=safe_id(resource_id))
        elif resource_id is not None:
            raise IndiaError("india_invalid_resource", 400)
        else:
            path = spec.path
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
        return await self._send(spec.method, path, access_token=access_token, payload=body, params=query, headers=idempotency_headers)

    async def upload(self, kind: str, upload_id: str, *, access_token: str, chunks: AsyncIterable[bytes],
                     size: int, content_type: str, idempotency_key: str | None = None) -> IndiaReply:
        idempotency_headers = _idempotency_headers(idempotency_key)
        spec = _UPLOADS.get(kind)
        if spec is None:
            raise IndiaError("india_action_not_allowed", 400)
        path, limit, content_types = spec
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
                    upload: bool = False) -> IndiaReply:
        request_headers = {"Accept": "application/json", "User-Agent": "JJTV-India-Connector/1.0", **(headers or {})}
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
                    async with client.stream(method, path, **options) as response:
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
                            code = "india_auth_required" if status == 401 else "india_forbidden" if status == 403 else "india_upstream_error"
                            raise IndiaError(code, 409 if status == 401 else status if 400 <= status < 500 else 502,
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
