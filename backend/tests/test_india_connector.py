"""Contract-shaped India HTTP tests. No real server, credentials, or database I/O."""
from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest

from app.modules.tenancy.india import (
    IndiaClient, IndiaError, MAX_UPLOAD_BYTES, normalize_api_base, normalize_login,
    normalize_staff, normalize_refresh, client_fingerprint,
)

BASE = "https://india.test/v1"
ACCESS = "a" * 43
REFRESH = "r" * 43
OPERATOR = "jjtv_operator_1"
CLIENT_SECRET = "synthetic_client_key_" + "k" * 43
USER = {"id": "user_existing-admin", "name": "Content operator", "role": "admin", "age_verified": False}


def run(awaitable):
    return asyncio.run(awaitable)


def reply(data, *, status=200, request_id="req-contract-1"):
    return httpx.Response(status, json={"data": data, "request_id": request_id})


@pytest.fixture(autouse=True)
def client_key(monkeypatch, tmp_path):
    key_file = tmp_path / "jjtv-client.key"
    key_file.write_text(CLIENT_SECRET, encoding="ascii")
    monkeypatch.setenv("INDIA_JJTV_CLIENT_ENABLED", "true")
    monkeypatch.setenv("INDIA_JJTV_CLIENT_SECRET_FILE", str(key_file))
    return key_file


def provider_tokens(access=ACCESS, refresh=REFRESH, **extra):
    return {"access_token": access, "refresh_token": refresh, "auth_method": "jjtv_client_password",
            "mfa_verified": False, "expires_at": "2099-01-01T00:15:00.000Z",
            "refresh_expires_at": "2099-01-01T08:00:00.000Z", **extra}


def assert_client_headers(request, operator=OPERATOR):
    assert request.headers["x-jjtv-client-id"] == "jjtv"
    assert request.headers["x-jjtv-client-secret"] == CLIENT_SECRET
    assert request.headers["x-jjtv-operator-id"] == operator


def test_named_password_me_refresh_and_family_logout_use_dedicated_contract():
    seen = []

    def handler(request):
        assert_client_headers(request)
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        seen.append((request.method, path))
        if path == "/v1/auth/jjtv/login":
            assert body == {"username": "existing-admin", "password": "password-used-only-here"}
            assert "authorization" not in request.headers
            return reply(provider_tokens(user=USER))
        if path == "/v1/me":
            assert request.headers["authorization"] == "Bearer " + ACCESS
            return reply(USER)
        if path == "/v1/auth/jjtv/refresh":
            assert body == {"refresh_token": REFRESH}
            return reply(provider_tokens("b" * 43, "s" * 43))
        assert path == "/v1/auth/jjtv/logout"
        assert body == {"refresh_token": "s" * 43}
        return reply({"logged_out": True})

    client = IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=httpx.MockTransport(handler))

    async def scenario():
        connected = normalize_login((await client.call("login", payload={"username": "existing-admin", "password": "password-used-only-here"})).data)
        assert connected["state"] == "connected" and connected["user"]["id"] == USER["id"]
        assert "challenge_token" not in connected
        assert connected["expires_at"] == "2099-01-01T00:15:00.000Z"
        assert connected["refresh_expires_at"] == "2099-01-01T08:00:00.000Z"
        assert normalize_staff((await client.call("me", access_token=connected["access_token"])).data)["role"] == "admin"
        tokens = (await client.call("refresh", payload={"refresh_token": connected["refresh_token"]})).data
        refreshed = normalize_login({**tokens, "user": USER})
        await client.call("logout", payload={"refresh_token": refreshed["refresh_token"]})

    run(scenario())
    assert seen == [("POST", "/v1/auth/jjtv/login"), ("GET", "/v1/me"),
                    ("POST", "/v1/auth/jjtv/refresh"), ("POST", "/v1/auth/jjtv/logout")]
    assert not hasattr(client, "password") and not hasattr(client, "access_token")


def test_versioned_content_payloads_and_safe_query_are_not_remapped():
    expected = [
        ("POST", "/v1/admin/series", {"title": "A draft", "synopsis": "Synopsis", "genre": "Drama", "language": "hi", "content_origin": "live_action", "age_rating": "U", "rights": None}),
        ("PATCH", "/v1/admin/series/series_1", {"title": "Reviewed title", "version": 3}),
        ("POST", "/v1/admin/series/series_1/episodes", {"title": "Episode 1", "episode_no": 1, "duration_ms": 12000}),
        ("POST", "/v1/admin/series/series_1/submit", {"version": 4}),
        ("POST", "/v1/admin/series/series_1/review", {"version": 5, "approved": True, "reason": "Reviewed evidence"}),
        ("POST", "/v1/admin/series/series_1/publish", {"version": 6, "reason": "Publish approved draft"}),
        ("POST", "/v1/admin/series/series_1/unpublish", {"reason": "Rights withdrawn"}),
    ]
    seen = []

    def handler(request):
        assert request.headers["authorization"] == "Bearer " + ACCESS
        if request.method == "GET":
            assert request.url.path == "/v1/admin/series"
            assert dict(request.url.params) == {"q": "A & B", "limit": "30", "review_queue": "true"}
            return reply({"items": [{"id": "series_1", "version": 3}], "next_cursor": None})
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return reply({"id": "series_1", "version": len(seen) + 2})

    client = IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=httpx.MockTransport(handler))

    async def scenario():
        result = await client.call("series_list", access_token=ACCESS, params={"q": "A & B", "limit": 30, "review_queue": True})
        assert result.data["next_cursor"] is None and result.request_id == "req-contract-1"
        for action, resource, payload in [
            ("series_create", None, expected[0][2]), ("series_edit", "series_1", expected[1][2]),
            ("episodes_create", "series_1", expected[2][2]), ("submit", "series_1", expected[3][2]),
            ("review", "series_1", expected[4][2]), ("publish", "series_1", expected[5][2]),
            ("unpublish", "series_1", expected[6][2]),
        ]:
            await client.call(action, access_token=ACCESS, resource_id=resource, payload=payload)

    run(scenario())
    assert seen == expected


def test_conflicts_and_auth_errors_preserve_only_safe_correlation():
    for status, upstream, expected_status, expected_code in [
        (401, "SESSION_EXPIRED", 409, "india_auth_required"),
        (403, "FORBIDDEN", 403, "india_forbidden"),
        (409, "VERSION_CONFLICT", 409, "india_upstream_error"),
        (429, "RATE_LIMITED", 429, "india_rate_limited"),
        (503, "SECRET_PROVIDER_FAILURE", 502, "india_upstream_error"),
    ]:
        transport = httpx.MockTransport(lambda request: httpx.Response(status, json={
            "code": upstream, "request_id": "req-safe-id", "message_key": "secret-password",
            "details": {"credential": "must-not-escape"},
        }))
        with pytest.raises(IndiaError) as caught:
            run(IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=transport).call("me", access_token=ACCESS))
        error = caught.value
        assert (error.status_code, error.code, error.upstream_code, error.request_id) == (expected_status, expected_code, upstream, "req-safe-id")
        assert "must-not-escape" not in str(vars(error)) and "secret-password" not in str(error)
        assert error.retryable is (status in {429, 503})


@pytest.mark.parametrize("upstream", ["INVALID_CREDENTIALS", None])
def test_bad_password_is_not_an_expired_business_session(upstream):
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={
        "code": upstream, "request_id": "auth-safe-id", "details": {"secret": "must-not-escape"},
    }))
    with pytest.raises(IndiaError) as caught:
        run(IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=transport).call(
            "login", payload={"username": "synthetic", "password": "synthetic-password"}))
    error = caught.value
    assert (error.status_code, error.code, error.upstream_code, error.request_id) == (400, "india_login_rejected", upstream, "auth-safe-id")
    assert "must-not-escape" not in str(vars(error))


@pytest.mark.parametrize("upstream,status", [("JJTV_CLIENT_DISABLED", 503), ("JJTV_CLIENT_PROOF_INVALID", 401)])
def test_unavailable_provider_client_is_not_reported_as_bad_password(upstream, status):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"code": upstream, "request_id": "client-invalid"}))
    with pytest.raises(IndiaError) as caught:
        run(IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=transport).call("login", payload={"username": "staff", "password": "synthetic"}))
    assert (caught.value.status_code, caught.value.code) == (503, "india_client_unavailable")


def test_refresh_401_still_expires_the_business_connection():
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"code": "SESSION_EXPIRED", "request_id": "refresh-expired"}))
    with pytest.raises(IndiaError) as caught:
        run(IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=transport).call("refresh", payload={"refresh_token": REFRESH}))
    assert (caught.value.status_code, caught.value.code) == (409, "india_auth_required")


@pytest.mark.parametrize("value", [{"user": USER}, {"access_token": ACCESS, "user": USER},
                                   {"access_token": ACCESS, "refresh_token": 123, "user": USER}])
def test_malformed_success_does_not_mean_saved_credentials_expired(value):
    with pytest.raises(IndiaError) as caught:
        normalize_login({"auth_method": "jjtv_client_password", "mfa_verified": False, **value})
    assert (caught.value.status_code, caught.value.code) == (502, "india_invalid_response")


def test_redirect_is_not_followed_and_cannot_leak_bearer():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://attacker.test/collect"})

    with pytest.raises(IndiaError, match="india_redirect_rejected"):
        run(IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=httpx.MockTransport(handler)).call("me", access_token=ACCESS))
    assert seen == [BASE + "/me"]


@pytest.mark.parametrize("base", ["", "http://india.test", "https://user:password@india.test", "https://india.test/v2", "https://india.test/?next=evil", "https://india.test/#fragment"])
def test_only_a_server_configured_https_origin_or_v1_base_is_allowed(base):
    with pytest.raises(IndiaError, match="india_not_configured"):
        normalize_api_base(base)


def test_bad_action_ids_queries_and_headers_never_reach_network():
    def forbidden(request):
        pytest.fail("Invalid caller input reached the upstream")

    client = IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=httpx.MockTransport(forbidden))
    for action, options in [
        ("https://attacker.test", {"access_token": ACCESS}),
        ("series_detail", {"access_token": ACCESS, "resource_id": "../auth/logout"}),
        ("series_detail", {"access_token": ACCESS, "resource_id": "x%2Fcontent"}),
        ("series_detail", {"access_token": ACCESS, "resource_id": "x?token=evil"}),
        ("series_list", {"access_token": ACCESS, "params": {"url": "https://attacker.test"}}),
        ("me", {"access_token": ACCESS + "\r\nInjected: true"}),
        ("me", {}),
    ]:
        with pytest.raises(IndiaError):
            run(client.call(action, **options))


def test_each_operator_supplies_its_own_business_credential():
    observed = []

    def handler(request):
        observed.append(request.headers["authorization"])
        return reply(USER)

    client = IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=httpx.MockTransport(handler))
    run(client.call("me", access_token="a" * 43))
    run(client.call("me", access_token="b" * 43))
    assert observed == ["Bearer " + "a" * 43, "Bearer " + "b" * 43]
    with pytest.raises(IndiaError, match="india_staff_required"):
        normalize_staff({"id": "user_viewer", "name": "Viewer", "role": "viewer"})


def test_raw_video_is_streamed_in_bounded_chunks_and_keeps_exact_contract():
    source_started = []
    observed = {}
    body = b"video-content" * 15000

    async def chunks():
        source_started.append(True)
        yield body[:100000]
        yield body[100000:]

    class StreamingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            assert source_started == []  # No preloading before sending the request.
            assert request.url.path == "/v1/admin/uploads/upload_1/content"
            assert request.headers["content-type"] == "video/mp4"
            assert request.headers["content-length"] == str(len(body))
            assert request.headers["authorization"] == "Bearer " + ACCESS
            digest, count, largest = hashlib.sha256(), 0, 0
            async for chunk in request.stream:
                digest.update(chunk); count += len(chunk); largest = max(largest, len(chunk))
            observed.update(size=count, largest=largest)
            return reply({"upload_id": "upload_1", "status": "received", "size_bytes": count, "sha256": digest.hexdigest()})

    result = run(IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=StreamingTransport()).upload(
        "video", "upload_1", access_token=ACCESS, chunks=chunks(), size=len(body), content_type="video/mp4"))
    assert result.data["sha256"] == hashlib.sha256(body).hexdigest()
    assert observed == {"size": len(body), "largest": 65536}


def test_upload_reservation_completion_rights_cover_and_subtitles_contracts():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content)))
        if request.url.path == "/v1/admin/episodes/episode_1/uploads":
            return reply({"upload_id": "upload_1", "upload_url": "/v1/admin/uploads/upload_1/content", "max_bytes": MAX_UPLOAD_BYTES,
                          "allowed_content_types": ["video/mp4", "video/quicktime", "video/webm", "video/x-matroska"]}, status=201)
        if request.url.path.endswith("/complete"):
            return reply({"id": "job_1", "type": "media_transcode", "status": "pending"})
        return reply({"id": "evidence_1", "status": "created"}, status=201)

    client = IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=httpx.MockTransport(handler))
    operations = [
        ("episode_upload_init", "episode_1", {"filename": "episode.mp4", "size_bytes": 4096, "content_type": "video/mp4"}),
        ("upload_complete", "upload_1", {"sha256": "f" * 64}),
        ("rights_upload_init", "series_1", {"filename": "license.pdf", "size_bytes": 1000, "content_type": "application/pdf", "document_version": "v1"}),
        ("cover_upload_init", "series_1", {"filename": "cover.png", "size_bytes": 1000, "content_type": "image/png"}),
        ("subtitles", "episode_1", {"availability": "not_provided", "reason": "No original subtitles supplied"}),
    ]
    for action, resource, payload in operations:
        run(client.call(action, resource_id=resource, access_token=ACCESS, payload=payload))
    assert [row[1] for row in seen] == ["/v1/admin/episodes/episode_1/uploads", "/v1/admin/uploads/upload_1/complete",
                                         "/v1/admin/series/series_1/rights/evidence", "/v1/admin/series/series_1/cover/uploads",
                                         "/v1/admin/episodes/episode_1/subtitles"]
    assert [row[2] for row in seen] == [item[2] for item in operations]


@pytest.mark.parametrize("kind,size", [("video", MAX_UPLOAD_BYTES + 1), ("rights", 10 * 1024 * 1024 + 1), ("cover", 5 * 1024 * 1024 + 1)])
def test_oversized_upload_is_rejected_before_network_or_source_read(kind, size):
    async def chunks():
        pytest.fail("Oversized file should not be read")
        yield b""

    def forbidden(request):
        pytest.fail("Oversized file reached network")

    with pytest.raises(IndiaError, match="india_upload_too_large"):
        run(IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=httpx.MockTransport(forbidden)).upload(
            kind, "upload_1", access_token=ACCESS, chunks=chunks(), size=size, content_type="application/octet-stream"))


@pytest.mark.parametrize("body,declared", [(b"too long", 3), (b"short", 10)])
def test_actual_upload_length_must_match_the_reserved_length(body, declared):
    async def chunks():
        yield body

    class ConsumingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            async for _ in request.stream:
                pass
            return reply({"status": "received"})

    with pytest.raises(IndiaError, match="india_upload_size_mismatch"):
        run(IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=ConsumingTransport()).upload(
            "video", "upload_1", access_token=ACCESS, chunks=chunks(), size=declared, content_type="video/mp4"))


def test_timeout_and_malformed_success_fail_closed_without_response_text():
    def timeout(request):
        raise httpx.ReadTimeout("credential-secret must not be surfaced", request=request)

    with pytest.raises(IndiaError) as caught:
        run(IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=httpx.MockTransport(timeout)).call("me", access_token=ACCESS))
    assert caught.value.code == "india_timeout" and caught.value.status_code == 504
    assert "credential-secret" not in str(caught.value)
    for body in [{"user": USER}, {"data": USER}, "secret-password"]:
        with pytest.raises(IndiaError, match="india_invalid_response"):
            run(IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))).call("me", access_token=ACCESS))


def test_mutation_idempotency_header_and_connector_identity_are_explicit():
    operation = "123e4567-e89b-42d3-a456-426614174000"
    seen = []

    def handler(request):
        seen.append(request.method)
        assert request.headers["idempotency-key"] == operation
        assert request.headers["user-agent"] == "JJTV-India-Connector/2.0"
        return reply({"id": "series_1", "version": 2})

    client = IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=httpx.MockTransport(handler))
    run(client.call("series_edit", access_token=ACCESS, resource_id="series_1", payload={"version": 1}, idempotency_key=operation.upper()))

    async def chunks():
        yield b"video"

    run(client.upload("video", "upload_1", access_token=ACCESS, chunks=chunks(), size=5,
                      content_type="video/mp4", idempotency_key=operation))
    assert seen == ["PATCH", "PUT"]
    for invalid in ["not-a-uuid", operation + "\r\nInjected: true", operation.replace("-", "")]:
        with pytest.raises(IndiaError, match="india_invalid_idempotency_key"):
            run(client.call("series_create", access_token=ACCESS, payload={}, idempotency_key=invalid))
    assert len(seen) == 2


@pytest.mark.parametrize("value", [
    {"mfa_required": True, "challenge_token": "c" * 43},
    {"access_token": ACCESS, "refresh_token": REFRESH, "user": USER},
    provider_tokens(user=USER, auth_method="first_party_password"),
    provider_tokens(user=USER, mfa_verified=True),
])
def test_web_mfa_tokens_or_challenges_cannot_be_accepted_as_client_authorization(value):
    with pytest.raises(IndiaError) as caught:
        normalize_login(value)
    assert (caught.value.status_code, caught.value.code) == (502, "india_client_contract_invalid")


@pytest.mark.parametrize("enabled", [None, "false", "1", ""])
def test_client_is_default_off_even_with_a_key_file(monkeypatch, enabled):
    if enabled is None: monkeypatch.delenv("INDIA_JJTV_CLIENT_ENABLED", raising=False)
    else: monkeypatch.setenv("INDIA_JJTV_CLIENT_ENABLED", enabled)
    with pytest.raises(IndiaError) as caught:
        IndiaClient(operator_id=OPERATOR, base_url=BASE)
    assert (caught.value.code, caught.value.status_code) == ("india_client_unavailable", 503)


@pytest.mark.parametrize("key_kind", ["missing", "relative", "directory", "empty", "invalid", "oversized"])
def test_missing_or_invalid_server_key_fails_closed(monkeypatch, tmp_path, key_kind):
    path = tmp_path / "invalid-key"
    if key_kind == "directory": path.mkdir()
    elif key_kind == "empty": path.write_text("")
    elif key_kind == "invalid": path.write_text("x" * 42)
    elif key_kind == "oversized": path.write_text("x" * 257)
    monkeypatch.setenv("INDIA_JJTV_CLIENT_SECRET_FILE", "relative-key" if key_kind == "relative" else str(path))
    with pytest.raises(IndiaError, match="india_client_unavailable"):
        IndiaClient(operator_id=OPERATOR, base_url=BASE)


def test_operator_identity_and_client_secret_headers_cannot_be_overridden():
    observed = []
    def handler(request):
        assert_client_headers(request)
        observed.append(request)
        return reply(USER)
    client = IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=httpx.MockTransport(handler))
    run(client._send("GET", "me", access_token=ACCESS, headers={
        "X-JJTV-Client-Id": "attacker", "X-JJTV-Client-Secret": "browser-supplied-secret",
        "X-JJTV-Operator-Id": "another-user"}))
    assert len(observed) == 1


@pytest.mark.parametrize("operator", ["", "bad\r\nHeader: x", "operator/other", "x" * 161])
def test_invalid_operator_identity_fails_before_network(operator):
    with pytest.raises(IndiaError, match="india_invalid_resource"):
        IndiaClient(operator_id=operator, base_url=BASE)


def test_client_key_rotation_changes_fingerprint_without_exposing_key(client_key):
    first = client_fingerprint()
    client_key.write_text("rotated_synthetic_key_" + "n" * 43, encoding="ascii")
    second = client_fingerprint()
    assert first != second and len(second) == 64
    assert CLIENT_SECRET not in first + second


@pytest.mark.parametrize("action", ["mfa_verify", "mfa_enrollment", "mfa_cancel"])
def test_retired_mfa_actions_cannot_reach_upstream(action):
    def forbidden(request):
        pytest.fail("Retired MFA request reached upstream")
    with pytest.raises(IndiaError, match="india_action_not_allowed"):
        run(IndiaClient(operator_id=OPERATOR, base_url=BASE, transport=httpx.MockTransport(forbidden)).call(action, payload={}))


@pytest.mark.parametrize("field", ["expires_at", "refresh_expires_at"])
@pytest.mark.parametrize("expiry", [None, 123, "not-a-date", "2099-01-01T00:15:00"])
def test_client_tokens_require_explicit_timezone_aware_expiries(field, expiry):
    value = provider_tokens(user=USER)
    if expiry is None: value.pop(field)
    else: value[field] = expiry
    with pytest.raises(IndiaError) as caught:
        normalize_login(value)
    assert (caught.value.code, caught.value.status_code) == ("india_invalid_response", 502)


@pytest.mark.parametrize("value", [None, [], "unexpected", {"user": {**USER, "id": "user_different"}}])
def test_malformed_or_changed_refresh_actor_is_not_saved(value):
    if isinstance(value, dict):
        value = provider_tokens(**value)
    with pytest.raises(IndiaError, match="india_invalid_response"):
        normalize_refresh(value, USER)


def test_refresh_without_user_retains_the_verified_physical_actor():
    assert normalize_refresh(provider_tokens(), USER)["user"] == normalize_staff(USER)
