"""Real queue badges, team filters and full-day calendars used by the desktop desk."""
import copy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.modules.account import service as accounts
from app.modules.support import service
from app.modules.support.models import SupportTicket
from app.modules.tenancy.models import TenantMember, TenantRole

UTC = timezone.utc


@pytest.fixture()
def client(session):
    return TestClient(app)


def staff_login(client, make, role="admin"):
    staff = make.admin(role=role)
    response = client.post("/admin/v1/auth/login", json={
        "tenant_code": "jjtv", "username": staff.username, "password": "pass-1234",
    })
    assert response.status_code == 200, response.text
    return staff, {"Authorization": "Bearer " + response.json()["token"]}


def test_queue_badges_stay_global_when_search_team_and_page_change(session, make, client, monkeypatch):
    stamp = datetime(2026, 11, 16, 12, tzinfo=UTC)
    monkeypatch.setattr(service, "now", lambda: stamp)
    user = make.user("queue-owner@example.com")
    staff, headers = staff_login(client, make)
    other = make.admin()
    config = copy.deepcopy(service.DEFAULT_CONFIG)
    config["calendar"] = {"timezone": "UTC", "weekdays": list(range(7)), "start": "00:00", "end": "24:00", "holidays": []}
    config["teams"].append({"code": "billing", "name": "Billing", "members": [], "max_concurrent": 10})
    service.save_settings(session, config, 0, staff.id)
    tickets = []
    for index, status in enumerate(["open", "in_progress", "waiting_customer", "resolved", "closed", "merged"]):
        ticket = service.create_ticket(session, user.id, {"subject": "Queue item " + str(index), "body": "Local question"})
        ticket.status = status
        ticket.assignee_id = staff.id if index == 0 else other.id if index == 2 else None
        ticket.team_code = "billing" if index == 0 else "default"
        ticket.created_at = stamp - timedelta(hours=2) if index == 0 else stamp
        ticket.updated_at = ticket.created_at
        if index == 2:
            ticket.first_response_at = stamp
            ticket.waiting_since = stamp
        tickets.append(ticket)
    # A future/foreign tenant row must never influence JJTV queue totals.
    from app.modules.account.models import User
    foreign_user=User(tenant_id="india-short-drama",email="foreign-queue@example.com",display_name="Foreign viewer")
    session.add(foreign_user);session.flush()
    foreign = SupportTicket(tenant_id="india-short-drama", user_id=foreign_user.id, number="TK-FOREIGN-QUEUE", subject="Foreign", status="open", priority="normal", team_code="billing", sla_policy=config["sla"]["normal"], created_at=stamp, updated_at=stamp)
    session.add(foreign)
    session.commit()
    response = client.get("/admin/v1/support/tickets", headers=headers, params={
        "q": "Queue item 0", "team_code": "billing", "page_size": 1,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert [t["number"] for t in body["items"]] == [tickets[0].number]
    assert body["counts"] == {"active": 3, "mine": 1, "unassigned": 1, "waiting_customer": 1, "overdue": 1}
    empty_page = client.get("/admin/v1/support/tickets", headers=headers, params={"q": "no match", "page": 9}).json()
    assert empty_page["items"] == []
    assert empty_page["counts"] == body["counts"]
    overdue = client.get("/admin/v1/support/tickets", headers=headers, params={"queue": "overdue"}).json()
    assert [t["number"] for t in overdue["items"]] == [tickets[0].number]
    active = client.get("/admin/v1/support/tickets", headers=headers, params={"queue": "active"}).json()
    assert active["total"] == 3
    public = client.get("/v1/support/tickets", headers={"Authorization": "Bearer " + accounts._issue_session(user)}).json()
    assert "counts" not in public
    assert all(t["number"] != foreign.number for t in public["items"])


def test_priority_triage_orders_before_pagination_and_rejects_unknown_sort(session, make, client):
    user = make.user()
    _, headers = staff_login(client, make)
    for priority in ["low", "normal", "urgent", "high"]:
        ticket = service.create_ticket(session, user.id, {"subject": priority, "body": "Triage question"})
        ticket.priority = priority
    session.commit()
    response = client.get("/admin/v1/support/tickets", headers=headers, params={"sort": "priority", "page_size": 2})
    assert response.status_code == 200, response.text
    assert [t["priority"] for t in response.json()["items"]] == ["urgent", "high"]
    second = client.get("/admin/v1/support/tickets", headers=headers, params={"sort": "priority", "page_size": 2, "page": 2}).json()
    assert [t["priority"] for t in second["items"]] == ["normal", "low"]
    assert client.get("/admin/v1/support/tickets?sort=invalid", headers=headers).status_code == 422
    assert client.get("/admin/v1/support/tickets?queue=invalid", headers=headers).status_code == 422


def test_read_only_agent_metadata_does_not_require_settings_permission(session, make, client):
    staff = make.admin(role="ops")
    member = session.get(TenantMember, ("jjtv", staff.id))
    role = session.get(TenantRole, member.role_id)
    role.permissions = ["support.view"]
    session.commit()
    response = client.post("/admin/v1/auth/login", json={"tenant_code": "jjtv", "username": staff.username, "password": "pass-1234"})
    assert response.status_code == 200, response.text
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    metadata = client.get("/admin/v1/support/agents", headers=headers)
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["teams"] == [{"code": "default", "name": "客服组"}]
    assert "members" not in str(metadata.json()["teams"])
    assert client.get("/admin/v1/support/settings", headers=headers).status_code == 403


def test_full_day_calendar_counts_dst_and_preserves_ticket_snapshot(session, make):
    config = copy.deepcopy(service.DEFAULT_CONFIG)
    config["calendar"] = {"timezone": "America/Los_Angeles", "weekdays": list(range(7)), "start": "00:00", "end": "24:00", "holidays": []}
    service.save_settings(session, config, 0, 1)
    user = make.user()
    ticket = service.create_ticket(session, user.id, {"subject": "Full day", "body": "Question"})
    session.commit()
    # Fall-back Sunday has 25 real working hours, not a hardcoded 24 hours.
    assert service.business_minutes(datetime(2026, 11, 1, 7, tzinfo=UTC), datetime(2026, 11, 2, 8, tzinfo=UTC), config["calendar"]) == 1500
    config["calendar"]["end"] = "18:00"
    service.save_settings(session, config, 1, 1)
    session.commit()
    assert ticket.sla_policy["calendar"]["end"] == "24:00"
    invalid = copy.deepcopy(config)
    invalid["calendar"]["start"] = "24:00"
    with pytest.raises(HTTPException):
        service.validate_settings(session, invalid)
