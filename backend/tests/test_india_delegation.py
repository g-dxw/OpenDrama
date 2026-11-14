"""Signed India content request contract; no staff password, real network or database."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from uuid import UUID

import httpx
import pytest

from app.modules.tenancy.india import IndiaClient, IndiaError

KEY = "synthetic_delegated_key_" + "d" * 43
BASE = "https://india.example/v1"
OPERATOR = "103"


@pytest.fixture()
def delegated_key(monkeypatch, tmp_path):
    path = tmp_path / "delegated.key"
    path.write_text(KEY, encoding="ascii")
    path.chmod(0o600)
    monkeypatch.setenv("INDIA_JJTV_DELEGATED_ENABLED", "true")
    monkeypatch.setenv("INDIA_JJTV_DELEGATED_SECRET_FILE", str(path))
    monkeypatch.delenv("INDIA_JJTV_CLIENT_ENABLED", raising=False)
    monkeypatch.delenv("INDIA_JJTV_CLIENT_SECRET_FILE", raising=False)
    return path


def decode_proof(request):
    encoded, signature = request.headers["x-jjtv-delegated-proof"].split(".")
    decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    assert hmac.compare_digest(decode(signature), hmac.new(KEY.encode(), encoded.encode(), hashlib.sha256).digest())
    claims = json.loads(decode(encoded))
    assert set(claims) == {"v", "iss", "aud", "tenant_id", "operator_id", "actor_scope", "permissions", "iat", "exp", "jti", "method", "target", "body_sha256", "content_type", "content_length", "idempotency_key"}
    assert (claims["v"], claims["iss"], claims["aud"], claims["tenant_id"], claims["operator_id"]) == (1, "jjtv", "india-content", "india-short-drama", OPERATOR)
    assert 0 <= int(time.time()) - claims["iat"] <= 2
    assert claims["exp"] - claims["iat"] == 30
    assert str(UUID(claims["jti"])) == claims["jti"]
    assert claims["method"] == request.method
    assert claims["target"] == request.url.raw_path.decode("ascii")
    for header in ("content-type", "content-length", "idempotency-key"):
        assert claims[header.replace("-", "_")] == request.headers.get(header, "")
    assert "authorization" not in request.headers
    assert "x-jjtv-client-secret" not in request.headers
    assert "x-jjtv-client-id" not in request.headers
    assert "x-jjtv-operator-id" not in request.headers
    return claims


def response():
    return httpx.Response(200, json={"data": {"items": []}, "request_id": "delegated-contract"})


def test_delegation_signs_exact_json_query_actor_and_permissions_without_old_key(delegated_key):
    seen = []
    key = "b87f1a11-c842-4466-8535-9bafc4af7153"

    def handler(request):
        claims = decode_proof(request)
        assert claims["actor_scope"] == "platform"
        assert claims["permissions"] == ["dramas.edit", "dramas.view"]
        assert claims["body_sha256"] == hashlib.sha256(request.content).hexdigest()
        if request.method == "GET":
            assert request.url.params["q"] == "印度 /?+"
            assert claims["body_sha256"] == hashlib.sha256(b"").hexdigest()
        else:
            assert json.loads(request.content) == {"version": 3, "title": "印度标题"}
            assert claims["idempotency_key"] == key
        seen.append(claims)
        return response()

    client = IndiaClient(operator_id=OPERATOR, base_url=BASE, permissions=["dramas.view", "dramas.edit", "dramas.view"],
                         actor_scope="platform", transport=httpx.MockTransport(handler))

    async def scenario():
        await client.call("series_list", params={"limit": 30, "q": "印度 /?+"})
        await client.call("series_edit", resource_id="series_1", payload={"version": 3, "title": "印度标题"}, idempotency_key=key)

    asyncio.run(scenario())
    assert len(seen) == 2 and seen[0]["jti"] != seen[1]["jti"]


def test_delegated_stream_proof_is_built_before_consuming_upload_and_binds_reservation(delegated_key):
    events = []

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            claims = decode_proof(request)
            assert claims["body_sha256"] == "STREAMING"
            assert claims["content_length"] == "6"
            assert claims["content_type"] == "video/mp4"
            assert claims["target"] == "/v1/admin/uploads/upload_1/content"
            assert events == []
            assert b"".join([part async for part in request.stream]) == b"abcdef"
            assert events == ["first", "second"]
            return response()

    async def chunks():
        events.append("first")
        yield b"abc"
        events.append("second")
        yield b"def"

    client = IndiaClient(operator_id=OPERATOR, base_url=BASE, permissions=["dramas.upload", "dramas.view"], transport=Transport())
    asyncio.run(client.upload("video", "upload_1", chunks=chunks(), size=6, content_type="video/mp4; charset=binary",
                             idempotency_key="b87f1a11-c842-4466-8535-9bafc4af7153"))


@pytest.mark.parametrize("setting", ["disabled", "mistyped-flag", "missing", "relative", "invalid"])
def test_delegation_requires_its_own_enabled_valid_key_and_never_uses_password_key(delegated_key, monkeypatch, setting):
    monkeypatch.setenv("INDIA_JJTV_CLIENT_ENABLED", "true")
    monkeypatch.setenv("INDIA_JJTV_CLIENT_SECRET_FILE", str(delegated_key))
    if setting == "disabled": monkeypatch.delenv("INDIA_JJTV_DELEGATED_ENABLED")
    elif setting == "mistyped-flag": monkeypatch.setenv("INDIA_JJTV_DELEGATED_ENABLED", "ture")
    elif setting == "missing": delegated_key.unlink()
    elif setting == "relative": monkeypatch.setenv("INDIA_JJTV_DELEGATED_SECRET_FILE", "relative.key")
    else: delegated_key.write_text("short", encoding="ascii")
    with pytest.raises(IndiaError) as caught:
        IndiaClient(operator_id=OPERATOR, base_url=BASE, permissions=["dramas.view"])
    assert (caught.value.code, caught.value.status_code) == ("india_delegated_unavailable", 503)


@pytest.mark.parametrize("permissions,actor_scope", [(["finance.view"], "tenant"), (["dramas.view"], "attacker"), ([None], "tenant")])
def test_delegation_rejects_noncontent_permissions_and_invalid_actor_scope(delegated_key, permissions, actor_scope):
    with pytest.raises(IndiaError) as caught:
        IndiaClient(operator_id=OPERATOR, base_url=BASE, permissions=permissions, actor_scope=actor_scope)
    assert caught.value.code == "india_delegated_identity_invalid"


@pytest.mark.parametrize("action", ["me", "login", "refresh", "logout"])
def test_delegated_identity_cannot_use_old_employee_auth_routes(delegated_key, action):
    client = IndiaClient(operator_id=OPERATOR, base_url=BASE, permissions=["dramas.view"])
    with pytest.raises(IndiaError) as caught:
        asyncio.run(client.call(action))
    assert caught.value.code == "india_action_not_allowed"


def test_delegation_trust_failure_does_not_request_legacy_reconnection(delegated_key):
    client = IndiaClient(operator_id=OPERATOR, base_url=BASE, permissions=["dramas.view"], transport=httpx.MockTransport(
        lambda request: httpx.Response(401, json={"code": "JJTV_DELEGATED_PROOF_INVALID", "request_id": "denied"})))
    with pytest.raises(IndiaError) as caught:
        asyncio.run(client.call("series_list"))
    assert (caught.value.code, caught.value.status_code) == ("india_delegated_unavailable", 503)
