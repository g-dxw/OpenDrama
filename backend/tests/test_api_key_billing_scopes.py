"""API-key scopes remain narrower than staff grants on all commerce surfaces."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.billing.models import Plan, PlanVersion
from app.modules.ops import api_keys
from app.modules.tenancy.models import TenantMember
from app.modules.tenancy.permissions import preset_role_id


@pytest.fixture()
def client(session):
    return TestClient(app)


@pytest.fixture()
def issuer(make, client):
    account = make.admin(role="admin", username="billing-key-issuer")
    response = client.post("/admin/v1/auth/login", json={
        "tenant_code": "jjtv", "username": account.username, "password": "pass-1234",
    })
    assert response.status_code == 200, response.text
    return account, {"Authorization": "Bearer " + response.json()["token"]}


def issue_key(client, issuer, scopes, user_id=None):
    account, headers = issuer
    response = client.post("/admin/v1/api-keys", headers=headers, json={
        "user_id": user_id or account.id, "name": "billing-scope-regression",
        "scopes": scopes, "expires_in_days": 30,
    })
    assert response.status_code == 201, response.text
    return {"Authorization": "Bearer " + response.json()["key"]}


# All request bodies are valid. A missing scope must reject before a handler
# reads financial data, changes a snapshot, creates a draft account or calls Stripe.
BILLING_REQUESTS = [
    ("GET", "/admin/v1/commerce/plans", None),
    ("GET", "/admin/v1/commerce/orders", None),
    ("GET", "/admin/v1/stripe/accounts", None),
    ("GET", "/admin/v1/stripe/prices", None),
    ("GET", "/admin/v1/coin-packs", None),
    ("POST", "/admin/v1/commerce/plans/missing/price", {"price_cents": 999, "note": "must reject"}),
    ("POST", "/admin/v1/stripe/accounts", {"code": "restricted", "name": "Must not create"}),
    ("PATCH", "/admin/v1/stripe/accounts/missing", {"name": "Must not change"}),
    ("POST", "/admin/v1/stripe/prices/provision", {}),
    ("POST", "/admin/v1/coin-packs", {"code": "restricted", "name_en": "Must not create", "price_cents": 99, "base_coins": 100}),
    ("PATCH", "/admin/v1/coin-packs/missing", {"price_cents": 999}),
    ("POST", "/admin/v1/coin-packs/missing/status", {"status": "active"}),
    ("GET", "/admin/v1/finance/orders", None),
]


@pytest.mark.parametrize("scopes", [["read"], ["read", "write"]], ids=["read-only", "read-write"])
@pytest.mark.parametrize("method,path,body", BILLING_REQUESTS, ids=[m + " " + p for m, p, _ in BILLING_REQUESTS])
def test_restricted_key_cannot_access_commerce(client, issuer, scopes, method, path, body):
    headers = issue_key(client, issuer, scopes)
    response = client.request(method, path, headers=headers, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "scope_denied"
    assert "billing" in response.json()["detail"]["message"]


@pytest.mark.parametrize("path", ["/commerce/plans", "/commerce/orders", "/stripe/accounts", "/stripe/prices", "/coin-packs"])
def test_billing_key_can_read_authorized_commerce(client, issuer, path):
    response = client.get("/admin/v1" + path, headers=issue_key(client, issuer, ["billing"]))
    assert response.status_code == 200, response.text
    assert "items" in response.json()


@pytest.mark.parametrize("method,path,body", [
    ("GET", "commerce/orders", None),
    ("POST", "commerce/plans/monthly/price", {}),
    ("GET", "stripe/accounts", None),
    ("PATCH", "stripe/accounts/main", {}),
    ("GET", "coin-packs", None),
    ("POST", "coin-packs/starter/status", {}),
])
def test_tenant_alias_has_same_billing_scope(method, path, body):
    assert api_keys.scope_for_request(method, "/tenant/v1/" + path) == "billing"
    assert api_keys.scope_for_request(method, "/admin/v1/" + path) == "billing"


def seed_plan(session):
    plan = Plan(code="monthly", name_en="Monthly")
    session.add(plan)
    session.flush()
    session.add(PlanVersion(plan_id=plan.id, version=1, price_cents=499))
    session.commit()
    return plan


def test_billing_key_can_manage_only_with_current_staff_grants(client, issuer, session):
    account, _ = issuer
    plan = seed_plan(session)
    headers = issue_key(client, issuer, ["billing"])
    response = client.post("/admin/v1/commerce/plans/monthly/price", headers=headers,
        json={"price_cents": 999, "note": "authorized snapshot"})
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert client.get("/admin/v1/stripe/accounts", headers=headers).status_code == 200

    # A key keeps its billing scope after the staff member is downgraded,
    # but immediately loses actions and menus outside their current grants.
    member = session.get(TenantMember, ("jjtv", account.id))
    member.role_id = preset_role_id("jjtv", "finance")
    session.commit()
    assert client.get("/admin/v1/commerce/plans", headers=headers).status_code == 200
    denied = client.post("/admin/v1/commerce/plans/monthly/price", headers=headers,
        json={"price_cents": 1499, "note": "must reject current read-only staff"})
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"]["code"] == "tenant_permission_denied"
    assert client.get("/admin/v1/stripe/accounts", headers=headers).status_code == 403
    assert session.scalar(select(func.count()).select_from(PlanVersion).where(PlanVersion.plan_id == plan.id)) == 2


def test_billing_key_writes_coins_and_draft_accounts_without_broad_write_scope(client, issuer):
    headers = issue_key(client, issuer, ["billing"])
    created = client.post("/admin/v1/coin-packs", headers=headers, json={
        "code": "starter", "name_en": "Starter", "price_cents": 99, "base_coins": 100,
    })
    assert created.status_code == 200, created.text
    changed = client.patch("/admin/v1/coin-packs/starter", headers=headers, json={"price_cents": 199})
    assert changed.status_code == 200, changed.text
    draft = client.post("/admin/v1/stripe/accounts", headers=headers, json={
        "code": "draft_test", "name": "Draft without credentials", "status": "draft",
    })
    assert draft.status_code == 200, draft.text
    assert draft.json()["secret_key_configured"] is False


def test_read_write_key_keeps_ordinary_content_access(client, issuer):
    headers = issue_key(client, issuer, ["read", "write"])
    assert client.get("/admin/v1/dramas", headers=headers).status_code == 200
    created = client.post("/admin/v1/dramas", headers=headers, json={"title": "Content scope unchanged"})
    assert created.status_code == 200, created.text
