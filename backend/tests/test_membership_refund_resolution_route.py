"""JJTV-only finance authorization and minimal manual source-resolution HTTP boundary."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.billing import commerce
from app.modules.billing.service import BillingError
from app.modules.billing.models import Order
from app.modules.billing.commerce_models import CoinSource, CommerceRefund, MembershipGrant
from app.modules.common.models import AuditLog
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount
from tests.test_commerce import commercial, membership_order, pay
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


@pytest.mark.parametrize("provider_outcome", ["failed", "canceled"])
def test_real_failed_refund_source_resolution_http_is_cas_safe_and_idempotent(
    session, commercial, make, monkeypatch, provider_outcome,
):
    user, original_actor, _network_calls = commercial
    point = datetime.now(timezone.utc)
    monkeypatch.setattr(commerce, "now", lambda: point)
    first = membership_order(session, user, "http-resolution-first")
    pay(session, first)
    # B's Checkout already exists before A freezes; its delayed callback is a
    # real external race, not an attempt to reopen checkout during a refund.
    checkout = commerce.checkout(session, user_id=user.id, email=user.email, product_type="membership", code="monthly", idempotency_key="http-resolution-second")
    second = session.scalar(select(Order).where(Order.order_no == checkout["order_no"]))
    original_remote = commerce.stripe_request
    def pending(db, code, path, data=None, method="POST", idempotency_key=None):
        if path == "/refunds":
            return {"id": "re_http_resolution", "status": "pending", "amount": first.amount_cents, "currency": "usd"}
        return original_remote(db, code, path, data, method, idempotency_key)
    monkeypatch.setattr(commerce, "stripe_request", pending)
    commerce.request_refund(session, first.order_no, original_actor.id, "Unused original source", "http-refund-first")
    pay(session, second)
    second_grant = session.scalar(select(MembershipGrant).where(MembershipGrant.order_id == second.id))
    second_window = (second_grant.starts_at, second_grant.ends_at)
    commerce.process_event(session, "env", {"type": "refund.updated", "data": {"object": {
        "id": "re_http_resolution", "payment_intent": first.payment_intent_id,
        "status": provider_outcome, "amount": first.amount_cents, "currency": "usd"}}})
    point = second_grant.ends_at
    client = TestClient(app)
    actor, headers = authorized_finance(session, make, client)
    detail_path = "/admin/v1/commerce/orders/" + first.order_no
    action_path = "/admin/v1/finance/orders/" + first.order_no + "/membership-refund-resolution"
    detail = client.get(detail_path, headers=headers)
    assert detail.status_code == 200, detail.text
    metadata = detail.json()["membership_refund_resolution"]
    assert metadata["required"] and metadata["can_resolve"] and not metadata["resolved"]
    assert metadata["provider_status"] == provider_outcome
    assert metadata["disposition"] == "restore_remaining"
    assert metadata["remaining_seconds"] == 30 * 86400
    body = {"reason": "Verified original provider failure; restore only the purchased source", "idempotency_key": "http-source-resolution-once", "expected_revision": metadata["revision"]}

    def state():
        session.expire_all()
        order = session.get(Order, first.id)
        grant = session.scalar(select(MembershipGrant).where(MembershipGrant.order_id == first.id))
        source = session.scalar(select(CoinSource).where(CoinSource.order_id == first.id))
        refund = session.scalar(select(CommerceRefund).where(CommerceRefund.order_id == first.id))
        return (order.status, deepcopy(order.checkout_context), grant.status, grant.starts_at, grant.ends_at,
                source.remaining, source.reserved, refund.status, session.get(WalletAccount, user.id).balance,
                session.scalar(select(func.count()).select_from(CoinLedgerEntry)))
    before = state()
    bad = client.post(action_path, headers=headers, json={**body, "expected_revision": "0" * 64})
    assert bad.status_code == 409, bad.text
    assert bad.json()["detail"]["code"] == "membership_resolution_changed"
    assert bad.json()["detail"]["committed"] is False
    assert state() == before

    def forbid_provider(*args, **kwargs):
        raise AssertionError("Manual source resolution must not request another Stripe refund or charge")
    monkeypatch.setattr(commerce, "stripe_request", forbid_provider)
    response = client.post(action_path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == set(RESULT)
    assert result["order_no"] == first.order_no and result["resolved"] and result["restored"]
    assert result["released_coins"] == 25 and not result["replayed"]
    assert datetime.fromisoformat(result["starts_at"]) == point
    assert datetime.fromisoformat(result["ends_at"]) - point == timedelta(days=30)
    after = state()
    assert after[8] == before[8] + 25
    assert (session.get(MembershipGrant, second_grant.id).starts_at, session.get(MembershipGrant, second_grant.id).ends_at) == second_window
    assert not session.get(MembershipGrant, second_grant.id).access_consumed
    replay = client.post(action_path, headers=headers, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == {**result, "replayed": True}
    assert state() == after
    resolved = client.get(detail_path, headers=headers).json()["membership_refund_resolution"]
    assert resolved["resolved"] and not resolved["required"]
    assert resolved["result"] == result
    duplicate = client.post(action_path, headers=headers, json={**body, "idempotency_key": "different-resolution-key"})
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["detail"]["code"] == "membership_resolution_already_resolved"
    assert state() == after
    audit = list(session.scalars(select(AuditLog).where(AuditLog.action == "membership_source.resolve", AuditLog.target_id == first.id)))
    assert len(audit) == 1 and audit[0].actor_id == actor.id and audit[0].tenant_id == "jjtv"
    assert audit[0].payload["reason"] == body["reason"]
    assert session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.biz_type == "refund_release", CoinLedgerEntry.biz_key == "refund_release:" + first.order_no)) == 1
