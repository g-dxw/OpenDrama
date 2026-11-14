"""JJTV-only finance authorization and minimal manual source-resolution HTTP boundary."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.billing import commerce
from app.modules.billing.service import BillingError
from app.modules.ops import api_keys
from app.modules.tenancy.models import Tenant, TenantModule, TenantRole
from app.modules.tenancy.permissions import preset_role_id, seed_tenant_defaults

PATH = "/admin/v1/finance/orders/RESOLVE-ORDER/membership-refund-resolution"
BODY = {"reason": "Verified failed refund; restore the purchased source", "idempotency_key": "resolution-route-one", "expected_revision": "a" * 64}
RESULT = {"order_no": "RESOLVE-ORDER", "resolved": True, "restored": True, "released_coins": 25,
          "starts_at": "2026-11-14T01:00:00+00:00", "ends_at": "2026-11-16T01:00:00+00:00", "replayed": False}


def login(client, actor):
    response = client.post("/admin/v1/auth/login", json={"tenant_code": actor._test_tenant_code, "username": actor.username, "password": "pass-1234"})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


@pytest.fixture
def boundary(session, make, monkeypatch):
    calls = []
    def core(db, order_no, actor_id, reason, idempotency_key, expected_revision):
        calls.append((order_no, actor_id, reason, idempotency_key, expected_revision))
        return {**RESULT, "account_code": "PRIVATE_MERCHANT", "payment_intent_id": "pi_PRIVATE", "snapshot": {"customer": "PRIVATE_CUSTOMER"}}
    monkeypatch.setattr(commerce, "resolve_membership_refund", core, raising=False)
    return TestClient(app), calls


def authorized_finance(session, make, client):
    actor = make.admin(role="finance")
    role = session.get(TenantRole, preset_role_id("jjtv", "finance"))
    role.permissions = ["finance.view", "finance.refund"]
    session.commit()
    return actor, login(client, actor)


def test_route_requires_staff_login(boundary):
    client, calls = boundary
    response = client.post(PATH, json=BODY)
    assert response.status_code == 401, response.text
    assert not calls


@pytest.mark.parametrize("role", ["viewer", "finance"])
def test_viewer_and_readonly_finance_cannot_resolve(boundary, session, make, role):
    client, calls = boundary
    actor = make.admin(role="ops" if role == "viewer" else role)
    if role == "viewer":
        session.get(TenantRole, preset_role_id("jjtv", "ops")).permissions = ["finance.view"]
        session.commit()
    response = client.post(PATH, headers=login(client, actor), json=BODY)
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "tenant_permission_denied"
    assert not calls


def test_explicit_refund_permission_routes_current_actor_and_filters_response(boundary, session, make):
    client, calls = boundary
    actor, headers = authorized_finance(session, make, client)
    response = client.post(PATH, headers=headers, json={**BODY, "actor_id": 999999, "account_code": "must-ignore"})
    assert response.status_code == 200, response.text
    assert response.json() == RESULT
    assert "PRIVATE" not in response.text
    assert calls == [("RESOLVE-ORDER", actor.id, BODY["reason"], BODY["idempotency_key"], BODY["expected_revision"])]
    assert "no-store" in response.headers["Cache-Control"]


@pytest.mark.parametrize("changes", [
    {"reason": ""}, {"reason": "x" * 501}, {"idempotency_key": "short"},
    {"idempotency_key": "x" * 101}, {"expected_revision": ""},
    {"expected_revision": "a" * 63}, {"expected_revision": "g" * 64},
    {"expected_revision": "A" * 64}, {"expected_revision": None},
])
def test_invalid_resolution_body_never_reaches_core(boundary, session, make, changes):
    client, calls = boundary
    _actor, headers = authorized_finance(session, make, client)
    response = client.post(PATH, headers=headers, json={**BODY, **changes})
    assert response.status_code == 422, response.text
    assert not calls


def test_missing_revision_never_reaches_core(boundary, session, make):
    client, calls = boundary
    _actor, headers = authorized_finance(session, make, client)
    response = client.post(PATH, headers=headers, json={key: value for key, value in BODY.items() if key != "expected_revision"})
    assert response.status_code == 422, response.text
    assert not calls


@pytest.mark.parametrize("tenant_id", ["other-resolution", "india-short-drama"])
def test_other_tenants_cannot_resolve_jjtv_sources(boundary, session, make, tenant_id):
    client, calls = boundary
    if tenant_id != "india-short-drama":
        tenant = Tenant(id=tenant_id, code=tenant_id, name="Other native", kind="native", status="active")
        session.add(tenant)
        seed_tenant_defaults(session, tenant)
        session.commit()
    actor = make.admin(role="admin", tenant_id=tenant_id)
    response = client.post(PATH, headers=login(client, actor), json=BODY)
    assert response.status_code == (409 if tenant_id == "india-short-drama" else 403), response.text
    assert not calls


def test_disabled_finance_module_stays_closed_to_tenant_admin(boundary, session, make):
    client, calls = boundary
    actor = make.admin(role="admin")
    headers = login(client, actor)
    session.get(TenantModule, ("jjtv", "finance")).enabled = False
    session.commit()
    response = client.post(PATH, headers=headers, json=BODY)
    assert response.status_code == 403, response.text
    assert not calls


def test_core_conflict_uses_existing_http_rollback_contract(boundary, session, make, monkeypatch):
    client, _calls = boundary
    _actor, headers = authorized_finance(session, make, client)
    detail = {"code": "membership_resolution_changed", "action": "review_again", "committed": False, "message": "Refresh the finance order detail"}
    def conflict(*args):
        raise BillingError(409, detail)
    monkeypatch.setattr(commerce, "resolve_membership_refund", conflict)
    response = client.post(PATH, headers=headers, json=BODY)
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": detail}


def test_resolution_keeps_billing_api_key_scope():
    assert api_keys.scope_for_request("POST", PATH) == "billing"
    assert api_keys.scope_for_request("POST", PATH.replace("/admin/v1/", "/tenant/v1/")) == "billing"


@pytest.mark.parametrize("scope", ["read", "billing"])
def test_resolution_api_key_requires_billing_scope_and_refund_permission(boundary, session, make, scope):
    client, calls = boundary
    owner, _headers = authorized_finance(session, make, client)
    issuer = make.admin(role="admin")
    key = client.post("/admin/v1/api-keys", headers=login(client, issuer), json={"user_id": owner.id, "name": "resolution-scope", "scopes": [scope], "expires_in_days": 30})
    assert key.status_code == 201, key.text
    response = client.post(PATH, headers={"Authorization": "Bearer " + key.json()["key"]}, json=BODY)
    assert response.status_code == (200 if scope == "billing" else 403), response.text
    if scope == "billing":
        assert response.json() == RESULT
        assert calls[0][1] == owner.id
    else:
        assert response.json()["detail"]["code"] == "scope_denied"
        assert not calls


def test_resolution_can_release_coins_without_restoring_an_access_window(boundary, session, make, monkeypatch):
    client, _calls = boundary
    _actor, headers = authorized_finance(session, make, client)
    result = {**RESULT, "restored": False, "starts_at": None, "ends_at": None, "replayed": True}
    monkeypatch.setattr(commerce, "resolve_membership_refund", lambda *args: {**result, "checkout_context": "PRIVATE_FROZEN_BUDGET"})
    response = client.post(PATH, headers=headers, json=BODY)
    assert response.status_code == 200, response.text
    assert response.json() == result
    assert "PRIVATE" not in response.text
