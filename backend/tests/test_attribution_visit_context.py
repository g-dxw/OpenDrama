"""Real landing visits keep one browser's click context through signup and purchase."""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select, func

from app.main import app
from app.modules.account import service as accounts
from app.modules.attribution import service
from app.modules.attribution.models import (
    AttributionEvent, AttributionPlan, AttributionSource,
    AttributionVisit, UserAttribution,
)
from app.modules.billing.models import Order
from app.modules.tenancy.models import Tenant


@pytest.fixture()
def campaign(session, monkeypatch):
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    pixel = service.save_pixel(session, {
        "name": "Click context fixture", "platform": "meta", "pixel_id": "123456789",
        "token": "context-fixture-token", "enabled": True,
        "delivery_mode": "test", "test_event_code": "TEST-CONTEXT",
    }, 1)
    source = AttributionSource(tenant_id="jjtv", name="Source", pixel_id=pixel.id, enabled=True)
    session.add(source)
    session.flush()
    plan = AttributionPlan(tenant_id="jjtv", name="Plan", source_id=source.id,
                           pixel_id=pixel.id, pid="context-pid", landing_path="/", enabled=True)
    session.add(plan)
    session.commit()
    return pixel, plan


def signup(client, monkeypatch, user, plan, device="browser-a", endpoint="email/authenticate"):
    result = (user, "fixture-token", True)
    data = {"email": user.email, "code": "123456"}
    if endpoint == "email/authenticate":
        monkeypatch.setattr(accounts, "login_or_register_with_code", lambda *args, **kwargs: result)
    elif endpoint == "email/register":
        monkeypatch.setattr(accounts, "register", lambda *args, **kwargs: result[:2])
    elif endpoint == "google":
        monkeypatch.setattr(accounts, "login_with_google", lambda *args, **kwargs: result)
        data = {"id_token": "fixture-google-token"}
    else:
        monkeypatch.setattr(accounts, "login_with_facebook", lambda *args, **kwargs: result)
        data = {"access_token": "fixture-facebook-token"}
    headers = {"X-JJTV-Attribution": plan.pid, "user-agent": "Registration browser"}
    if device is not None:
        headers["X-Device-Id"] = device
    response = client.post("/v1/auth/" + endpoint, headers=headers, json=data)
    assert response.status_code == 200, response.text


def land(client, plan, device, suffix="a", **values):
    headers = {"user-agent": "Landing browser"}
    if device is not None:
        headers["X-Device-Id"] = device
    response = client.post("/v1/attr/visit", headers=headers, json={
        "pid": plan.pid, "fbclid": "meta-click-" + suffix,
        "ttclid": "tiktok-click-" + suffix, **values,
    })
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("endpoint", ["email/authenticate", "email/register", "google", "facebook"])
@pytest.mark.parametrize("platform", ["meta", "tiktok"])
def test_browser_landing_signup_and_payment_keep_the_same_click(
    session, make, campaign, monkeypatch, endpoint, platform,
):
    pixel, plan = campaign
    pixel.platform = platform
    session.commit()
    user = make.user()
    client = TestClient(app)
    # Both consumers send X-Device-Id; H5 and the first PC request omit body.client_id.
    land(client, plan, "browser-a", client_id="untrusted-body-client")
    land(client, plan, "browser-b", "b")
    signup(client, monkeypatch, user, plan, endpoint=endpoint)
    identity = session.get(UserAttribution, user.id)
    assert identity.context["fbclid"] == "meta-click-a"
    assert identity.context["ttclid"] == "tiktok-click-a"
    assert identity.context["user_agent"] == "Registration browser"
    visit = session.scalar(select(AttributionVisit).where(AttributionVisit.client_id == "browser-a"))
    assert visit is not None
    registration = session.scalar(select(AttributionEvent).where(AttributionEvent.kind == "registration"))
    assert registration.context == identity.context
    sub = make.subscription(user)
    order = Order(order_no="CLICK-ORDER", user_id=user.id, plan_version_id=sub.plan_version_id,
                  amount_cents=499, currency="USD", status="paid")
    session.add(order)
    session.commit()
    purchase = service.record_payment(session, order_no=order.order_no, user_id=user.id,
                                      pid=plan.pid, amount_cents=499, currency="USD")[0]
    session.commit()
    for event in (registration, purchase):
        payload = service.provider_payload(session, event, pixel)
        if platform == "meta":
            assert payload["data"][0]["user_data"]["fbc"].endswith(".meta-click-a")
        else:
            assert payload["data"][0]["user"]["ttclid"] == "tiktok-click-a"
        assert "click-b" not in str(payload)


@pytest.mark.parametrize("device", [None, "browser-other", "x" * 101])
def test_signup_without_a_matching_device_never_uses_another_visitors_clicks(
    session, make, campaign, monkeypatch, device,
):
    _, plan = campaign
    client = TestClient(app)
    land(client, plan, "browser-a")
    user = make.user()
    signup(client, monkeypatch, user, plan, device=device)
    identity = session.get(UserAttribution, user.id)
    assert identity.context == {"user_agent": "Registration browser"}


def test_matching_is_tenant_and_plan_scoped_with_legacy_body_fallback(
    session, make, campaign, monkeypatch,
):
    pixel, plan = campaign
    other = AttributionPlan(tenant_id="jjtv", name="Other", source_id=plan.source_id,
                            pixel_id=pixel.id, pid="context-other", enabled=True)
    foreign = Tenant(id="context-foreign", code="context-foreign", name="Foreign",
                     kind="native", status="active")
    session.add_all([other, foreign])
    session.flush()
    # Even malformed foreign data sharing a plan FK must not supply JJTV context.
    session.add(AttributionVisit(id="foreign-poison", tenant_id=foreign.id, plan_id=plan.id,
                                client_id="browser-a", context={"fbclid": "foreign-click"}))
    session.commit()
    client = TestClient(app)
    land(client, other, "browser-a", "other")
    first = make.user("first@example.test")
    signup(client, monkeypatch, first, plan)
    assert session.get(UserAttribution, first.id).context == {"user_agent": "Registration browser"}
    land(client, plan, None, client_id="legacy-browser")
    second = make.user("second@example.test")
    signup(client, monkeypatch, second, plan, device="legacy-browser")
    assert session.get(UserAttribution, second.id).context["fbclid"] == "meta-click-a"


def test_revisits_update_known_clicks_but_registration_and_retries_are_immutable(
    session, make, campaign, monkeypatch,
):
    _, plan = campaign
    client = TestClient(app)
    first = land(client, plan, "browser-a")
    latest = land(client, plan, "browser-a", "latest")
    assert latest["visit_id"] == first["visit_id"]  # Keep visit-count deduplication.
    user = make.user()
    signup(client, monkeypatch, user, plan)
    identity = session.get(UserAttribution, user.id)
    assert identity.context["ttclid"] == "tiktok-click-latest"
    original = dict(identity.context)
    # A different later visit, even on the same campaign, cannot rewrite signup.
    land(client, plan, "browser-a", "after-signup")
    signup(client, monkeypatch, user, plan)
    session.expire_all()
    assert session.get(UserAttribution, user.id).context == original
    registration = session.scalar(select(AttributionEvent).where(AttributionEvent.kind == "registration"))
    assert registration.context == original
    assert session.scalar(select(func.count()).select_from(AttributionEvent)) == 1


def test_payment_merges_partial_context_only_for_its_registered_plan(session, make, campaign):
    pixel, plan = campaign
    user = make.user()
    service.record_registration(session, user_id=user.id, pid=plan.pid,
                                context={"fbclid": "signup-click", "ttclid": "signup-tiktok"})
    sub = make.subscription(user)
    order = Order(order_no="PARTIAL-CONTEXT", user_id=user.id, plan_version_id=sub.plan_version_id,
                  amount_cents=499, currency="USD", status="paid")
    other = AttributionPlan(tenant_id="jjtv", name="Other", source_id=plan.source_id,
                            pixel_id=pixel.id, pid="other-plan", enabled=True)
    session.add_all([order, other])
    session.commit()
    event = service.record_payment(session, order_no=order.order_no, user_id=user.id, pid=plan.pid,
                                   amount_cents=499, currency="USD", context={"user_agent": "Payment browser"})[0]
    assert event.context == {"fbclid": "signup-click", "ttclid": "signup-tiktok", "user_agent": "Payment browser"}
    another_order = Order(order_no="OTHER-PLAN-ORDER", user_id=user.id, plan_version_id=sub.plan_version_id,
                          amount_cents=499, currency="USD", status="paid")
    session.add(another_order)
    session.commit()
    foreign_event = service.record_payment(session, order_no=another_order.order_no, user_id=user.id,
                                           pid=other.pid, amount_cents=499, currency="USD")[0]
    assert foreign_event.context == {}
