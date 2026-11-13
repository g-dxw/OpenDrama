"""Persist facts once, protect pixel credentials and retry without duplicate conversions."""
import pytest
from datetime import timedelta
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select,func
from app.modules.attribution import service
from app.modules.attribution.models import AttributionPixel,AttributionSource,AttributionPlan,AttributionEvent,UserAttribution
from app.modules.billing.models import Order

@pytest.fixture()
def pixel(session,monkeypatch):
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY",Fernet.generate_key().decode())
    row=service.save_pixel(session,{"name":"Meta test","platform":"meta","pixel_id":"123456789","token":"secret-test-token","enabled":True,"delivery_mode":"test","test_event_code":"TEST123"},1)
    session.commit();return row
@pytest.fixture()
def plan(session,pixel):
    source=AttributionSource(tenant_id="jjtv",name="Source",pixel_id=pixel.id,enabled=True);session.add(source);session.flush()
    row=AttributionPlan(tenant_id="jjtv",name="Plan",source_id=source.id,pixel_id=pixel.id,pid="test-pid",landing_path="/#/",enabled=True)
    session.add(row);session.commit();return row

def test_credentials_are_encrypted_masked_and_fail_closed(session,pixel,monkeypatch):
    assert "secret-test-token" not in pixel.encrypted_token
    assert "secret-test-token" not in str(service.pixel_out(pixel))
    assert service.decrypt_secret(pixel.encrypted_token)=="secret-test-token"
    monkeypatch.delenv("SETTINGS_ENCRYPTION_KEY")
    with pytest.raises(HTTPException) as error: service.decrypt_secret(pixel.encrypted_token)
    assert error.value.status_code==503

def test_registration_is_first_touch_and_idempotent(session,make,pixel,plan):
    user=make.user();first=service.record_registration(session,user_id=user.id,pid=plan.pid,context={"user_agent":"Test browser"});session.commit()
    second=service.record_registration(session,user_id=user.id,pid=plan.pid);session.commit()
    assert first[0].id==second[0].id
    assert session.scalar(select(func.count()).select_from(AttributionEvent))==1
    other=AttributionPlan(tenant_id="jjtv",name="Other",source_id=plan.source_id,pixel_id=pixel.id,pid="other-pid",landing_path="/",enabled=True);session.add(other);session.commit()
    assert service.record_registration(session,user_id=user.id,pid=other.pid)==[]
    assert session.get(UserAttribution,user.id).plan_id==plan.id

def test_paid_facts_validate_order_and_freeze_dedupe(session,make,pixel,plan):
    user=make.user();sub=make.subscription(user)
    order=Order(order_no="ATTR-ORDER",user_id=user.id,plan_version_id=sub.plan_version_id,amount_cents=1999,currency="USD",status="paid")
    session.add(order);session.commit()
    service.record_registration(session,user_id=user.id,pid=plan.pid);session.commit()
    assert service.record_payment(session,order_no="MISSING",user_id=user.id,pid=plan.pid,amount_cents=1999,currency="USD")==[]
    assert service.record_payment(session,order_no=order.order_no,user_id=user.id,pid=plan.pid,amount_cents=1,currency="USD")==[]
    one=service.record_payment(session,order_no=order.order_no,user_id=user.id,pid=None,amount_cents=1999,currency="USD");session.commit()
    two=service.record_payment(session,order_no=order.order_no,user_id=user.id,pid=None,amount_cents=1999,currency="USD");session.commit()
    assert one[0].id==two[0].id and one[0].event_id=="purchase:ATTR-ORDER"
    stats=service.plan_out(session,plan)
    assert stats["paid_orders"]==1 and stats["paying_users"]==1 and stats["revenue_cents"]=={"USD":1999}

def test_delivery_outbox_retries_stable_id_and_respects_disable(session,make,pixel,plan,monkeypatch):
    user=make.user();event=service.record_registration(session,user_id=user.id,pid=plan.pid)[0];session.commit()
    calls=[]
    def fail(db,e,p):calls.append(e.event_id);return False,{"error":"Temporary provider error"}
    monkeypatch.setattr(service,"send_provider",fail)
    assert service.process_due(session)==[event.id];session.commit()
    assert event.status=="retrying" and event.attempts==1
    assert service.process_due(session)==[]
    event.next_attempt_at=service.now()-timedelta(seconds=1);session.commit()
    monkeypatch.setattr(service,"send_provider",lambda db,e,p:(calls.append(e.event_id) or True,{"status_code":200}))
    service.process_due(session);session.commit()
    assert event.status=="delivered" and len(set(calls))==1 and event.attempts==2
    assert service.process_due(session)==[]
    blocked=service.enqueue(session,pixel_id=pixel.id,event_id="registration:blocked",kind="registration",plan_id=plan.id);session.commit()
    pixel.enabled=False;session.commit();service.process_due(session);session.commit()
    assert blocked.status=="blocked" and blocked.attempts==0

def test_payload_hashes_identity_and_test_events_do_not_count(session,make,pixel,plan):
    user=make.user("matching@example.com")
    event=service.record_registration(session,user_id=user.id,pid=plan.pid,context={"user_agent":"Browser"})[0];session.commit()
    payload=service.provider_payload(session,event,pixel)
    assert payload["test_event_code"]=="TEST123"
    assert "matching@example.com" not in str(payload)
    assert payload["data"][0]["user_data"]["em"]==[service.hashed(user.email)]
    assert payload["data"][0]["event_id"]==event.event_id
    test=service.enqueue(session,pixel_id=pixel.id,event_id="test:1",kind="test",plan_id=plan.id,test=True);session.commit()
    assert service.plan_out(session,plan)["registrations"]==1
    pixel.platform="tiktok"
    payload=service.provider_payload(session,test,pixel)
    assert payload["event_source_id"]==pixel.pixel_id and payload["data"][0]["event_id"]=="test:1"

def test_local_links_disabled_plan_and_visit_dedup(session,pixel,plan):
    for value in ("https://evil.example/","//evil.example/","/\\evil.example/","/x\n"):
        with pytest.raises(HTTPException):service.safe_landing(value)
    assert service.plan_link(plan).startswith("https://jjtv.8jj.net/?pid=test-pid")
    one=service.visit(session,plan.pid,client_id="browser-one");session.commit()
    two=service.visit(session,plan.pid,client_id="browser-one");session.commit()
    assert one["visit_id"]==two["visit_id"] and one["pixel"]["pixel_id"]==pixel.pixel_id
    assert "token" not in str(one)
    plan.enabled=False;session.commit()
    assert service.visit(session,plan.pid)=={"pid":plan.pid,"active":False}


def test_http_200_is_not_delivery_without_provider_acceptance(session,pixel,monkeypatch):
    event=service.enqueue(session,pixel_id=pixel.id,event_id="test:wire",kind="test",test=True,context={"user_agent":"Browser"});session.commit()
    class Response:
        status_code=200
        def json(self): return {"events_received":0}
    class Client:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,*args,**kwargs): return Response()
    import httpx
    monkeypatch.setattr(httpx,"Client",Client)
    ok,result=service.send_provider(session,event,pixel)
    assert ok is False and result["error"]
