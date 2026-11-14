"""Authenticated, source-proven order verification for payment return recovery."""
from itertools import count

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app.main import app
from app.modules.account import service as accounts
from app.modules.billing.commerce_models import CommerceSettlement, CoinSource, MembershipGrant
from app.modules.billing.models import Order, Plan, PlanVersion
from app.modules.wallet.models import WalletAccount


@pytest.fixture
def order_http(session, make):
    owner = make.user("verify-owner@example.com", coins=77)
    other = make.user("verify-other@example.com", coins=23)
    plan = Plan(code="monthly", name_en="Current mutable name")
    session.add(plan)
    session.flush()
    version = PlanVersion(plan_id=plan.id, version=1, price_cents=1999, benefits={})
    session.add(version)
    session.commit()
    sequence = count(1)

    def create(*, user=owner, status="paid", settled=False, kind="membership", snapshot=None):
        number = next(sequence)
        row = Order(order_no="VERIFY-" + str(number), user_id=user.id,
                    plan_version_id=version.id if kind == "membership" else None,
                    product_type=kind, product_snapshot=snapshot if snapshot is not None else {
                        "code": "frozen-monthly" if kind == "membership" else "coin-pack-50",
                        "name": "PRIVATE_SNAPSHOT_NAME", "benefits": {"private": "PRIVATE_BENEFITS"}},
                    amount_cents=1999, currency="USD", billing_mode="one_time", status=status,
                    account_code="PRIVATE_MERCHANT", checkout_key="PRIVATE_KEY_" + str(number),
                    checkout_url="https://checkout.stripe.com/PRIVATE_URL_" + str(number),
                    checkout_context={"request": {"email": "PRIVATE_EMAIL@example.com"}},
                    external_txn_id="cs_PRIVATE_" + str(number), payment_intent_id="pi_PRIVATE_" + str(number),
                    invoice_id="in_PRIVATE_" + str(number), subscription_ref="sub_PRIVATE_" + str(number))
        session.add(row)
        session.flush()
        if settled:
            session.add(CommerceSettlement(key="PRIVATE_SETTLEMENT_" + str(number), order_id=row.id))
        session.commit()
        return row

    headers = {"Authorization": "Bearer " + accounts._issue_session(owner)}
    return TestClient(app), owner, other, headers, create


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid-viewer-token"}])
def test_order_verification_requires_a_viewer_session(order_http, headers):
    client, _owner, _other, _owner_headers, create = order_http
    row = create(settled=True)
    for order_no in (row.order_no, "missing-order"):
        response = client.get("/v1/commerce/orders/" + order_no, headers=headers)
        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "login required"}


def test_foreign_and_missing_order_are_indistinguishable(order_http):
    client, _owner, other, headers, create = order_http
    foreign = create(user=other, settled=True)
    responses = [client.get("/v1/commerce/orders/" + number, headers=headers)
                 for number in (foreign.order_no, "missing-order")]
    assert [response.status_code for response in responses] == [404, 404]
    assert responses[0].json() == responses[1].json() == {"detail": "order not found"}


@pytest.mark.parametrize(("status", "settled"), [
    ("paid", True), ("paid", False), ("created", False), ("pending", False),
    ("failed", False), ("refund_pending", True), ("refunded", True),
    ("refund_review", True), ("refunded", False), ("created", True),
])
def test_status_and_settlement_proof_are_independent_and_private(order_http, session, status, settled):
    client, owner, other, headers, create = order_http
    row = create(status=status, settled=settled)
    # Neither another customer's payment nor this customer's other settled
    # order is proof that the requested/imported paid order was fulfilled.
    create(user=other, settled=True)
    create(settled=True)
    before = (session.get(WalletAccount, owner.id).balance,
              session.scalar(select(func.count()).select_from(CoinSource)),
              session.scalar(select(func.count()).select_from(MembershipGrant)),
              session.scalar(select(func.count()).select_from(CommerceSettlement)))
    for _ in range(2):
        response = client.get("/v1/commerce/orders/" + row.order_no, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"order": {"order_no": row.order_no, "product_type": "membership",
                                            "product_code": "frozen-monthly", "status": status},
                                   "settled": settled}
        assert "private" in response.headers["Cache-Control"]
        assert "no-store" in response.headers["Cache-Control"]
        assert "PRIVATE" not in response.text
    session.expire_all()
    assert before == (session.get(WalletAccount, owner.id).balance,
                      session.scalar(select(func.count()).select_from(CoinSource)),
                      session.scalar(select(func.count()).select_from(MembershipGrant)),
                      session.scalar(select(func.count()).select_from(CommerceSettlement)))
    assert session.get(Order, row.id).status == status


def test_coin_product_and_legacy_membership_code_compatibility(order_http):
    client, _owner, _other, headers, create = order_http
    coin = create(kind="coin_pack", status="created")
    legacy = create(snapshot={})
    for row, kind, code in ((coin, "coin_pack", "coin-pack-50"), (legacy, "membership", "monthly")):
        response = client.get("/v1/commerce/orders/" + row.order_no, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"order": {"order_no": row.order_no, "product_type": kind,
                                            "product_code": code, "status": row.status}, "settled": False}


def test_tenant_selector_cannot_expand_public_jjtv_ownership(order_http):
    client, _owner, other, headers, create = order_http
    own = create(settled=True)
    foreign = create(user=other, settled=True)
    selected = {**headers, "X-JJTV-Tenant": "india-short-drama"}
    assert client.get("/v1/commerce/orders/" + own.order_no, headers=selected).status_code == 200
    assert client.get("/v1/commerce/orders/" + foreign.order_no, headers=selected).status_code == 404


def test_staff_session_does_not_grant_viewer_order_access(order_http, make):
    client, _owner, _other, _headers, create = order_http
    row = create(settled=True)
    admin = make.admin(role="admin")
    login = client.post("/admin/v1/auth/login", json={"tenant_code": "jjtv", "username": admin.username, "password": "pass-1234"})
    assert login.status_code == 200, login.text
    staff = {"Authorization": "Bearer " + login.json()["token"]}
    assert client.get("/v1/commerce/orders/" + row.order_no, headers=staff).status_code == 401


def test_unknown_product_code_is_empty_and_snapshot_objects_are_not_returned(order_http):
    client, _owner, _other, headers, create = order_http
    unknown = create(kind="coin_pack", snapshot={})
    malformed = create(snapshot={"code": {"private": "PRIVATE_SNAPSHOT_OBJECT"}})
    for row, code in ((unknown, ""), (malformed, "monthly")):
        response = client.get("/v1/commerce/orders/" + row.order_no, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["order"]["product_code"] == code
        assert "PRIVATE" not in response.text
