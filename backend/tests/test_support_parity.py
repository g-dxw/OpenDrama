"""Customer ownership, internal-note privacy, SLA and real assignment workflows."""
import copy
from datetime import datetime,timedelta,timezone
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.modules.account import service as accounts
from app.modules.support import service
from app.modules.support.models import SupportTicket,SupportMessage,SupportEvent
UTC=timezone.utc

@pytest.fixture()
def client(session): return TestClient(app)

def user_headers(user): return {"Authorization":"Bearer "+accounts._issue_session(user)}
def staff_headers(client,make,role="admin",tenant_id="jjtv"):
    staff=make.admin(role=role,tenant_id=tenant_id)
    response=client.post("/admin/v1/auth/login",json={"tenant_code":tenant_id,"username":staff.username,"password":"pass-1234"})
    assert response.status_code==200,response.text
    return staff,{"Authorization":"Bearer "+response.json()["token"]}

def create(client,user,**extra):
    response=client.post("/v1/support/tickets",headers=user_headers(user),json={"subject":"Payment help","category":"billing","body":"Where is my purchase?",**extra})
    assert response.status_code==201,response.text
    return response.json()["ticket"]["number"]

def test_customer_ownership_and_private_notes_never_escape(session,make,client):
    user=make.user("support-owner@example.com");other=make.user("support-other@example.com")
    _,headers=staff_headers(client,make)
    number=create(client,user)
    note=client.post(f"/admin/v1/support/tickets/{number}/messages",headers=headers,json={"body":"Private fraud review","internal":True})
    assert note.status_code==200,note.text
    assert note.json()["ticket"]["first_response_at"] is None
    assert note.json()["ticket"]["status"]=="open"
    public=client.get(f"/v1/support/tickets/{number}",headers=user_headers(user)).json()
    assert [m["body"] for m in public["messages"]]==["Where is my purchase?"]
    assert "events" not in public and "customer" not in public
    for path in ("", "/read", "/close"):
        response=client.get(f"/v1/support/tickets/{number}",headers=user_headers(other)) if not path else client.post(f"/v1/support/tickets/{number}{path}",headers=user_headers(other),json={})
        assert response.status_code==404
    reply=client.post(f"/admin/v1/support/tickets/{number}/messages",headers=headers,json={"body":"Let us check this for you"})
    assert reply.status_code==200,reply.text
    assert reply.json()["ticket"]["status"]=="waiting_customer"
    assert reply.json()["ticket"]["first_response_at"]
    public=client.get(f"/v1/support/tickets/{number}",headers=user_headers(user)).json()
    assert len(public["messages"])==2 and public["ticket"]["unread_count"]==1
    assert all("internal" not in m and "author_id" not in m for m in public["messages"])
    assert client.post(f"/v1/support/tickets/{number}/read",headers=user_headers(user)).status_code==200
    assert client.get(f"/v1/support/tickets/{number}",headers=user_headers(user)).json()["ticket"]["unread_count"]==0

def test_business_calendar_holiday_weekend_and_pause():
    calendar={"timezone":"UTC","weekdays":[0,1,2,3,4],"start":"09:00","end":"17:00","holidays":["2026-11-16"]}
    # Friday 16:00 -> Tuesday 10:00 excludes weekend and Monday holiday.
    assert service.business_minutes(datetime(2026,11,13,16,tzinfo=UTC),datetime(2026,11,17,10,tzinfo=UTC),calendar)==120
    assert service.business_minutes(datetime(2026,11,17,10,tzinfo=UTC),datetime(2026,11,17,9,tzinfo=UTC),calendar)==0

def test_waiting_customer_pauses_sla_without_rewriting_calendar(session,make,monkeypatch):
    user=make.user();stamp=datetime(2026,11,16,9,tzinfo=UTC)
    config=copy.deepcopy(service.DEFAULT_CONFIG);config["calendar"]["timezone"]="UTC";config["calendar"]["end"]="17:00"
    service.save_settings(session,config,0,1);session.commit()
    monkeypatch.setattr(service,"now",lambda:stamp)
    ticket=service.create_ticket(session,user.id,{"subject":"Help","body":"Question","category":"general"});session.commit()
    stamp+=timedelta(hours=1);service.add_message(session,ticket,"Please share details",1,admin=True);session.commit()
    stamp+=timedelta(hours=23)
    assert service.sla(ticket)["first_response_minutes"]==60
    assert service.sla(ticket)["resolution_minutes"]==60
    service.add_message(session,ticket,"Here are details",user.id);session.commit()
    assert ticket.status=="open" and ticket.waiting_since is None
    stamp+=timedelta(hours=1)
    assert service.sla(ticket)["resolution_minutes"]==120
    config["calendar"]["timezone"]="Asia/Shanghai";service.save_settings(session,config,1,1);session.commit()
    assert ticket.sla_policy["calendar"]["timezone"]=="UTC"

def test_round_robin_limits_and_dynamic_fields(session,make):
    user=make.user();a=make.admin();b=make.admin()
    config=copy.deepcopy(service.DEFAULT_CONFIG)
    config["teams"][0].update(members=[a.id,b.id],max_concurrent=1)
    config["assignment"]="round_robin"
    config["categories"][0]["fields"]=[{"key":"device","type":"select","required":True,"options":["PC","Mobile"],"label_en":"Device"}]
    service.save_settings(session,config,0,a.id);session.commit()
    with pytest.raises(HTTPException): service.create_ticket(session,user.id,{"subject":"Help","body":"Test","category":"general"})
    data={"subject":"Help","body":"Test","category":"general","fields":{"device":"PC"}}
    first=service.create_ticket(session,user.id,data);session.commit()
    second=service.create_ticket(session,user.id,data);session.commit()
    third=service.create_ticket(session,user.id,data);session.commit()
    assert {first.assignee_id,second.assignee_id}=={a.id,b.id}
    assert third.assignee_id is None

def test_merge_ownership_cycles_and_messages(session,make):
    user=make.user();other=make.user("other-merge@example.com")
    data={"subject":"Help","body":"Hello","category":"general"}
    a=service.create_ticket(session,user.id,data);b=service.create_ticket(session,user.id,data);c=service.create_ticket(session,other.id,data);session.commit()
    with pytest.raises(HTTPException):service.merge(session,a,c,1)
    with pytest.raises(HTTPException):service.merge(session,a,a,1)
    service.add_message(session,a,"Internal only",1,True,True);service.merge(session,a,b,1);session.commit()
    assert a.status=="merged" and a.merged_into_id==b.id
    assert len(service.detail(session,b)["messages"])==2
    assert len(service.detail(session,b,True)["messages"])==3
    with pytest.raises(HTTPException):service.merge(session,b,a,1)

def test_bulk_partial_results_csat_and_report_samples(session,make,client):
    user=make.user();_,headers=staff_headers(client,make);number=create(client,user)
    response=client.post("/admin/v1/support/tickets/bulk",headers=headers,json={"numbers":[number,"TK-NOT-FOUND"],"changes":{"status":"resolved"}})
    assert response.status_code==200,response.text
    assert [r["ok"] for r in response.json()["items"]]==[True,False]
    rated=client.post(f"/v1/support/tickets/{number}/csat",headers=user_headers(user),json={"score":5,"comment":"Thanks"})
    assert rated.status_code==200,rated.text
    report=client.get("/admin/v1/support/reports?days=7",headers=headers)
    assert report.status_code==200,report.text
    assert report.json()["csat_average"]==5
    assert report.json()["first_response_sla_rate"] is None

def test_support_rejects_disabled_module_and_cross_tenant(session,make,client):
    _,finance=staff_headers(client,make,"finance")
    denied=client.get("/admin/v1/support/tickets",headers=finance)
    assert denied.status_code==403,denied.text
    _,admin=staff_headers(client,make)
    # A foreign tenant selector cannot change the authenticated tenant.
    assert client.get("/admin/v1/support/tickets",headers={**admin,"X-JJTV-Tenant":"india-short-drama"}).status_code==403

def test_public_rejects_blank_and_foreign_orders(session,make,client):
    user=make.user()
    assert client.post("/v1/support/tickets",headers=user_headers(user),json={"subject":"   ","body":"x"}).status_code==422
    assert client.post("/v1/support/tickets",headers=user_headers(user),json={"subject":"x","body":"x","order_no":"FOREIGN"}).status_code==422
    assert client.get("/v1/support/tickets").status_code==401
