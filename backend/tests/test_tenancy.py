"""Tenant-bound content and India authorization regressions.

All India HTTP calls are mocked. Tests never call or migrate the old system.
"""
from __future__ import annotations
import hashlib
import base64
import hmac
import json
from uuid import uuid4
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import app
from app.modules.common.models import AuditLog
from app.modules.content.models import Drama, DramaTag, Episode, MediaUpload, Tag
from app.modules.tenancy.india import IndiaClient, IndiaError, IndiaReply
from app.modules.tenancy.models import DEFAULT_TENANT, INDIA_TENANT, IndiaConnection, TenantMember, TenantRole
from app.modules.tenancy.permissions import preset_role_id

TENANTS = '/platform/v1/tenants'
INDIA = '/admin/v1/tenants/india-short-drama/india'
ACCESS_A, ACCESS_B = 'a' * 43, 'b' * 43
REFRESH = 'r' * 43
CHALLENGE = 'c' * 43
CLIENT_SECRET = 'synthetic_jjtv_client_key_' + 'k' * 43
OLD_ACTOR = {'id': 'user_old-editor', 'name': 'Old content operator', 'role': 'editor', 'age_verified': False}


def bridge_tokens(access=ACCESS_A, refresh=REFRESH, **extra):
    return {"access_token": access, "refresh_token": refresh, "auth_method": "jjtv_client_password",
            "mfa_verified": False, "expires_at": "2099-01-01T00:15:00.000Z",
            "refresh_expires_at": "2099-01-01T08:00:00.000Z", **extra}

def login(client, staff, tenant_code=None):
    body = dict(username=staff.username, password='pass-1234')
    path = '/platform/v1/auth/login' if staff.account_scope == 'platform' else '/tenant/v1/auth/login'
    if staff.account_scope == 'tenant': body['tenant_code'] = tenant_code or getattr(staff, '_test_tenant_code', staff.tenant_id)
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer '+response.json()['token']}

def scoped(headers, tenant):
    return {**headers, 'X-JJTV-Tenant': tenant}

@pytest.fixture()
def context(session, make, monkeypatch, tmp_path):
    owner = make.admin(role='admin', tenant_id=INDIA_TENANT)
    operator = make.admin(role='ops', tenant_id=INDIA_TENANT)
    finance = make.admin(role='finance', tenant_id=INDIA_TENANT)
    platform = make.platform_admin()
    # A login may exist without any business capability; connecting India still requires view permission.
    role = TenantRole(id='test-india-no-access', tenant_id=INDIA_TENANT, name='No content access', kind='business', permissions=[], version=1)
    session.add(role); session.flush()
    session.get(TenantMember, (INDIA_TENANT, operator.id)).role_id = role.id
    session.commit()
    key = Fernet.generate_key()
    monkeypatch.setenv('TENANT_CONNECTION_KEY', key.decode())
    monkeypatch.setenv('INDIA_API_BASE_URL', 'https://india.example/v1')
    client_key = tmp_path / 'jjtv-client.key'
    client_key.write_text(CLIENT_SECRET, encoding='ascii')
    monkeypatch.setenv('INDIA_JJTV_CLIENT_ENABLED', 'true')
    monkeypatch.setenv('INDIA_JJTV_CLIENT_SECRET_FILE', str(client_key))
    monkeypatch.setenv('INDIA_JJTV_DELEGATED_ENABLED', 'false')
    client = TestClient(app)
    return dict(db=session, make=make, client=client, owner=owner, operator=operator, finance=finance,
                platform=platform, platform_headers=login(client, platform), owner_headers=login(client, owner),
                operator_headers=login(client, operator), finance_headers=login(client, finance), cipher=Fernet(key), client_key=client_key, support={})

def native(context, name='Another studio'):
    code = 'test-'+uuid4().hex[:12]
    response = context['client'].post(TENANTS, json=dict(code=code, name=name), headers=context['platform_headers'])
    assert response.status_code == 201, response.text
    return response.json()['tenant']['id']

def entered(context, tenant):
    if tenant not in context['support']:
        response = context['client'].post(TENANTS+f'/{tenant}/enter', json={}, headers=context['platform_headers'])
        assert response.status_code == 200, response.text
        context['support'][tenant] = {'Authorization': 'Bearer '+response.json()['token']}
    return scoped(context['support'][tenant], tenant)

def grant(context, tenant, *members):
    assert tenant == INDIA_TENANT
    for user, key in members:
        role_id = preset_role_id(tenant, 'tenant_admin' if key == 'admin' else key)
        result = context['client'].patch(f'/tenant/v1/staff/{user.id}', json={'role_id': role_id}, headers=context['owner_headers'])
        assert result.status_code == 200, result.text
        for key in ('operator','finance'):
            if context[key].id == user.id: context[key+'_headers'] = login(context['client'], user)

def create_drama(context, tenant, title):
    response = context['client'].post('/admin/v1/dramas', json={'title': title}, headers=entered(context, tenant))
    assert response.status_code == 200, response.text
    return response.json()

def create_episode(context, tenant, drama_id):
    response = context['client'].post(f'/admin/v1/dramas/{drama_id}/episodes', json={'episode_number': 1, 'title': 'Episode 1'}, headers=entered(context, tenant))
    assert response.status_code == 200, response.text
    return response.json()

def test_support_content_is_scoped_and_cannot_switch_by_header(context):
    first, second = native(context, 'Studio A'), native(context, 'Studio B')
    a = create_drama(context, first, 'Same tenant-local title')
    b = create_drama(context, second, 'Same tenant-local title')
    ep = create_episode(context, first, a['id'])
    tags=[]
    for tenant in (first, second):
        result=context['client'].post('/admin/v1/tags',json={'name_en':'Same local tag'},headers=entered(context,tenant))
        assert result.status_code in (200,201),result.text
        tags.append(result.json())
    headers_b=entered(context,second)
    assert {item['id'] for item in context['client'].get('/admin/v1/dramas',headers=headers_b).json()['items']} == {b['id']}
    assert context['client'].get('/admin/v1/dramas',headers=scoped(entered(context,first),second)).status_code == 403
    for method,path,payload in [('GET',f'/admin/v1/dramas/{a["id"]}',None),('PATCH',f'/admin/v1/dramas/{a["id"]}',{'title':'Forbidden'}),('GET',f'/admin/v1/dramas/{a["id"]}/episodes',None),('POST',f'/admin/v1/dramas/{a["id"]}/episodes',{'episode_number':2}),('PATCH',f'/admin/v1/episodes/{ep["id"]}',{'title':'Forbidden'}),('PATCH',f'/admin/v1/tags/{tags[0]["id"]}',{'name_en':'Forbidden'})]:
        kwargs={'headers':headers_b}
        if payload is not None:kwargs['json']=payload
        result=context['client'].request(method,path,**kwargs)
        assert result.status_code==404,(method,path,result.text)
    result=context['client'].patch(f'/admin/v1/dramas/{b["id"]}',json={'tag_ids':[tags[0]['id']]},headers=headers_b)
    assert result.status_code in (404,422)
    uploaded=context['client'].post(f'/admin/v1/episodes/{ep["id"]}/upload-session',headers=entered(context,first),json={'filename':'episode.mp4','mime':'video/mp4','size_bytes':16,'width':720,'height':1280,'duration_seconds':12})
    assert uploaded.status_code==200,uploaded.text
    video_id=uploaded.json()['video_id']
    assert context['client'].head(f'/admin/v1/dev-tus/{video_id}',headers=headers_b).status_code==404
    assert context['client'].patch(f'/admin/v1/dev-tus/{video_id}',content=b'must-not-write',headers=headers_b).status_code==404
    context['db'].expire_all()
    assert context['db'].get(Drama,a['id']).title=='Same tenant-local title'
    assert context['db'].get(Episode,ep['id']).title=='Episode 1'
    assert context['db'].scalar(select(DramaTag).where(DramaTag.drama_id==b['id'])) is None
    assert context['db'].scalar(select(MediaUpload).where(MediaUpload.video_id==video_id)).status!='completed'

def test_public_catalog_and_playback_do_not_accept_admin_tenant_header(context):
    db,client=context['db'],context['client']
    other=native(context)
    default=Drama(tenant_id=DEFAULT_TENANT,title='Default public show',status='published',free_episode_count=1)
    hidden=Drama(tenant_id=other,title='Other tenant private show',status='published',free_episode_count=1)
    db.add_all([default,hidden]);db.flush()
    episodes=[Episode(drama_id=row.id,episode_number=1,title='First',status='published',bunny_video_id='fake-'+str(row.id)) for row in (default,hidden)]
    db.add_all(episodes);db.commit()
    headers={'X-JJTV-Tenant':other}
    home=client.get('/v1/home',headers=headers)
    assert home.status_code==200
    assert {row['id'] for row in home.json()['items']}=={default.id}
    assert client.get(f'/v1/dramas/{hidden.id}',headers=headers).status_code==404
    assert client.get(f'/v1/preview/dramas/{hidden.id}',headers=headers).status_code==404
    playback=client.post('/v1/playback',json={'drama_id':hidden.id,'episode_id':episodes[1].id},headers=headers)
    assert playback.status_code in (200,403,404)
    if playback.status_code==200:assert playback.json().get('allowed') is False and not playback.json().get('url')

@pytest.mark.parametrize('path',['/admin/v1/finance/orders','/admin/v1/finance/ledger'])
def test_nonowner_native_tenant_cannot_use_unadapted_legacy_module(context,path):
    other=native(context)
    response=context['client'].get(path,headers=entered(context,other))
    assert response.status_code==403,response.text


@pytest.fixture()
def india_mock(monkeypatch):
    from app.modules.tenancy import router as tenant_router
    observed = []
    state = {"expired": False}

    class FakeIndiaClient:
        def __init__(self, *, operator_id, **kwargs):
            self.operator_id = operator_id

        async def call(self, action, *, access_token=None, resource_id=None, payload=None, params=None, idempotency_key=None):
            observed.append({"action": action, "access_token": access_token, "resource_id": resource_id,
                             "payload": payload, "params": params, "idempotency_key": idempotency_key, "operator_id": self.operator_id})
            if state["expired"] and access_token is not None:
                raise IndiaError("india_auth_required", 409, upstream_code="SESSION_EXPIRED", request_id="old-expired")
            if action == "configuration": return IndiaReply({"authentication": {"identity_mode": "first_party", "admin_password_enabled": True}}, "old-config")
            if action == "login":
                token = ACCESS_B if payload["username"] == "old-second" else ACCESS_A
                return IndiaReply(bridge_tokens(token, user=OLD_ACTOR), "old-login")
            if action == "refresh":
                assert payload == {"refresh_token": REFRESH}
                return IndiaReply(bridge_tokens("n" * 43, "s" * 43), "old-refresh")
            if action == "logout":
                assert payload == {"refresh_token": REFRESH}
                assert access_token is None
                return IndiaReply({"logged_out": True}, "old-logout")
            if action == "me":
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
    logouts = [call for call in india_mock[0] if call["action"] == "logout"]
    assert len(logouts) == 1 and logouts[0]["payload"] == {"refresh_token": REFRESH}
    assert logouts[0]["operator_id"] == str(context["operator"].id)


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
    configuration = client.get(TENANTS + "/" + INDIA_TENANT + "/modules", headers=context["platform_headers"])
    assert configuration.status_code == 200, configuration.text
    assert client.patch(TENANTS + "/" + INDIA_TENANT, json={"status": "paused", "version": configuration.json()["version"]}, headers=context["platform_headers"]).status_code == 200
    assert client.get(INDIA + "/admin/series", headers=context["operator_headers"]).status_code in (401, 403, 409)
    db.expire_all()
    assert db.scalar(select(IndiaConnection).where(IndiaConnection.user_id == context["operator"].id)) is None
    configuration = client.get(TENANTS + "/" + INDIA_TENANT + "/modules", headers=context["platform_headers"])
    assert configuration.status_code == 200, configuration.text
    assert client.patch(TENANTS + "/" + INDIA_TENANT, json={"status": "active", "version": configuration.json()["version"]}, headers=context["platform_headers"]).status_code == 200
    context["operator_headers"] = login(client, context["operator"])
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


def test_failed_tenant_staff_audit_rolls_back_role_and_connection(context, india_mock):
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
        if target.actor_id == context["owner"].id and target.target_id == operator.id and target.target_type == "admin_user":
            rejected_audits.append(target.action)
            raise RuntimeError("synthetic audit insert failure")

    event.listen(AuditLog, "before_insert", reject_membership_audit)
    try:
        failed_client = TestClient(app, raise_server_exceptions=False)
        response = failed_client.patch("/tenant/v1/staff/" + str(operator.id), json={"role_id": preset_role_id(INDIA_TENANT, "finance")}, headers=headers)
        assert response.status_code >= 500
    finally:
        event.remove(AuditLog, "before_insert", reject_membership_audit)
    assert len(rejected_audits) == 1  # The target staff audit, not authentication, failed.
    db.expire_all()
    member = db.get(TenantMember, (INDIA_TENANT, operator.id), populate_existing=True)
    remaining = db.get(IndiaConnection, primary_key, populate_existing=True)
    assert member is not None and member.role == "ops"
    assert remaining is not None and remaining.secret == saved_cipher
    assert context["client"].get(INDIA + "/session", headers=context["operator_headers"]).json()["connected"] is True


@pytest.fixture()
def india_auth_http(monkeypatch, context):
    """Real connector transport with synthetic provider secrets and session data."""
    from app.modules.tenancy import router as tenant_router
    observed, state = [], {"errors": {}, "responses": {}}

    def handler(request):
        path = request.url.path.removeprefix("/v1/")
        body = json.loads(request.content) if request.content else {}
        headers = dict(request.headers)
        observed.append((path, body, headers))
        assert headers["x-jjtv-client-id"] == "jjtv"
        assert headers["x-jjtv-client-secret"] == context["client_key"].read_text(encoding="ascii")
        error = state["errors"].get(path)
        if error:
            status, code = error
            return httpx.Response(status, json={"code": code, "request_id": "auth-http-error",
                                               "details": {"secret": "upstream-private-body"}})
        if path in state["responses"]:
            data = state["responses"][path]
        elif path == "auth/jjtv/login":
            data = bridge_tokens(user=OLD_ACTOR)
        elif path == "auth/jjtv/refresh":
            data = bridge_tokens("n" * 43, "s" * 43)
        elif path == "auth/jjtv/logout":
            data = {"logged_out": True}
        elif path == "me":
            data = OLD_ACTOR
        elif path == "admin/series":
            data = {"items": [], "next_cursor": None}
        else:
            pytest.fail("Unexpected synthetic India path: " + path)
        return httpx.Response(200, json={"data": data, "request_id": "auth-http-success"})

    monkeypatch.setattr(tenant_router, "IndiaClient", lambda **kwargs: IndiaClient(
        **kwargs, base_url="https://india.example/v1", transport=httpx.MockTransport(handler)))
    return observed, state


def saved_india(context):
    db = context["db"]
    db.expire_all()
    key = (INDIA_TENANT, context["owner"].id, hashlib.sha256(context["owner_headers"]["Authorization"].encode()).hexdigest())
    row = db.get(IndiaConnection, key)
    return row, json.loads(context["cipher"].decrypt(row.secret.encode()).decode()) if row else None


def replace_saved_india(context, data):
    row, _ = saved_india(context)
    row.secret = context["cipher"].encrypt(json.dumps(data).encode()).decode()
    context["db"].commit()
    return row.secret


def test_first_connection_uses_only_password_and_server_derived_client_identity(context, india_auth_http):
    client, headers = context["client"], context["owner_headers"]
    forged = {**headers, "X-JJTV-Client-Id": "attacker", "X-JJTV-Client-Secret": "browser-forgery",
              "X-JJTV-Operator-Id": "another-user"}
    connected = connect(context, forged)
    assert connected.json()["auth_mode"] == "jjtv_client_password"
    assert "no-store" in connected.headers["cache-control"]
    assert not any(value in connected.text for value in [CLIENT_SECRET, ACCESS_A, REFRESH, "challenge_token", "mfa_required"])
    assert [call[0] for call in india_auth_http[0]] == ["auth/jjtv/login"]
    assert india_auth_http[0][0][2]["x-jjtv-operator-id"] == str(context["owner"].id)
    row, data = saved_india(context)
    assert data["auth_mode"] == "jjtv_client_password" and data["state"] == "connected"
    assert all(value not in json.dumps(data) for value in [CLIENT_SECRET, "old-password-not-stored", CHALLENGE])


def test_password_bridge_support_keeps_real_platform_actor_and_bound_business_session(context, india_auth_http):
    client, db = context["client"], context["db"]
    denied = client.post(INDIA + "/session", headers=context["platform_headers"],
        json={"username": "old-existing", "password": "synthetic"})
    assert denied.status_code == 403 and india_auth_http[0] == []
    support = entered(context, INDIA_TENANT)
    connect(context, support)
    assert india_auth_http[0][0][2]["x-jjtv-operator-id"] == str(context["platform"].id)
    assert client.get(INDIA + "/session", headers=context["owner_headers"]).json()["connected"] is False
    before = len(india_auth_http[0])
    spoofed = client.get(INDIA + "/session", headers=scoped(support, DEFAULT_TENANT))
    assert spoofed.status_code == 403 and len(india_auth_http[0]) == before
    db.expire_all()
    audit = db.scalar(select(AuditLog).where(
        AuditLog.actor_id == context["platform"].id, AuditLog.action == "india.login"))
    assert audit is not None and audit.actor_scope == "platform" and audit.tenant_id == INDIA_TENANT
    assert audit.payload["upstream_actor_id"] == OLD_ACTOR["id"]


@pytest.mark.parametrize("endpoint", ["mfa", "enrollment", "cancel"])
def test_retired_mfa_routes_never_forward_otp_or_enrollment(context, india_auth_http, endpoint):
    response = context["client"].post(INDIA + "/session/" + endpoint, headers=context["owner_headers"],
        json={"challenge_token": CHALLENGE, "code": "123456"})
    assert response.status_code == 410 and response.json()["detail"]["code"] == "india_mfa_retired"
    assert india_auth_http[0] == [] and saved_india(context)[0] is None


@pytest.mark.parametrize("setting", ["disabled", "missing-key"])
def test_unconfigured_client_cannot_fall_back_to_old_web_auth(context, india_auth_http, monkeypatch, setting):
    if setting == "disabled": monkeypatch.delenv("INDIA_JJTV_CLIENT_ENABLED", raising=False)
    else: context["client_key"].unlink()
    client, headers = context["client"], context["owner_headers"]
    status = client.get(INDIA + "/session", headers=headers)
    assert status.status_code == 200
    assert status.json() == {"configured": False, "auth_mode": "unavailable", "connected": False, "state": "disconnected"}
    denied = client.post(INDIA + "/session", headers=headers, json={"username": "old-existing", "password": "synthetic"})
    assert denied.status_code == 503 and denied.json()["detail"]["code"] == "india_client_unavailable"
    assert india_auth_http[0] == []


def test_bad_password_preserves_an_existing_india_business_connection(context, india_auth_http):
    connect(context)
    before = saved_india(context)[0].secret
    india_auth_http[1]["errors"]["auth/jjtv/login"] = (401, "INVALID_CREDENTIALS")
    rejected = context["client"].post(INDIA + "/session", headers=context["owner_headers"],
        json={"username": "another-account", "password": "synthetic-wrong-password"})
    assert rejected.status_code == 400 and rejected.json()["detail"]["code"] == "india_login_rejected"
    assert "upstream-private-body" not in rejected.text
    assert saved_india(context)[0].secret == before
    assert context["client"].get(INDIA + "/session", headers=context["owner_headers"]).json()["connected"] is True


@pytest.mark.parametrize("stage", ["login", "refresh"])
@pytest.mark.parametrize("shape,expected_code", [("mfa", "india_client_contract_invalid"),
    ("web-token", "india_client_contract_invalid"), ("malformed-token", "india_invalid_response")])
def test_invalid_provider_auth_success_preserves_existing_connection(context, india_auth_http, stage, shape, expected_code):
    connect(context)
    before = saved_india(context)[0].secret
    if shape == "mfa": response = {"mfa_required": True, "challenge_token": CHALLENGE}
    elif shape == "web-token": response = {"access_token": ACCESS_A, "refresh_token": REFRESH, "user": OLD_ACTOR}
    else: response = {**bridge_tokens(user=OLD_ACTOR), "refresh_token": 123}
    india_auth_http[1]["responses"]["auth/jjtv/" + stage] = response
    rejected = context["client"].post(INDIA + "/session" + ("/refresh" if stage == "refresh" else ""),
        headers=context["owner_headers"], json={} if stage == "refresh" else {"username": "other", "password": "synthetic"})
    assert rejected.status_code == 502 and rejected.json()["detail"]["code"] == expected_code
    assert saved_india(context)[0].secret == before
    assert context["client"].get("/admin/v1/auth/me", headers=context["owner_headers"]).status_code == 200


@pytest.mark.parametrize("state", ["mfa_required", "connected"])
def test_legacy_challenges_and_web_mfa_business_tokens_are_never_reused(context, india_auth_http, state):
    connect(context)
    legacy = {"state": state, "challenge_token": CHALLENGE} if state == "mfa_required" else {
        "state": "connected", "access_token": ACCESS_A, "refresh_token": REFRESH, "user": OLD_ACTOR}
    replace_saved_india(context, legacy)
    before = len(india_auth_http[0])
    client, headers = context["client"], context["owner_headers"]
    assert client.get(INDIA + "/session", headers=headers).json()["connected"] is False
    for method, path in [("GET", "/admin/series"), ("POST", "/session/refresh")]:
        rejected = client.request(method, INDIA + path, headers=headers)
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "india_auth_required"
    assert len(india_auth_http[0]) == before


def test_client_key_rotation_invalidates_saved_connection_until_new_password_login(context, india_auth_http):
    connect(context)
    context["client_key"].write_text("rotated_synthetic_client_" + "n" * 43, encoding="ascii")
    before = len(india_auth_http[0])
    client, headers = context["client"], context["owner_headers"]
    assert client.get(INDIA + "/session", headers=headers).json()["connected"] is False
    rejected = client.get(INDIA + "/admin/series", headers=headers)
    assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "india_auth_required"
    assert len(india_auth_http[0]) == before
    connect(context)
    assert client.get(INDIA + "/session", headers=headers).json()["connected"] is True


def test_real_connector_business_401_still_clears_its_connection_only(context, india_auth_http):
    connect(context)
    india_auth_http[1]["errors"]["admin/series"] = (401, "SESSION_EXPIRED")
    expired = context["client"].get(INDIA + "/admin/series", headers=context["owner_headers"])
    assert expired.status_code == 409 and expired.json()["detail"]["code"] == "india_auth_required"
    assert saved_india(context)[0] is None
    assert context["client"].get("/admin/v1/auth/me", headers=context["owner_headers"]).status_code == 200


def test_disconnect_revokes_through_dedicated_family_endpoint_only(context, india_auth_http):
    connect(context)
    disconnected = context["client"].delete(INDIA + "/session", headers=context["owner_headers"])
    assert disconnected.status_code == 200 and disconnected.json()["connected"] is False
    assert [call[0] for call in india_auth_http[0]] == ["auth/jjtv/login", "auth/jjtv/logout"]
    assert india_auth_http[0][-1][1] == {"refresh_token": REFRESH}
    assert saved_india(context)[0] is None
    assert context["client"].get("/admin/v1/auth/me", headers=context["owner_headers"]).status_code == 200


def test_failed_provider_logout_keeps_local_connection_instead_of_claiming_revocation(context, india_auth_http):
    connect(context)
    before = saved_india(context)[0].secret
    india_auth_http[1]["errors"]["auth/jjtv/logout"] = (503, "TEMPORARILY_UNAVAILABLE")
    rejected = context["client"].delete(INDIA + "/session", headers=context["owner_headers"])
    assert rejected.status_code == 502 and rejected.json()["detail"]["retryable"] is True
    assert saved_india(context)[0].secret == before


def test_expiring_access_is_refreshed_before_content_request_and_not_blindly_retried(context, india_auth_http):
    connect(context)
    _, data = saved_india(context)
    data["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    replace_saved_india(context, data)
    india_auth_http[0].clear()
    listed = context["client"].get(INDIA + "/admin/series", headers=context["owner_headers"])
    assert listed.status_code == 200
    assert [call[0] for call in india_auth_http[0]] == ["auth/jjtv/refresh", "admin/series"]
    assert india_auth_http[0][-1][2]["authorization"] == "Bearer " + "n" * 43
    assert saved_india(context)[1]["access_token"] == "n" * 43


@pytest.fixture()
def india_delegated_http(monkeypatch, context, tmp_path):
    """Actual consumer HTTP transport, with independently checked synthetic signed proofs."""
    from app.modules.tenancy import router as tenant_router

    secret = 'synthetic_delegated_contract_' + 'd' * 43
    key = tmp_path / 'jjtv-delegated.key'
    key.write_text(secret, encoding='ascii')
    key.chmod(0o600)
    monkeypatch.setenv('INDIA_JJTV_DELEGATED_ENABLED', 'true')
    monkeypatch.setenv('INDIA_JJTV_DELEGATED_SECRET_FILE', str(key))
    # Delegation needs neither encrypted legacy connections nor password bridge configuration.
    monkeypatch.delenv('TENANT_CONNECTION_KEY', raising=False)
    monkeypatch.delenv('INDIA_JJTV_CLIENT_ENABLED', raising=False)
    monkeypatch.delenv('INDIA_JJTV_CLIENT_SECRET_FILE', raising=False)
    observed = []

    def handler(request):
        encoded, signature = request.headers['x-jjtv-delegated-proof'].split('.')
        decode = lambda value: base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        assert hmac.compare_digest(decode(signature), hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
        claims = json.loads(decode(encoded))
        assert claims['target'] == request.url.raw_path.decode('ascii')
        assert claims['body_sha256'] == ('STREAMING' if request.method == 'PUT' else hashlib.sha256(request.content).hexdigest())
        assert 'authorization' not in request.headers and 'x-jjtv-client-secret' not in request.headers
        observed.append({'claims': claims, 'body': request.content, 'path': request.url.path})
        return httpx.Response(200, json={'data': {'items': [], 'next_cursor': None}, 'request_id': 'delegated-http-request'})

    monkeypatch.setattr(tenant_router, 'IndiaClient', lambda **kwargs: IndiaClient(
        **kwargs, base_url='https://india.example/v1', transport=httpx.MockTransport(handler)))
    return observed, key


def delegated_role(context, permissions):
    db = context['db']
    role = TenantRole(id='delegated-'+uuid4().hex, tenant_id=INDIA_TENANT, name='Delegated test role',
                      kind='business', permissions=permissions, version=1)
    db.add(role); db.flush()
    db.get(TenantMember, (INDIA_TENANT, context['operator'].id)).role_id = role.id
    db.commit()
    return role


def test_delegated_india_works_after_each_jjtv_login_without_any_legacy_connection(context, india_delegated_http):
    client, headers, db = context['client'], context['owner_headers'], context['db']
    status = client.get(INDIA+'/session', headers=headers)
    assert status.status_code == 200 and status.json()['auth_mode'] == 'jjtv_delegated'
    assert status.json()['configured'] is True and status.json()['connected'] is True
    assert status.json()['username'] == context['owner'].username
    assert 'role' not in status.json() and 'expires_at' not in status.json()
    assert india_delegated_http[0] == []  # Status does not perform an old staff login.
    forged = {**headers, 'X-JJTV-Operator-Id': 'attacker', 'X-JJTV-Delegated-Proof': 'browser-forgery',
              'X-JJTV-Client-Secret': 'browser-secret'}
    assert client.get(INDIA+'/admin/series', headers=forged).status_code == 200
    fresh = login(client, context['owner'])
    assert client.get(INDIA+'/admin/series', headers=fresh).status_code == 200
    claims = [item['claims'] for item in india_delegated_http[0]]
    assert len(claims) == 2 and all(item['operator_id'] == str(context['owner'].id) for item in claims)
    assert all(item['actor_scope'] == 'tenant' and item['tenant_id'] == INDIA_TENANT for item in claims)
    assert claims[0]['jti'] != claims[1]['jti']
    assert db.scalar(select(IndiaConnection)) is None


@pytest.mark.parametrize('method,path,body', [('POST','/session',{'username':'unused','password':'never-forward'}),
    ('POST','/session/refresh',{}), ('DELETE','/session',None)])
def test_delegated_mode_retires_individual_password_connection_controls(context, india_delegated_http, method, path, body):
    response = context['client'].request(method, INDIA+path, headers=context['owner_headers'], json=body)
    assert response.status_code == 410 and response.json()['detail']['code'] == 'india_connection_not_required'
    assert india_delegated_http[0] == [] and context['db'].scalar(select(IndiaConnection)) is None


@pytest.mark.parametrize('configuration', ['missing-key', 'mistyped-flag'])
def test_delegated_configuration_failure_cannot_fall_back_to_legacy_password(context, india_delegated_http, monkeypatch, configuration):
    if configuration == 'missing-key': india_delegated_http[1].unlink()
    else: monkeypatch.setenv('INDIA_JJTV_DELEGATED_ENABLED', 'ture')
    status = context['client'].get(INDIA+'/session', headers=context['owner_headers'])
    assert status.status_code == 200 and status.json()['auth_mode'] == 'jjtv_delegated'
    assert status.json()['configured'] is False and status.json()['connected'] is False
    denied = context['client'].get(INDIA+'/admin/series', headers=context['owner_headers'])
    assert denied.status_code == 503 and denied.json()['detail']['code'] == 'india_delegated_unavailable'
    assert india_delegated_http[0] == []


def test_delegated_permissions_are_live_and_cannot_be_supplied_by_browser_or_other_tenant(context, india_delegated_http):
    client = context['client']
    assert client.get(INDIA+'/session', headers=context['operator_headers']).status_code == 403
    role = delegated_role(context, ['dramas.view', 'dramas.edit'])
    headers = context['operator_headers']
    assert client.get(INDIA+'/admin/series', headers=headers).status_code == 200
    assert india_delegated_http[0][-1]['claims']['permissions'] == ['dramas.edit', 'dramas.view']
    before = len(india_delegated_http[0])
    assert client.get(INDIA+'/admin/series', headers=scoped(headers, DEFAULT_TENANT)).status_code == 403
    assert client.get(INDIA+'/admin/series', headers=context['platform_headers']).status_code == 403
    role.permissions = []; role.version += 1; context['db'].commit()
    assert client.get(INDIA+'/admin/series', headers=headers).status_code == 403
    assert len(india_delegated_http[0]) == before


def test_delegated_edit_and_pricing_permissions_are_checked_per_field_before_signing(context, india_delegated_http):
    client, headers, db = context['client'], context['operator_headers'], context['db']
    role = delegated_role(context, ['dramas.view', 'dramas.pricing'])
    def patch(payload):
        return client.patch(INDIA+'/admin/series/series_existing', json=payload,
                            headers={**headers, 'Idempotency-Key': str(uuid4())})
    assert patch({'version': 3, 'free_episode_count': 2}).status_code == 200
    assert patch({'version': 3, 'title': 'Denied'}).status_code == 403
    assert len(india_delegated_http[0]) == 1
    assert india_delegated_http[0][0]['claims']['permissions'] == ['dramas.pricing', 'dramas.view']
    role.permissions = ['dramas.view', 'dramas.edit']; role.version += 1; db.commit()
    assert patch({'version': 3, 'title': 'Allowed'}).status_code == 200
    assert patch({'version': 3, 'title': 'Mixed denied', 'member_window': True}).status_code == 403
    assert len(india_delegated_http[0]) == 2
    create = client.post(INDIA+'/admin/series', json={'title': 'Pricing denied', 'free_episode_count': 0},
                         headers={**headers, 'Idempotency-Key': str(uuid4())})
    assert create.status_code == 403 and len(india_delegated_http[0]) == 2


def test_delegated_review_is_separate_from_publish_and_support_audits_real_platform_actor(context, india_delegated_http):
    client, db = context['client'], context['db']
    delegated_role(context, ['reviews.view', 'reviews.decide'])
    headers = {**context['operator_headers'], 'Idempotency-Key': str(uuid4())}
    assert client.post(INDIA+'/admin/series/series_existing/review', json={'version':3,'approved':True,'reason':'Review'}, headers=headers).status_code == 200
    assert client.post(INDIA+'/admin/series/series_existing/publish', json={'version':3,'reason':'Publish'}, headers=headers).status_code == 403
    assert len(india_delegated_http[0]) == 1
    support = {**entered(context, INDIA_TENANT), 'Idempotency-Key': str(uuid4())}
    result = client.post(INDIA+'/admin/series/series_existing/publish', json={'version':3,'reason':'Platform support'}, headers=support)
    assert result.status_code == 200
    proof = india_delegated_http[0][-1]['claims']
    assert proof['operator_id'] == str(context['platform'].id) and proof['actor_scope'] == 'platform'
    db.expire_all()
    record = db.scalar(select(AuditLog).where(AuditLog.actor_id == context['platform'].id, AuditLog.action == 'india.publish'))
    assert record.actor_scope == 'platform' and record.tenant_id == INDIA_TENANT
    assert record.payload['auth_mode'] == 'jjtv_delegated'
    assert record.payload['upstream_request_id'] == 'delegated-http-request'
    assert 'upstream_actor_id' not in record.payload
