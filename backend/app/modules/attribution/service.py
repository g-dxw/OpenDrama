"""Fact-driven conversions: a committed outbox is retried with stable event IDs.

Provider wire formats: Meta Conversions API server-event parameters;
TikTok Events API 2.0 /open_api/v1.3/event/track/ (official Business API docs).
No provider calls happen while recording registration/payment facts.
"""
from __future__ import annotations
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from app.modules.account.models import User
from app.modules.ops.managed_settings import encrypt_secret, decrypt_secret
from app.modules.common.models import AuditLog
from .models import AttributionPixel, AttributionSource, AttributionPlan, AttributionVisit, UserAttribution, AttributionEvent

def now(): return datetime.now(timezone.utc)
def aware(value): return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

def owned(db,model,identifier):
    row=db.get(model,identifier)
    if row is None or row.tenant_id!="jjtv": raise HTTPException(404,"Attribution object not found")
    return row

def pixel_out(row):
    return dict(id=row.id,name=row.name,platform=row.platform,pixel_id=row.pixel_id,enabled=row.enabled,delivery_mode=row.delivery_mode,test_event_code=row.test_event_code,expires_at=row.expires_at,token_configured=bool(row.encrypted_token),token_masked="••••••••" if row.encrypted_token else "",expired=bool(row.expires_at and aware(row.expires_at)<=now()),created_at=row.created_at)

def audit(db,actor_id,action,target,payload):
    db.add(AuditLog(tenant_id="jjtv",actor_type="admin",actor_id=actor_id,action=action,target_type="attribution",target_id=target,payload=payload))

def save_pixel(db,data,actor_id,identifier=None):
    row=owned(db,AttributionPixel,identifier) if identifier else AttributionPixel(tenant_id="jjtv",enabled=False,encrypted_token="",delivery_mode="test",test_event_code="")
    if identifier and any(k in data and data[k]!=getattr(row,k) for k in ("platform","pixel_id")):
        if db.scalar(select(AttributionPlan.id).where(AttributionPlan.pixel_id==row.id).limit(1)): raise HTTPException(409,"Pixel identity is immutable once linked to a plan; create a new pixel")
    for key in ("name","platform","pixel_id","enabled","delivery_mode","test_event_code","expires_at"):
        if key in data: setattr(row,key,data[key])
    if data.get("token"): row.encrypted_token=encrypt_secret(data["token"].strip())
    if data.get("clear_token"): row.encrypted_token="";row.enabled=False
    if row.platform not in {"meta","tiktok"} or not re.fullmatch(r"[A-Za-z0-9_-]{3,100}",row.pixel_id or ""): raise HTTPException(422,"Invalid pixel identity")
    if row.delivery_mode not in {"test","live"}: raise HTTPException(422,"Unknown delivery mode")
    if row.enabled and not row.encrypted_token: raise HTTPException(422,"Configure a token before enabling this pixel")
    if row.enabled and row.delivery_mode=="test" and not row.test_event_code: raise HTTPException(422,"Test mode requires a test event code")
    if row.enabled and row.expires_at and aware(row.expires_at)<=now(): raise HTTPException(422,"Pixel token has expired")
    query=select(AttributionPixel.id).where(AttributionPixel.tenant_id=="jjtv",AttributionPixel.platform==row.platform,AttributionPixel.pixel_id==row.pixel_id)
    if identifier: query=query.where(AttributionPixel.id!=identifier)
    with db.no_autoflush: duplicate=db.scalar(query)
    if duplicate: raise HTTPException(409,"This pixel is already configured")
    if not identifier: db.add(row)
    db.flush();audit(db,actor_id,"attribution.pixel",row.id,{"enabled":row.enabled,"credential_changed":bool(data.get("token") or data.get("clear_token"))});return row

def active_pixel(row): return bool(row.enabled and row.encrypted_token and (not row.expires_at or aware(row.expires_at)>now()))

def active_plan(db,pid):
    if not pid: return None
    plan=db.scalar(select(AttributionPlan).where(AttributionPlan.tenant_id=="jjtv",AttributionPlan.pid==pid,AttributionPlan.enabled.is_(True)))
    if not plan: return None
    source=owned(db,AttributionSource,plan.source_id);pixel=owned(db,AttributionPixel,plan.pixel_id)
    return plan if source.enabled and active_pixel(pixel) else None

def safe_landing(path):
    if not isinstance(path,str) or not path.startswith("/") or path.startswith("//") or len(path)>500 or "\\" in path or any(ord(c)<32 for c in path): raise HTTPException(422,"Landing page must be a local site path")
    parsed=urlsplit(path)
    if parsed.scheme or parsed.netloc: raise HTTPException(422,"Landing page must stay on JJTV")
    return path

def plan_link(plan):
    base=os.environ.get("WEB_PUBLIC_BASE_URL","https://jjtv.8jj.net").rstrip("/")
    parsed=urlsplit(safe_landing(plan.landing_path))
    query=dict(parse_qsl(parsed.query,keep_blank_values=True));query["pid"]=plan.pid
    return base+urlunsplit(("","",parsed.path,urlencode(query),parsed.fragment))

def plan_out(db,row):
    events=list(db.scalars(select(AttributionEvent).where(AttributionEvent.plan_id==row.id,AttributionEvent.test.is_(False))))
    payments=[e for e in events if e.kind=="purchase"]
    revenue={}
    for e in payments: revenue[e.currency]=revenue.get(e.currency,0)+e.amount_cents
    return dict(id=row.id,pid=row.pid,name=row.name,source_id=row.source_id,pixel_id=row.pixel_id,landing_path=row.landing_path,region=row.region,enabled=row.enabled,tracking_url=plan_link(row),visits=db.scalar(select(func.count()).select_from(AttributionVisit).where(AttributionVisit.plan_id==row.id)) or 0,registrations=sum(e.kind=="registration" for e in events),paying_users=len({e.user_id for e in payments}),paid_orders=len(payments),revenue_cents=revenue,created_at=row.created_at)

def clean_context(context):
    source=context or {}
    return {k:str(source[k])[:500] for k in ("user_agent","ttclid","fbclid") if source.get(k)}

def visit(db,pid,context=None,client_id=""):
    plan=active_plan(db,pid)
    if not plan: return dict(pid=pid,active=False)
    stamp=now()
    # A browser revisit with the same client id is one visit within 30 minutes.
    row=None
    if client_id:
        row=db.scalar(select(AttributionVisit).where(AttributionVisit.plan_id==plan.id,AttributionVisit.client_id==client_id,AttributionVisit.created_at>=stamp-timedelta(minutes=30)).order_by(AttributionVisit.created_at.desc()).limit(1))
    if row is None:
        row=AttributionVisit(id=secrets.token_hex(16),tenant_id="jjtv",plan_id=plan.id,client_id=client_id,context=clean_context(context),created_at=stamp);db.add(row);db.flush()
    pixel=owned(db,AttributionPixel,plan.pixel_id)
    return dict(pid=plan.pid,active=True,visit_id=row.id,pixel=dict(platform=pixel.platform,pixel_id=pixel.pixel_id))

def enqueue(db,*,pixel_id,event_id,kind,plan_id=None,user_id=None,order_no=None,amount_cents=0,currency="USD",test=False,context=None):
    values=dict(tenant_id="jjtv",plan_id=plan_id,pixel_id=pixel_id,event_id=event_id,kind=kind,user_id=user_id,order_no=order_no,amount_cents=amount_cents,currency=currency,test=test,context=clean_context(context),status="pending",attempts=0,next_attempt_at=now(),last_error="",response={},created_at=now())
    identifier=db.scalar(insert(AttributionEvent).values(**values).on_conflict_do_nothing(index_elements=[AttributionEvent.pixel_id,AttributionEvent.event_id]).returning(AttributionEvent.id))
    if identifier: return db.get(AttributionEvent,identifier)
    return db.scalar(select(AttributionEvent).where(AttributionEvent.pixel_id==pixel_id,AttributionEvent.event_id==event_id))

def record_registration(db,*,user_id:int,pid:str|None,context=None):
    plan=active_plan(db,pid)
    if plan is None: return []
    # First successful registration owns attribution; subsequent logins cannot reassign it.
    db.execute(insert(UserAttribution).values(user_id=user_id,tenant_id="jjtv",plan_id=plan.id,context=clean_context(context),created_at=now()).on_conflict_do_nothing(index_elements=[UserAttribution.user_id]))
    attribution=db.get(UserAttribution,user_id)
    if attribution is None or attribution.plan_id!=plan.id: return []
    return [enqueue(db,pixel_id=plan.pixel_id,event_id="registration:"+str(user_id),kind="registration",plan_id=plan.id,user_id=user_id,context=context)]

def record_payment(db,*,order_no:str,user_id:int,pid:str|None,amount_cents:int,currency:str,context=None):
    from app.modules.billing.models import Order
    order=db.scalar(select(Order).where(Order.order_no==order_no,Order.user_id==user_id))
    if order is None or order.status!="paid" or order.amount_cents!=amount_cents or order.currency.upper()!=currency.upper(): return []
    context=clean_context(context)
    identity=db.get(UserAttribution,user_id)
    if not pid and identity and identity.tenant_id=="jjtv":
        previous=db.get(AttributionPlan,identity.plan_id);pid=previous.pid if previous else None
    plan=active_plan(db,pid)
    if plan is None: return []
    if not context and identity and identity.tenant_id=="jjtv" and identity.plan_id==plan.id: context=identity.context
    return [enqueue(db,pixel_id=plan.pixel_id,event_id="purchase:"+order_no,kind="purchase",plan_id=plan.id,user_id=user_id,order_no=order_no,amount_cents=amount_cents,currency=currency.upper(),context=context)]

def event_out(row):
    return {key:getattr(row,key) for key in ("id","plan_id","pixel_id","event_id","kind","order_no","amount_cents","currency","test","status","attempts","next_attempt_at","last_error","response","created_at","delivered_at")}

def hashed(value): return hashlib.sha256(value.strip().lower().encode()).hexdigest()

def provider_payload(db,event,pixel):
    user=db.get(User,event.user_id) if event.user_id else None
    plan=db.get(AttributionPlan,event.plan_id) if event.plan_id else None
    url=plan_link(plan) if plan else os.environ.get("WEB_PUBLIC_BASE_URL","https://jjtv.8jj.net")
    kind={"registration":"CompleteRegistration","purchase":"Purchase","test":"PageView"}.get(event.kind,"PageView")
    user_data={}
    if user and user.email: user_data["email"]=hashed(user.email)
    if user: user_data["external_id"]=hashed("jjtv:"+str(user.id))
    context=event.context or {}
    if context.get("user_agent"): user_data["user_agent"]=context["user_agent"]
    if context.get("ttclid"): user_data["ttclid"]=context["ttclid"]
    properties=dict(currency=event.currency,value=event.amount_cents/100) if event.kind=="purchase" else {}
    test_code=pixel.test_event_code if event.test or pixel.delivery_mode=="test" else ""
    if (event.test or pixel.delivery_mode=="test") and not test_code: raise ValueError("Configure a provider test event code")
    if pixel.platform=="tiktok":
        payload=dict(event_source="web",event_source_id=pixel.pixel_id,data=[dict(event=kind,event_time=int(aware(event.created_at).timestamp()),event_id=event.event_id,user=user_data,page=dict(url=url),properties=properties)])
        if test_code: payload["test_event_code"]=test_code
        return payload
    meta_user={}
    if "email" in user_data: meta_user["em"]=[user_data["email"]]
    if "external_id" in user_data: meta_user["external_id"]=[user_data["external_id"]]
    if context.get("user_agent"): meta_user["client_user_agent"]=context["user_agent"]
    if context.get("fbclid"): meta_user["fbc"]="fb.1."+str(int(aware(event.created_at).timestamp()*1000))+"."+context["fbclid"]
    payload=dict(data=[dict(event_name=kind,event_time=int(aware(event.created_at).timestamp()),event_id=event.event_id,action_source="website",event_source_url=url,user_data=meta_user,custom_data=properties)])
    if test_code: payload["test_event_code"]=test_code
    return payload

def send_provider(db,event,pixel):
    import httpx
    token=decrypt_secret(pixel.encrypted_token)
    payload=provider_payload(db,event,pixel)
    if pixel.platform=="meta":
        version=os.environ.get("META_GRAPH_API_VERSION","v24.0")
        if not re.fullmatch(r"v[0-9]+\.[0-9]+",version): raise ValueError("Invalid Meta Graph API version")
        endpoint="https://graph.facebook.com/"+version+"/"+pixel.pixel_id+"/events"
        headers={"Authorization":"Bearer "+token}
    else: endpoint="https://business-api.tiktok.com/open_api/v1.3/event/track/";headers={"Access-Token":token}
    with httpx.Client(timeout=12,follow_redirects=False) as client: response=client.post(endpoint,json=payload,headers=headers)
    try: data=response.json()
    except ValueError: data={}
    if not isinstance(data,dict): data={}
    error=data.get("error")
    safe=dict(status_code=response.status_code,request_id=str(data.get("request_id") or data.get("fbtrace_id") or "")[:200]) if isinstance(data,dict) else dict(status_code=response.status_code)
    if response.status_code>=400 or error or (pixel.platform=="tiktok" and data.get("code")!=0) or (pixel.platform=="meta" and (type(data.get("events_received")) is not int or data["events_received"]<1)):
        message=str(error.get("message",error) if isinstance(error,dict) else data.get("message","Provider rejected event"))[:1500].replace(token,"[redacted]")
        safe.update(error=message,code=str(error.get("code","") if isinstance(error,dict) else data.get("code","")))
        return False,safe
    return True,safe

def process_due(db,limit=20):
    events=list(db.scalars(select(AttributionEvent).where(AttributionEvent.tenant_id=="jjtv",AttributionEvent.status.in_(["pending","retrying"]),AttributionEvent.next_attempt_at<=now()).order_by(AttributionEvent.id).limit(max(1,min(limit,100))).with_for_update(skip_locked=True)))
    results=[]
    for event in events:
        pixel=owned(db,AttributionPixel,event.pixel_id)
        if not active_pixel(pixel) and not (event.test and pixel.encrypted_token and (not pixel.expires_at or aware(pixel.expires_at)>now())):
            event.status="blocked";event.last_error="Pixel disabled, unconfigured or expired";results.append(event.id);continue
        if event.plan_id:
            plan=owned(db,AttributionPlan,event.plan_id)
            if not active_plan(db,plan.pid): event.status="blocked";event.last_error="Plan or source disabled";results.append(event.id);continue
        event.attempts+=1
        try:
            ok,response=send_provider(db,event,pixel)
        except Exception as exc:
            # Never persist request objects, URLs with credentials, or exception text.
            ok,response=False,dict(error="Provider delivery failed",error_type=type(exc).__name__)
        event.response=response
        if ok:
            event.status="delivered";event.delivered_at=now();event.last_error=""
        else:
            event.status="failed" if event.attempts>=8 else "retrying"
            event.last_error=response.get("error","Delivery failed")[:2000]
            event.next_attempt_at=now()+timedelta(seconds=min(3600,60*(2**(event.attempts-1))))
        results.append(event.id)
    db.flush();return results
