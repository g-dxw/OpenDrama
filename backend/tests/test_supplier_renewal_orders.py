"""Renewal receipts are retained for billing, never supplier first-order commission.

These tests use real migrations/PostgreSQL and the existing mocked Stripe fixture.
"""
from copy import deepcopy
from datetime import datetime, timezone
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.billing import commerce as commerce
from app.modules.billing import service as billing
from app.modules.billing.models import Order, Plan, PlanVersion
from app.modules.ops import settlements
from app.modules.ops.models import Supplier
from tests.test_commerce import commercial, pay
from tests.test_partner_p4 import _setup

UTC = timezone.utc


@pytest.fixture
def client(session):
    return TestClient(app)


@pytest.fixture
def supplier_case(session, make, client):
    admin_headers, partner_headers, drama = _setup(make, client, session)
    supplier = session.get(Supplier, drama.supplier_id)
    return supplier, drama, admin_headers, partner_headers


def subscription_orders(session, commercial, drama, *, invoice_order=(1, 2), billing_reasons=False):
    user, actor, _ = commercial
    billing.get_billing_settings(session).billing_mode = "subscription"
    plan = Plan(code="monthly", name_en="Monthly")
    session.add(plan); session.flush()
    version = PlanVersion(plan_id=plan.id, version=1, price_cents=1999,
        base_coins=20, bonus_coins=5, benefits={})
    session.add(version); session.commit()
    result = commerce.checkout(session, user_id=user.id, email=user.email,
        product_type="membership", code="monthly", idempotency_key="supplier-first-purchase",
        source_drama_id=drama.id)
    initial = session.scalar(select(Order).where(Order.order_no == result["order_no"]))
    subscription = "sub_supplier_" + str(user.id)
    commerce.process_event(session, "env", {"type":"checkout.session.completed", "data":{"object":{
        "id":initial.external_txn_id, "mode":"subscription", "payment_status":"paid",
        "amount_total":1999, "currency":"usd", "subscription":subscription,
        "metadata":{"commerce_order_no":initial.order_no}}}})
    stamp = int(time.time())
    def invoice(number):
        return {"type":"invoice.paid", "data":{"object":{
            "id":"in_supplier_" + str(number), "subscription":subscription,
            "customer":"cus_supplier", "payment_intent":"pi_supplier_" + str(number),
            "amount_paid":1999, "currency":"usd", "lines":{"data":[{"period":{
                "start":stamp + (number - 1) * 30 * 86400,
                "end":stamp + number * 30 * 86400}}]}}}}
    invoices = {number:invoice(number) for number in (1, 2)}
    if billing_reasons:
        for number, payload in invoices.items():
            payload["data"]["object"]["billing_reason"] = (
                "subscription_create" if number == 1 else "subscription_cycle")
    checkout_id = initial.id
    for number in invoice_order:
        commerce.process_event(session, "env", invoices[number])
    initial = session.scalar(select(Order).where(Order.invoice_id == "in_supplier_1"))
    renewal = session.scalar(select(Order).where(Order.invoice_id == "in_supplier_2"))
    first_received = initial if invoice_order[0] == 1 else renewal
    later_received = renewal if invoice_order[0] == 1 else initial
    assert first_received.id == checkout_id and first_received.checkout_key
    assert later_received.checkout_key is None and later_received.subscription_ref == subscription
    assert initial.source_drama_id == renewal.source_drama_id == drama.id
    return user, actor, initial, renewal, invoices[2]


def refund_initial(session, actor, initial):
    result = commerce.request_refund(session, initial.order_no, actor.id,
        "Unused original period", "supplier-first-refund")
    assert result["status"] == "succeeded"
    session.refresh(initial)
    assert initial.status == "refunded" and initial.refunded_at is not None


def period():
    return settlements.month_bounds(datetime.now(UTC).strftime("%Y-%m"))


@pytest.mark.parametrize("report", ["helper", "overview", "dramas", "statement"])
def test_refunded_initial_does_not_turn_a_renewal_into_supplier_first_order(
    session, commercial, supplier_case, client, report,
):
    supplier, drama, admin_headers, partner_headers = supplier_case
    user, actor, initial, renewal, replay = subscription_orders(session, commercial, drama)
    month = datetime.now(UTC).strftime("%Y-%m")
    baseline = client.post("/admin/v1/settlements/generate", json={"month":month}, headers=admin_headers)
    assert baseline.status_code == 200, baseline.text
    statement = client.get("/admin/v1/settlements?month="+month, headers=admin_headers).json()["items"][0]
    assert statement["order_count"] == 1 and statement["gross_cents"] == 1999
    refund_initial(session, actor, initial)
    commerce.process_event(session, "env", replay)
    commerce.process_event(session, "env", replay)
    assert session.scalar(select(func.count()).select_from(Order).where(Order.user_id == user.id)) == 2
    session.refresh(renewal)
    assert renewal.status == "paid" and renewal.source_drama_id == initial.source_drama_id == drama.id
    if report == "helper":
        assert settlements.first_orders_for_supplier(session, supplier.id, *period()) == []
    elif report == "overview":
        response = client.get("/partner/v1/stats/overview", headers=partner_headers)
        assert response.status_code == 200, response.text
        money = response.json()
        assert (money["first_orders"], money["gross_cents"], money["revenue_cents"]) == (0, 0, 0)
    elif report == "dramas":
        response = client.get("/partner/v1/stats/dramas", headers=partner_headers)
        assert response.status_code == 200, response.text
        money = next(item for item in response.json()["items"] if item["id"] == drama.id)
        assert (money["first_orders"], money["gross_cents"], money["revenue_cents"]) == (0, 0, 0)
    else:
        for _ in range(2):
            regenerated = client.post("/admin/v1/settlements/generate", json={"month":month}, headers=admin_headers)
            assert regenerated.status_code == 200, regenerated.text
        listed = client.get("/admin/v1/settlements?month="+month, headers=admin_headers).json()
        assert listed["total"] == 1
        money = listed["items"][0]
        assert money["id"] == statement["id"]
        assert (money["order_count"], money["gross_cents"], money["revenue_cents"]) == (0, 0, 0)


def test_managed_initial_invoice_still_qualifies_for_supplier_commission(session, commercial, supplier_case):
    supplier, drama, _, _ = supplier_case
    user, actor, _ = commercial
    # A refunded earlier ordinary purchase must not disqualify the later genuine
    # Checkout invoice when the legacy first-eligible-order ranking advances.
    result = commerce.checkout(session, user_id=user.id, email=user.email,
        product_type="coin_pack", code="starter", idempotency_key="supplier-earlier-purchase",
        source_drama_id=drama.id)
    earlier = session.scalar(select(Order).where(Order.order_no == result["order_no"]))
    pay(session, earlier)
    refund_initial(session, actor, earlier)
    user, _, initial, renewal, replay = subscription_orders(session, commercial, drama)
    commerce.process_event(session, "env", replay)
    orders = settlements.first_orders_for_supplier(session, supplier.id, *period())
    assert [order.id for order in orders] == [initial.id]
    assert initial.invoice_id and initial.subscription_ref and initial.checkout_key
    assert renewal.source_drama_id == drama.id
    assert settlements.compute(session, supplier, *period())["gross_cents"] == 1999


def test_exclude_renewals_before_ranking_the_next_valid_purchase(session, commercial, supplier_case):
    supplier, drama, _, _ = supplier_case
    user, actor, initial, _, _ = subscription_orders(session, commercial, drama)
    refund_initial(session, actor, initial)
    result = commerce.checkout(session, user_id=user.id, email=user.email,
        product_type="coin_pack", code="starter", idempotency_key="supplier-later-purchase",
        source_drama_id=drama.id)
    later = session.scalar(select(Order).where(Order.order_no == result["order_no"]))
    pay(session, later)
    orders = settlements.first_orders_for_supplier(session, supplier.id, *period())
    assert [order.id for order in orders] == [later.id]
    assert settlements.compute(session, supplier, *period())["gross_cents"] == 500


def test_legacy_first_order_without_managed_invoice_fields_still_qualifies(session, make, supplier_case):
    supplier, drama, _, _ = supplier_case
    buyer = make.user(email="legacy-supplier@example.test")
    # Historical orders have none of the three managed-invoice origin fields.
    order = Order(order_no="legacy-supplier-first", user_id=buyer.id, amount_cents=799,
        currency="USD", billing_mode="subscription", status="paid",
        paid_at=datetime.now(UTC), source_drama_id=drama.id, external_txn_id="legacy-checkout")
    session.add(order); session.commit()
    assert order.checkout_key is None and order.invoice_id is None and order.subscription_ref is None
    assert [row.id for row in settlements.first_orders_for_supplier(session, supplier.id, *period())] == [order.id]


@pytest.mark.parametrize("refund_first", [False, True])
def test_invoice_reason_overrides_delivery_order_for_all_supplier_reports(
    session, commercial, supplier_case, client, refund_first,
):
    supplier, drama, admin_headers, partner_headers = supplier_case
    user, actor, initial, renewal, replay = subscription_orders(session, commercial, drama,
        invoice_order=(2, 1), billing_reasons=True)
    # The existing settlement handler attaches whichever invoice arrives first
    # to the Checkout row. Commission must follow invoice origin, not that row.
    assert renewal.checkout_key and initial.checkout_key is None
    if refund_first:
        refund_initial(session, actor, initial)
    commerce.process_event(session, "env", replay)
    commerce.process_event(session, "env", replay)
    expected_ids = [] if refund_first else [initial.id]
    count, gross = (0, 0) if refund_first else (1, 1999)
    assert [row.id for row in settlements.first_orders_for_supplier(session, supplier.id, *period())] == expected_ids
    computed = settlements.compute(session, supplier, *period())
    assert (computed["order_count"], computed["gross_cents"]) == (count, gross)
    for endpoint in ("overview", "dramas"):
        response = client.get("/partner/v1/stats/"+endpoint, headers=partner_headers)
        assert response.status_code == 200, response.text
        money = response.json()
        if endpoint == "dramas":
            money = next(item for item in money["items"] if item["id"] == drama.id)
        assert (money["first_orders"], money["gross_cents"], money["revenue_cents"]) == (
            count, gross, computed["revenue_cents"])
    month = datetime.now(UTC).strftime("%Y-%m")
    for _ in range(2):
        response = client.post("/admin/v1/settlements/generate", json={"month":month}, headers=admin_headers)
        assert response.status_code == 200, response.text
    statements = client.get("/admin/v1/settlements?month="+month, headers=admin_headers).json()
    assert statements["total"] == count
    if count:
        money = statements["items"][0]
        assert (money["order_count"], money["gross_cents"], money["revenue_cents"]) == (
            count, gross, computed["revenue_cents"])
    assert session.scalar(select(func.count()).select_from(Order).where(Order.user_id == user.id)) == 2
    assert initial.source_drama_id == renewal.source_drama_id == drama.id


@pytest.mark.parametrize("invoice_number", [1, 2])
def test_old_invoice_replays_cannot_erase_known_origin(
    session, commercial, supplier_case, invoice_number,
):
    supplier, drama, _, _ = supplier_case
    user, _, initial, renewal, replay = subscription_orders(session, commercial, drama,
        invoice_order=(2, 1), billing_reasons=True)
    order = initial if invoice_number == 1 else renewal
    reason = "subscription_create" if invoice_number == 1 else "subscription_cycle"
    event = deepcopy(replay)
    event["data"]["object"].update(id=order.invoice_id,
        payment_intent=order.payment_intent_id, billing_reason=reason)
    original = deepcopy(order.checkout_context)
    assert original["invoice_billing_reason"] == reason
    for missing_reason in (None, "subscription_update"):
        if missing_reason is None:
            event["data"]["object"].pop("billing_reason", None)
        else:
            event["data"]["object"]["billing_reason"] = missing_reason
        commerce.process_event(session, "env", event)
        session.refresh(order)
        assert order.checkout_context["invoice_billing_reason"] == reason
    assert [row.id for row in settlements.first_orders_for_supplier(session, supplier.id, *period())] == [initial.id]
    assert session.scalar(select(func.count()).select_from(Order).where(Order.user_id == user.id)) == 2


@pytest.mark.parametrize("invoice_number", [1, 2])
def test_conflicting_known_invoice_origin_is_rejected(
    session, commercial, supplier_case, invoice_number,
):
    supplier, drama, _, _ = supplier_case
    _, _, initial, renewal, replay = subscription_orders(session, commercial, drama,
        invoice_order=(2, 1), billing_reasons=True)
    order = initial if invoice_number == 1 else renewal
    original_reason = "subscription_create" if invoice_number == 1 else "subscription_cycle"
    event = deepcopy(replay)
    event["data"]["object"].update(id=order.invoice_id,
        payment_intent=order.payment_intent_id,
        billing_reason="subscription_cycle" if invoice_number == 1 else "subscription_create")
    with pytest.raises(commerce.BillingError, match="invoice billing reason conflicts"):
        commerce.process_event(session, "env", event)
    session.rollback(); session.refresh(order)
    assert order.checkout_context["invoice_billing_reason"] == original_reason
    assert [row.id for row in settlements.first_orders_for_supplier(session, supplier.id, *period())] == [initial.id]
