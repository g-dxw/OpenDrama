"""Tenant boundaries and the India BFF, using the dedicated test DB/Redis fixtures.

India HTTP is always mocked. These tests do not call or migrate the old system.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.main import app
from app.modules.common.models import AuditLog
from app.modules.content.models import Drama, DramaTag, Episode, MediaUpload, Tag
from app.modules.tenancy.india import IndiaError, IndiaReply
from app.modules.tenancy.models import DEFAULT_TENANT, INDIA_TENANT, IndiaConnection, Tenant, TenantMember

TENANTS = "/admin/v1/tenants"
INDIA = TENANTS + "/india-short-drama/india"
ACCESS_A, ACCESS_B = "a" * 43, "b" * 43
REFRESH = "r" * 43
CHALLENGE = "c" * 43
OLD_ACTOR = {"id": "user_old-editor", "name": "Old content operator", "role": "editor", "age_verified": False}


def login(client, staff):
    response = client.post("/admin/v1/auth/login", json={"username": staff.username, "password": "pass-1234"})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def scoped(headers, tenant):
    return {**headers, "X-JJTV-Tenant": tenant}


@pytest.fixture()
def context(session, make, monkeypatch):
    # The shared fixture truncates all tables, including migration seed rows.
    for identifier, name, kind in [(DEFAULT_TENANT, "JJTV", "native"), (INDIA_TENANT, "India Short Drama", "india_legacy")]:
        if session.get(Tenant, identifier) is None:
            session.add(Tenant(id=identifier, name=name, kind=kind, status="active"))
    session.commit()
    owner, operator, finance = make.admin(role="admin"), make.admin(role="ops"), make.admin(role="finance")
    for person in (operator, finance):
        if session.get(TenantMember, (DEFAULT_TENANT, person.id)) is None:
            session.add(TenantMember(tenant_id=DEFAULT_TENANT, user_id=person.id, role=person.role))
    session.commit()
    key = Fernet.generate_key()
    monkeypatch.setenv("TENANT_CONNECTION_KEY", key.decode())
    monkeypatch.setenv("INDIA_API_BASE_URL", "https://india.example/v1")
    client = TestClient(app)
    return {"db": session, "make": make, "client": client, "owner": owner, "operator": operator,
            "finance": finance, "owner_headers": login(client, owner), "operator_headers": login(client, operator),
            "finance_headers": login(client, finance), "cipher": Fernet(key)}


def native(context, name="Another studio"):
    response = context["client"].post(TENANTS, json={"name": name}, headers=context["owner_headers"])
    assert response.status_code in (200, 201), response.text
    result = response.json()
    assert result["kind"] == "native" and result["status"] == "active"
    return result["id"]


def grant(context, tenant, *members):
    response = context["client"].put(TENANTS + f"/{tenant}/members", json={"members": [
        {"user_id": user.id, "role": role} for user, role in members
    ]}, headers=context["owner_headers"])
    assert response.status_code == 200, response.text
    return response.json()


def create_drama(context, tenant, title):
    response = context["client"].post("/admin/v1/dramas", json={"title": title}, headers=scoped(context["owner_headers"], tenant))
    assert response.status_code == 200, response.text
    return response.json()


def create_episode(context, tenant, drama_id):
    response = context["client"].post(f"/admin/v1/dramas/{drama_id}/episodes", json={"episode_number": 1, "title": "Episode 1"},
                                       headers=scoped(context["owner_headers"], tenant))
    assert response.status_code == 200, response.text
    return response.json()


def test_directory_own_role_and_explicit_membership(context):
    client = context["client"]
    other = native(context)
    owner_rows = client.get(TENANTS, headers=context["owner_headers"])
    assert owner_rows.status_code == 200
    assert isinstance(owner_rows.json(), list)
    assert {row["id"] for row in owner_rows.json()} >= {DEFAULT_TENANT, INDIA_TENANT, other}
    assert all(row["own_role"] == "admin" for row in owner_rows.json())
    rows = client.get(TENANTS, headers=context["operator_headers"]).json()
    assert {row["id"] for row in rows} == {DEFAULT_TENANT}
    grant(context, other, (context["operator"], "finance"))
    rows = client.get(TENANTS, headers=context["operator_headers"]).json()
    assert next(row for row in rows if row["id"] == other)["own_role"] == "finance"
    grant(context, other, (context["finance"], "admin"))
    rows = client.get(TENANTS, headers=context["finance_headers"]).json()
    assert next(row for row in rows if row["id"] == other)["own_role"] == "finance"  # No global-role elevation.


def test_only_platform_admin_manages_directory_and_india_kind_is_fixed(context):
    client, owner = context["client"], context["owner_headers"]
    assert client.post(TENANTS, json={"name": "Forbidden"}, headers=context["operator_headers"]).status_code == 403
    other = native(context)
    assert client.patch(TENANTS + f"/{other}", json={"status": "paused"}, headers=owner).status_code == 200
    assert client.get("/admin/v1/dramas", headers=scoped(owner, other)).status_code == 409
    assert client.patch(TENANTS + f"/{other}", json={"status": "active"}, headers=owner).status_code == 200
    assert client.get("/admin/v1/dramas", headers=scoped(owner, other)).status_code == 200
    assert client.patch(TENANTS + "/" + INDIA_TENANT, json={"kind": "native"}, headers=owner).status_code == 422
    context["db"].expire_all()
    assert context["db"].get(Tenant, INDIA_TENANT).kind == "india_legacy"


def test_member_replace_revokes_default_jjtv_access_and_cannot_enable_India_native_routes(context):
    client, owner = context["client"], context["owner_headers"]
    response = client.get(TENANTS + "/jjtv/members", headers=owner)
    assert response.status_code == 200
    assert set(response.json()) >= {"members", "staff"}
    assert any(row["user_id"] == context["operator"].id for row in response.json()["members"])
    assert client.put(TENANTS + "/jjtv/members", json={"members": []}, headers=context["operator_headers"]).status_code == 403
    grant(context, DEFAULT_TENANT)
    assert client.get("/admin/v1/dramas", headers=context["operator_headers"]).status_code == 403
    assert DEFAULT_TENANT not in {row["id"] for row in client.get(TENANTS, headers=context["operator_headers"]).json()}
    grant(context, INDIA_TENANT, (context["operator"], "ops"))
    denied = client.get("/admin/v1/dramas", headers=scoped(context["operator_headers"], INDIA_TENANT))
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "tenant_content_route_required"


def test_native_drama_episode_tag_and_upload_are_isolated_by_header(context):
    client, db = context["client"], context["db"]
    first, second = native(context, "Studio A"), native(context, "Studio B")
    a = create_drama(context, first, "Same tenant-local title")
    b = create_drama(context, second, "Same tenant-local title")
    ep = create_episode(context, first, a["id"])
    tags = []
    for tenant in (first, second):
        response = client.post("/admin/v1/tags", json={"name_en": "Same local tag"}, headers=scoped(context["owner_headers"], tenant))
        assert response.status_code in (200, 201), response.text
        tags.append(response.json())
    headers_b = scoped(context["owner_headers"], second)
    assert {row["id"] for row in client.get("/admin/v1/dramas", headers=headers_b).json()["items"]} == {b["id"]}
    assert {row["id"] for row in client.get("/admin/v1/tags", headers=headers_b).json()["items"]} == {tags[1]["id"]}
    for method, path, body in [
        ("GET", f"/admin/v1/dramas/{a['id']}", None),
        ("PATCH", f"/admin/v1/dramas/{a['id']}", {"title": "Must not change"}),
        ("GET", f"/admin/v1/dramas/{a['id']}/episodes", None),
        ("POST", f"/admin/v1/dramas/{a['id']}/episodes", {"episode_number": 2}),
        ("PATCH", f"/admin/v1/episodes/{ep['id']}", {"title": "Must not change"}),
        ("PATCH", f"/admin/v1/tags/{tags[0]['id']}", {"name_en": "Must not change"}),
    ]:
        kwargs = {"headers": headers_b}
        if body is not None: kwargs["json"] = body
        response = client.request(method, path, **kwargs)
        assert response.status_code == 404, (method, path, response.status_code, response.text)
    # A valid local drama must not be linked to another tenant's valid tag ID.
    response = client.patch(f"/admin/v1/dramas/{b['id']}", json={"tag_ids": [tags[0]["id"]]}, headers=headers_b)
    assert response.status_code in (404, 422)
    upload = client.post(f"/admin/v1/episodes/{ep['id']}/upload-session", headers=scoped(context["owner_headers"], first),
                         json={"filename": "episode.mp4", "mime": "video/mp4", "size_bytes": 16, "width": 720, "height": 1280, "duration_seconds": 12})
    assert upload.status_code == 200, upload.text
    video_id = upload.json()["video_id"]
    assert client.head(f"/admin/v1/dev-tus/{video_id}", headers=headers_b).status_code == 404
    assert client.patch(f"/admin/v1/dev-tus/{video_id}", content=b"must-not-write", headers=headers_b).status_code == 404
    db.expire_all()
    assert db.get(Drama, a["id"]).title == "Same tenant-local title"
    assert db.get(Episode, ep["id"]).title == "Episode 1"
    assert db.scalar(select(DramaTag).where(DramaTag.drama_id == b["id"])) is None
    assert db.scalar(select(MediaUpload).where(MediaUpload.video_id == video_id)).status != "completed"


def test_public_catalog_and_playback_always_use_jjtv_even_with_tenant_header(context):
    db, client = context["db"], context["client"]
    other = native(context)
    default = Drama(tenant_id=DEFAULT_TENANT, title="Default public show", status="published", free_episode_count=1)
    hidden = Drama(tenant_id=other, title="Other tenant public-looking show", status="published", free_episode_count=1)
    db.add_all([default, hidden]); db.flush()
    episodes = [Episode(drama_id=row.id, episode_number=1, title="First", status="published", bunny_video_id="fake-" + str(row.id)) for row in (default, hidden)]
    db.add_all(episodes); db.commit()
    headers = {"X-JJTV-Tenant": other}
    home = client.get("/v1/home", headers=headers)
    assert home.status_code == 200
    assert {row["id"] for row in home.json()["items"]} == {default.id}
    assert client.get(f"/v1/dramas/{hidden.id}", headers=headers).status_code == 404
    assert client.get(f"/v1/preview/dramas/{hidden.id}", headers=headers).status_code == 404
    playback = client.post("/v1/playback", json={"drama_id": hidden.id, "episode_id": episodes[1].id}, headers=headers)
    assert playback.status_code in (200, 403, 404)
    if playback.status_code == 200:
        assert playback.json().get("allowed") is False and not playback.json().get("url")


@pytest.mark.parametrize("path", ["/admin/v1/finance/orders", "/admin/v1/finance/ledger", "/admin/v1/staff"])
def test_nondefault_native_tenant_cannot_enter_global_finance_or_staff(context, path):
    other = native(context)
    response = context["client"].get(path, headers=scoped(context["owner_headers"], other))
    assert response.status_code == 403, response.text


@pytest.fixture()
def india_mock(monkeypatch):
    from app.modules.tenancy import router as tenant_router
    observed = []
    state = {"challenge": False, "expired": False, "enrollment": False}

    class FakeIndiaClient:
        def __init__(self, **kwargs):
            pass

        async def call(self, action, *, access_token=None, resource_id=None, payload=None, params=None, idempotency_key=None):
            observed.append({"action": action, "access_token": access_token, "resource_id": resource_id,
                             "payload": payload, "params": params, "idempotency_key": idempotency_key})
            if action == "configuration": return IndiaReply({"authentication": {"identity_mode": "first_party", "admin_password_enabled": True}}, "old-config")
            if action == "login":
                if state["challenge"]:
                    return IndiaReply({"mfa_required": True, "challenge_token": CHALLENGE, "expires_at": "2099-01-01T00:00:00Z", "enrollment_required": state["enrollment"]}, "old-login")
                token = ACCESS_B if payload["username"] == "old-second" else ACCESS_A
                return IndiaReply({"access_token": token, "refresh_token": REFRESH, "user": OLD_ACTOR, "environment": "production"}, "old-login")
            if action == "mfa_verify":
                assert payload == {"challenge_token": CHALLENGE, "code": "123456"}
                return IndiaReply({"access_token": ACCESS_A, "refresh_token": REFRESH, "user": OLD_ACTOR, "environment": "production"}, "old-mfa")
            if action == "mfa_enrollment": return IndiaReply({"secret_base32": "A" * 32, "otpauth_uri": "otpauth://totp/8JJ:old?secret=" + "A" * 32, "expires_at": "2099-01-01T00:00:00Z"}, "old-enrollment")
            if action == "mfa_cancel": return IndiaReply({"cancelled": True}, "old-cancel")
            if action == "refresh":
                assert payload == {"refresh_token": REFRESH}
                return IndiaReply({"access_token": "n" * 43, "refresh_token": "s" * 43}, "old-refresh")
            if action == "me":
                if state["expired"]: raise IndiaError("india_auth_required", 409, upstream_code="SESSION_EXPIRED", request_id="old-expired")
                assert access_token in {ACCESS_A, ACCESS_B, "n" * 43}
                return IndiaReply(OLD_ACTOR, "old-me")
            if action == "series_list":
                return IndiaReply({"items": [{"id": "series_existing", "title": "Old series", "version": 3}], "next_cursor": None}, "old-list")
            if action == "series_create":
                return IndiaReply({"id": "series_new", "title": payload["title"], "version": 1}, "old-create")
            return IndiaReply({"id": resource_id, "version": 4}, "old-action")

    monkeypatch.setattr(tenant_router, "IndiaClient", FakeIndiaClient)
    return observed, state


def connect(context, headers=None, username="old-existing"):
    response = context["client"].post(INDIA + "/session", json={"username": username, "password": "old-password-not-stored"},
                                       headers=headers or context["owner_headers"])
    assert response.status_code == 200, response.text
    assert response.json()["connected"] is True
    assert ACCESS_A not in response.text and REFRESH not in response.text
    return response


def test_india_connection_is_encrypted_and_bound_to_staff_and_JJTV_bearer(context, india_mock):
    client, db = context["client"], context["db"]
    grant(context, INDIA_TENANT, (context["operator"], "ops"))
    headers = context["operator_headers"]
    connect(context, headers)
    row = db.scalar(select(IndiaConnection).where(IndiaConnection.user_id == context["operator"].id))
    assert row.tenant_id == INDIA_TENANT
    assert row.session_key == hashlib.sha256(headers["Authorization"].encode()).hexdigest()
    saved_session_key = row.session_key
    assert ACCESS_A not in row.secret and REFRESH not in row.secret and "old-password" not in row.secret
    decrypted = context["cipher"].decrypt(row.secret.encode()).decode()
    assert ACCESS_A in decrypted and REFRESH in decrypted
    assert "old-password-not-stored" not in decrypted
    other_jjtv_session = login(client, context["operator"])
    assert other_jjtv_session != headers
    assert client.get(INDIA + "/session", headers=other_jjtv_session).json()["connected"] is False
    assert client.get(INDIA + "/session", headers=context["owner_headers"]).json()["connected"] is False
    assert client.get(INDIA + "/session", headers=headers).json()["connected"] is True
    assert client.delete(INDIA + "/session", headers=headers).status_code in (200, 204)
    db.expire_all()
    assert db.get(IndiaConnection, (INDIA_TENANT, context["operator"].id, saved_session_key)) is None
    assert not any(call["action"] == "logout" for call in india_mock[0])  # Disconnect is local, not account-wide logout.


def test_india_mfa_accepts_only_current_bound_challenge_and_never_stores_otp(context, india_mock):
    observed, state = india_mock
    state["challenge"] = True
    client, headers, db = context["client"], context["owner_headers"], context["db"]
    pending = client.post(INDIA + "/session", json={"username": "old-existing", "password": "old-password-not-stored"}, headers=headers)
    assert pending.status_code == 200
    assert pending.json()["mfa_required"] is True and pending.json()["connected"] is False
    assert pending.json()["challenge_token"] == CHALLENGE
    count = len(observed)
    rejected = client.post(INDIA + "/session/mfa", json={"challenge_token": "x" * 43, "code": "123456"}, headers=headers)
    assert rejected.status_code in (400, 409)
    assert not any(call["action"] == "mfa_verify" for call in observed[count:])
    different_session = login(client, context["owner"])
    rejected = client.post(INDIA + "/session/mfa", json={"challenge_token": CHALLENGE, "code": "123456"}, headers=different_session)
    assert rejected.status_code in (400, 409)
    accepted = client.post(INDIA + "/session/mfa", json={"challenge_token": CHALLENGE, "code": "123456"}, headers=headers)
    assert accepted.status_code == 200 and accepted.json()["connected"] is True
    row = db.scalar(select(IndiaConnection).where(IndiaConnection.user_id == context["owner"].id))
    decrypted = context["cipher"].decrypt(row.secret.encode()).decode()
    assert "123456" not in decrypted and "old-password-not-stored" not in decrypted and CHALLENGE not in decrypted
    assert client.post(INDIA + "/session/mfa", json={"challenge_token": CHALLENGE, "code": "123456"}, headers=headers).status_code in (400, 409)


def test_india_refresh_rotates_saved_credentials_and_old_401_does_not_log_out_JJTV(context, india_mock):
    client, headers, db = context["client"], context["owner_headers"], context["db"]
    connect(context)
    refreshed = client.post(INDIA + "/session/refresh", json={}, headers=headers)
    assert refreshed.status_code == 200 and refreshed.json()["connected"] is True
    db.expire_all()
    row = db.scalar(select(IndiaConnection).where(IndiaConnection.user_id == context["owner"].id))
    stored = context["cipher"].decrypt(row.secret.encode()).decode()
    assert "n" * 43 in stored and "s" * 43 in stored and ACCESS_A not in stored
    india_mock[1]["expired"] = True
    expired = client.get(INDIA + "/admin/series", headers=headers)
    assert expired.status_code == 409, expired.text
    assert expired.json()["detail"]["code"] == "india_auth_required"
    assert client.get("/admin/v1/auth/me", headers=headers).status_code == 200


def test_india_fixed_proxy_preserves_envelope_requires_UUID_and_audits_real_actor(context, india_mock):
    client, headers, db = context["client"], context["owner_headers"], context["db"]
    connect(context)
    listing = client.get(INDIA + "/admin/series?q=Old&limit=30", headers=headers)
    assert listing.status_code == 200
    assert listing.json() == {"data": {"items": [{"id": "series_existing", "title": "Old series", "version": 3}], "next_cursor": None}, "request_id": "old-list"}
    seen = len(india_mock[0])
    assert client.get(INDIA + "/admin/orders", headers=headers).status_code == 404
    assert all(call["action"] in {"me", "configuration"} for call in india_mock[0][seen:])
    payload = {"title": "New upstream draft", "synopsis": "Text", "genre": "Drama", "language": "hi", "content_origin": "live_action", "age_rating": "U", "rights": None}
    assert client.post(INDIA + "/admin/series", json=payload, headers=headers).status_code in (400, 422)
    operation = str(uuid4())
    created = client.post(INDIA + "/admin/series", json=payload, headers={**headers, "Idempotency-Key": operation})
    assert created.status_code in (200, 201), created.text
    forwarded = [call for call in india_mock[0] if call["action"] == "series_create"]
    assert len(forwarded) == 1 and forwarded[0]["payload"] == payload and forwarded[0]["idempotency_key"] == operation
    db.expire_all()
    audits = db.scalars(select(AuditLog).where(AuditLog.actor_id == context["owner"].id)).all()
    assert any(row.payload.get("tenant_id") == INDIA_TENANT and row.payload.get("upstream_actor_id") == OLD_ACTOR["id"]
               and row.payload.get("upstream_request_id") == "old-create" for row in audits)
    assert all("old-password-not-stored" not in json.dumps(row.payload) and ACCESS_A not in json.dumps(row.payload) for row in audits)


def test_india_mfa_cancel_and_enrollment_are_bound_and_no_store(context, india_mock):
    client, headers, db = context["client"], context["owner_headers"], context["db"]
    india_mock[1].update(challenge=True, enrollment=True)
    pending = client.post(INDIA + "/session", json={"username": "old-existing", "password": "old-password-not-stored"}, headers=headers)
    assert pending.status_code == 200 and pending.json()["enrollment_required"] is True
    assert pending.headers["cache-control"] == "no-store"
    another = login(client, context["owner"])
    denied = client.post(INDIA + "/session/enrollment", json={"challenge_token": CHALLENGE}, headers=another)
    assert denied.status_code == 409
    enrollment = client.post(INDIA + "/session/enrollment", json={"challenge_token": CHALLENGE}, headers=headers)
    assert enrollment.status_code == 200 and enrollment.json()["data"]["secret_base32"] == "A" * 32
    assert enrollment.headers["cache-control"] == "no-store"
    row = db.scalar(select(IndiaConnection).where(IndiaConnection.user_id == context["owner"].id))
    decrypted = context["cipher"].decrypt(row.secret.encode()).decode()
    assert "A" * 32 not in decrypted and "otpauth://" not in decrypted and "old-password-not-stored" not in decrypted
    cancelled = client.post(INDIA + "/session/cancel", json={"challenge_token": CHALLENGE}, headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["connected"] is False
    assert client.get(INDIA + "/session", headers=headers).json()["state"] == "disconnected"
    assert client.post(INDIA + "/session/mfa", json={"challenge_token": CHALLENGE, "code": "123456"}, headers=headers).status_code == 409


def test_india_tenant_readonly_and_pause_revoke_write_and_connection(context, india_mock):
    client, owner, db = context["client"], context["owner_headers"], context["db"]
    assert client.get(INDIA + "/session", headers=context["operator_headers"]).status_code == 403
    grant(context, INDIA_TENANT, (context["operator"], "finance"))
    connect(context, context["operator_headers"])
    assert client.get(INDIA + "/admin/series", headers=context["operator_headers"]).status_code == 200
    before = len(india_mock[0])
    denied = client.post(INDIA + "/admin/series", json={"title": "Forbidden write"},
                         headers={**context["operator_headers"], "Idempotency-Key": str(uuid4())})
    assert denied.status_code == 403
    assert not any(call["action"] == "series_create" for call in india_mock[0][before:])
    assert client.patch(TENANTS + "/" + INDIA_TENANT, json={"status": "paused"}, headers=owner).status_code == 200
    assert client.get(INDIA + "/admin/series", headers=context["operator_headers"]).status_code == 409
    db.expire_all()
    assert db.scalar(select(IndiaConnection).where(IndiaConnection.user_id == context["operator"].id)) is None
    assert client.patch(TENANTS + "/" + INDIA_TENANT, json={"status": "active"}, headers=owner).status_code == 200
    assert client.get(INDIA + "/session", headers=context["operator_headers"]).json()["connected"] is False


def test_india_authorization_mutex_reentry_and_overlapping_refresh_fail_before_upstream(context, india_mock):
    from fastapi import HTTPException, Request
    from app.modules.tenancy import service as tenant_service

    client, headers, user = context["client"], context["owner_headers"], context["owner"]
    connect(context)
    request = Request({"type": "http", "method": "POST", "path": INDIA + "/session/refresh",
                       "headers": [(b"authorization", headers["Authorization"].encode())]})
    before = len(india_mock[0])
    # Model the first worker holding this same operator/browser authorization lease.
    with tenant_service.connection_mutation(request, user):
        with pytest.raises(HTTPException) as reentered:
            with tenant_service.connection_mutation(request, user):
                pytest.fail("A second mutation acquired the current connection lease")
        assert reentered.value.status_code == 409
        assert reentered.value.detail["code"] == "india_connection_busy"
        overlapping = client.post(INDIA + "/session/refresh", json={}, headers=headers)
        assert overlapping.status_code == 409
        assert overlapping.json()["detail"]["code"] == "india_connection_busy"
        assert not any(call["action"] == "refresh" for call in india_mock[0][before:])
    # Release must make a subsequent intentional refresh usable again.
    refreshed = client.post(INDIA + "/session/refresh", json={}, headers=headers)
    assert refreshed.status_code == 200 and refreshed.json()["connected"] is True
    assert len([call for call in india_mock[0][before:] if call["action"] == "refresh"]) == 1


def test_old_India_401_does_not_delete_a_concurrently_rotated_connection(context, india_mock, monkeypatch):
    from sqlalchemy.orm import Session
    from app.modules.tenancy import router as tenant_router

    client, headers, db = context["client"], context["owner_headers"], context["db"]
    connect(context)
    session_key = hashlib.sha256(headers["Authorization"].encode()).hexdigest()
    primary_key = (INDIA_TENANT, context["owner"].id, session_key)
    original_call = tenant_router.IndiaClient.call
    rotated_ciphertexts = []

    async def rotate_then_expire(self, action, **kwargs):
        if action != "series_list":
            return await original_call(self, action, **kwargs)
        assert kwargs["access_token"] == ACCESS_A
        # A separate request commits new credentials while the old request awaits HTTP.
        with Session(db.get_bind(), expire_on_commit=False) as concurrent:
            row = concurrent.get(IndiaConnection, primary_key)
            assert row is not None
            data = json.loads(context["cipher"].decrypt(row.secret.encode()).decode())
            data.update(access_token="n" * 43, refresh_token="s" * 43)
            row.secret = context["cipher"].encrypt(json.dumps(data).encode()).decode()
            rotated_ciphertexts.append(row.secret)
            concurrent.commit()
        raise IndiaError("india_auth_required", 409, upstream_code="SESSION_EXPIRED", request_id="old-inflight-401")

    monkeypatch.setattr(tenant_router.IndiaClient, "call", rotate_then_expire)
    response = client.get(INDIA + "/admin/series", headers=headers)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "india_auth_required"
    db.expire_all()
    remaining = db.get(IndiaConnection, primary_key)
    assert remaining is not None and remaining.secret == rotated_ciphertexts[0]
    stored = json.loads(context["cipher"].decrypt(remaining.secret.encode()).decode())
    assert stored["access_token"] == "n" * 43 and stored["refresh_token"] == "s" * 43
    assert client.get(INDIA + "/session", headers=headers).json()["connected"] is True
    assert client.get("/admin/v1/auth/me", headers=headers).status_code == 200


def test_corrupt_India_cipher_cleanup_does_not_delete_a_concurrent_replacement(context, india_mock, monkeypatch):
    from cryptography.fernet import InvalidToken
    from sqlalchemy.orm import Session
    from app.modules.tenancy import service as tenant_service

    client, headers, db = context["client"], context["owner_headers"], context["db"]
    connect(context)
    primary_key = (INDIA_TENANT, context["owner"].id, hashlib.sha256(headers["Authorization"].encode()).hexdigest())
    row = db.get(IndiaConnection, primary_key, populate_existing=True)
    replacement = json.loads(context["cipher"].decrypt(row.secret.encode()).decode())
    replacement.update(access_token="n" * 43, refresh_token="s" * 43)
    replacement_cipher = context["cipher"].encrypt(json.dumps(replacement).encode()).decode()
    row.secret = "corrupt-old-cipher"
    db.commit()
    raced = []
    original_cipher_factory = tenant_service.connection_cipher

    class RacingCipher:
        def decrypt(self, value):
            if value != b"corrupt-old-cipher":
                return context["cipher"].decrypt(value)
            assert not raced
            with Session(db.get_bind(), expire_on_commit=False) as concurrent:
                current = concurrent.get(IndiaConnection, primary_key)
                current.secret = replacement_cipher
                concurrent.commit()
            raced.append(True)
            raise InvalidToken()

    monkeypatch.setattr(tenant_service, "connection_cipher", lambda: RacingCipher())
    rejected = client.get(INDIA + "/session", headers=headers)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "india_auth_required"
    assert raced == [True]
    monkeypatch.setattr(tenant_service, "connection_cipher", original_cipher_factory)
    db.expire_all()
    remaining = db.get(IndiaConnection, primary_key, populate_existing=True)
    assert remaining is not None and remaining.secret == replacement_cipher
    assert client.get(INDIA + "/session", headers=headers).json()["connected"] is True


def test_failed_tenant_membership_audit_rolls_back_members_and_connection(context, india_mock):
    from sqlalchemy import event

    db, headers = context["db"], context["owner_headers"]
    operator = context["operator"]
    grant(context, INDIA_TENANT, (operator, "ops"))
    connect(context, context["operator_headers"])
    primary_key = (INDIA_TENANT, operator.id, hashlib.sha256(context["operator_headers"]["Authorization"].encode()).hexdigest())
    connection = db.get(IndiaConnection, primary_key, populate_existing=True)
    saved_cipher = connection.secret
    rejected_audits = []

    def reject_membership_audit(mapper, database_connection, target):
        if target.action == "tenant.members.update" and target.payload.get("tenant_id") == INDIA_TENANT:
            rejected_audits.append(target.action)
            raise RuntimeError("synthetic audit insert failure")

    event.listen(AuditLog, "before_insert", reject_membership_audit)
    try:
        failed_client = TestClient(app, raise_server_exceptions=False)
        response = failed_client.put(TENANTS + "/" + INDIA_TENANT + "/members", json={"members": []}, headers=headers)
        assert response.status_code >= 500
    finally:
        event.remove(AuditLog, "before_insert", reject_membership_audit)
    assert rejected_audits == ["tenant.members.update"]  # Failure occurred at the intended audit boundary.
    db.expire_all()
    member = db.get(TenantMember, (INDIA_TENANT, operator.id), populate_existing=True)
    remaining = db.get(IndiaConnection, primary_key, populate_existing=True)
    assert member is not None and member.role == "ops"
    assert remaining is not None and remaining.secret == saved_cipher
    assert context["client"].get(INDIA + "/session", headers=context["operator_headers"]).json()["connected"] is True
