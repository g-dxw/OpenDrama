"""Attribution settings, test-only diagnostics and read-only delivery logs."""
from datetime import datetime
import secrets
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.db import get_db
from app.modules.ops.models import AdminUser
from app.modules.ops.router import require_admin
from app.modules.tenancy.permissions import require_permission
from .models import AttributionPixel, AttributionSource, AttributionPlan, AttributionEvent
from . import service

admin_router=APIRouter(prefix="/admin/v1/attr",tags=["attribution-admin"])
public_router=APIRouter(prefix="/v1/attr",tags=["attribution"])
class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid")
class PixelIn(Strict):
    name:str=Field(min_length=1,max_length=120)
    platform:str
    pixel_id:str=Field(min_length=3,max_length=100)
    token:str=Field(default="",max_length=5000)
    enabled:bool=False
    delivery_mode:str="test"
    test_event_code:str=Field(default="",max_length=100)
    expires_at:datetime|None=None
class PixelPatch(Strict):
    name:str|None=Field(default=None,min_length=1,max_length=120)
    token:str|None=Field(default=None,max_length=5000)
    enabled:bool|None=None
    delivery_mode:str|None=None
    test_event_code:str|None=Field(default=None,max_length=100)
    expires_at:datetime|None=None
    clear_token:bool=False
class SourceIn(Strict):
    name:str=Field(min_length=1,max_length=120)
    pixel_id:int=Field(ge=1)
    account_id:str=Field(default="",max_length=120)
    timezone:str=Field(default="Asia/Shanghai",max_length=64)
    currency:str=Field(default="USD",pattern="^[A-Z]{3}$")
    enabled:bool=True
    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls,value):
        try: ZoneInfo(value)
        except ZoneInfoNotFoundError: raise ValueError("Unknown timezone") from None
        return value
class PlanIn(Strict):
    name:str=Field(min_length=1,max_length=120)
    source_id:int=Field(ge=1)
    landing_path:str=Field(default="/",max_length=500)
    region:str=Field(default="",max_length=64)
    enabled:bool=True
class PlanPatch(Strict):
    name:str|None=Field(default=None,min_length=1,max_length=120)
    landing_path:str|None=Field(default=None,max_length=500)
    region:str|None=Field(default=None,max_length=64)
    enabled:bool|None=None
class VisitIn(Strict):
    pid:str=Field(min_length=1,max_length=48)
    client_id:str=Field(default="",max_length=100)
    ttclid:str=Field(default="",max_length=500)
    fbclid:str=Field(default="",max_length=500)

def scope(request,permission):
    if getattr(request.state,"tenant_id",None)!="jjtv": raise HTTPException(403,"Attribution is available for JJTV only")
    require_permission(request,permission)

@public_router.post("/visit")
def visit(body:VisitIn,request:Request,db:Session=Depends(get_db)):
    tenant_id = db.info.get("content_tenant_id")
    if tenant_id != "jjtv": raise HTTPException(403,"Attribution is available for JJTV only")
    from app.modules.tenancy.models import Tenant
    from app.modules.sites.service import features_for
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or not features_for(db, tenant).get("attribution", False):
        raise HTTPException(403, detail={"code": "feature_disabled", "feature": "attribution", "message": "Attribution collection is disabled on this site"})
    context=dict(user_agent=request.headers.get("user-agent","")[:500],ttclid=body.ttclid,fbclid=body.fbclid)
    # Current PC/H5 requests always carry the shared client device header.
    # Keep body-only older clients compatible, but never let it override that identity.
    client_id=request.headers.get("X-Device-Id")
    if client_id is None: client_id=body.client_id
    result=service.visit(db,body.pid,context,client_id);db.commit();return result

@admin_router.get("/pixels")
def pixels(request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.view");return dict(items=[service.pixel_out(p) for p in db.scalars(select(AttributionPixel).where(AttributionPixel.tenant_id=="jjtv").order_by(AttributionPixel.id.desc()))])

@admin_router.post("/pixels",status_code=201)
def create_pixel(body:PixelIn,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.manage");row=service.save_pixel(db,body.model_dump(),staff.id);db.commit();return service.pixel_out(row)

@admin_router.patch("/pixels/{identifier}")
def update_pixel(identifier:int,body:PixelPatch,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.manage");data=body.model_dump(exclude_unset=True)
    if any(value is None for key,value in data.items() if key!="expires_at"): raise HTTPException(422,"Setting cannot be null")
    row=service.save_pixel(db,data,staff.id,identifier);db.commit();return service.pixel_out(row)

@admin_router.post("/pixels/{identifier}/test")
def test_pixel(identifier:int,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.manage");pixel=service.owned(db,AttributionPixel,identifier)
    if not pixel.encrypted_token or not pixel.test_event_code: raise HTTPException(422,"Configure token and provider test event code first")
    row=service.enqueue(db,pixel_id=pixel.id,event_id="test:"+secrets.token_hex(12),kind="test",test=True,context={"user_agent":request.headers.get("user-agent","")[:500]})
    service.audit(db,staff.id,"attribution.test",pixel.id,{});db.commit();return service.event_out(row)

@admin_router.get("/sources")
def sources(request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.view")
    return dict(items=[dict(id=s.id,name=s.name,pixel_id=s.pixel_id,account_id=s.account_id,timezone=s.timezone,currency=s.currency,enabled=s.enabled) for s in db.scalars(select(AttributionSource).where(AttributionSource.tenant_id=="jjtv").order_by(AttributionSource.id.desc()))])

def save_source(db,body,actor_id,identifier=None):
    service.owned(db,AttributionPixel,body.pixel_id)
    row=service.owned(db,AttributionSource,identifier) if identifier else AttributionSource(tenant_id="jjtv")
    if identifier and row.pixel_id!=body.pixel_id and db.scalar(select(AttributionPlan.id).where(AttributionPlan.source_id==identifier).limit(1)): raise HTTPException(409,"Create a new source to change the pixel after a plan exists")
    for key,value in body.model_dump().items(): setattr(row,key,value)
    if identifier is None: db.add(row)
    db.flush();service.audit(db,actor_id,"attribution.source",row.id,{"enabled":row.enabled});return row

@admin_router.post("/sources",status_code=201)
def create_source(body:SourceIn,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.manage");row=save_source(db,body,staff.id);db.commit();return {"id":row.id}

@admin_router.patch("/sources/{identifier}")
def update_source(identifier:int,body:SourceIn,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.manage");row=save_source(db,body,staff.id,identifier);db.commit();return {"id":row.id}

@admin_router.get("/plans")
def plans(request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.view");return dict(items=[service.plan_out(db,p) for p in db.scalars(select(AttributionPlan).where(AttributionPlan.tenant_id=="jjtv").order_by(AttributionPlan.id.desc()))])

@admin_router.post("/plans",status_code=201)
def create_plan(body:PlanIn,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.manage");source=service.owned(db,AttributionSource,body.source_id)
    row=AttributionPlan(tenant_id="jjtv",pid=secrets.token_urlsafe(18),pixel_id=source.pixel_id,**body.model_dump());row.landing_path=service.safe_landing(row.landing_path)
    db.add(row);db.flush();service.audit(db,staff.id,"attribution.plan",row.id,{});db.commit();return service.plan_out(db,row)

@admin_router.patch("/plans/{identifier}")
def update_plan(identifier:int,body:PlanPatch,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.manage");row=service.owned(db,AttributionPlan,identifier)
    for key,value in body.model_dump(exclude_unset=True).items():
        if value is None: raise HTTPException(422,"Plan value cannot be null")
        setattr(row,key,service.safe_landing(value) if key=="landing_path" else value)
    service.audit(db,staff.id,"attribution.plan",row.id,{"enabled":row.enabled});db.commit();return service.plan_out(db,row)

@admin_router.post("/plans/{identifier}/test")
def test_plan(identifier:int,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.manage");plan=service.owned(db,AttributionPlan,identifier);pixel=service.owned(db,AttributionPixel,plan.pixel_id)
    if not pixel.encrypted_token or not pixel.test_event_code: raise HTTPException(422,"Configure token and provider test event code first")
    row=service.enqueue(db,pixel_id=pixel.id,plan_id=plan.id,event_id="test:"+secrets.token_hex(12),kind="test",test=True,context={"user_agent":request.headers.get("user-agent","")[:500]})
    service.audit(db,staff.id,"attribution.test_plan",plan.id,{});db.commit();return service.event_out(row)

@admin_router.get("/events")
def events(request:Request,status:str|None=None,plan_id:int|None=None,page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=100),staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.view");query=select(AttributionEvent).where(AttributionEvent.tenant_id=="jjtv")
    if status: query=query.where(AttributionEvent.status==status)
    if plan_id: query=query.where(AttributionEvent.plan_id==plan_id)
    total=db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows=db.scalars(query.order_by(AttributionEvent.id.desc()).offset((page-1)*page_size).limit(page_size))
    return dict(items=[service.event_out(e) for e in rows],total=total,page=page,page_size=page_size)

@admin_router.post("/events/{identifier}/retry")
def retry(identifier:int,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    scope(request,"attribution.manage");row=db.scalar(select(AttributionEvent).where(AttributionEvent.id==identifier,AttributionEvent.tenant_id=="jjtv").with_for_update())
    if not row: raise HTTPException(404,"Event not found")
    if row.status=="delivered": raise HTTPException(409,"Event already delivered")
    row.status="pending";row.next_attempt_at=service.now();row.last_error="";row.attempts=0
    service.audit(db,staff.id,"attribution.retry",row.id,{"event_id":row.event_id});db.commit();return service.event_out(row)
