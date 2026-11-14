"""Authenticated support endpoints. All administrative access is tenant-scoped."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.modules.account.models import User
from app.modules.common.auth import require_user
from app.modules.ops.models import AdminUser
from app.modules.ops.router import require_admin
from app.modules.tenancy.permissions import require_permission
from . import service
from .models import SupportTicket

admin_router=APIRouter(prefix="/admin/v1/support",tags=["support-admin"])
public_router=APIRouter(prefix="/v1/support",tags=["support"])

class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid")
class TicketIn(Strict):
    subject:str=Field(min_length=1,max_length=200)
    category:str=Field(default="general",max_length=64)
    body:str=Field(min_length=1,max_length=10000)
    order_no:str|None=Field(default=None,max_length=64)
    fields:dict=Field(default_factory=dict)
    @field_validator("subject","body")
    @classmethod
    def nonblank(cls,value):
        if not value.strip(): raise ValueError("Text cannot be blank")
        return value.strip()
class MessageIn(Strict):
    body:str=Field(min_length=1,max_length=10000)
    @field_validator("body")
    @classmethod
    def nonblank(cls,value):
        if not value.strip(): raise ValueError("Message cannot be blank")
        return value.strip()
class StaffMessageIn(MessageIn):
    internal:bool=False
class UpdateIn(Strict):
    version:int|None=Field(default=None,ge=1)
    status:str|None=None
    priority:str|None=None
    assignee_id:int|None=Field(default=None,ge=1)
class BulkIn(Strict):
    numbers:list[str]=Field(min_length=1,max_length=100)
    changes:UpdateIn
class MergeIn(Strict):
    target_number:str=Field(min_length=1,max_length=32)
class SettingsIn(Strict):
    config:dict
    version:int=Field(ge=0)
class CsatIn(Strict):
    score:int=Field(ge=1,le=5)
    comment:str=Field(default="",max_length=1000)

def public_scope(db):
    if db.info.get("content_tenant_id","jjtv")!="jjtv": raise HTTPException(403,"Support is available for JJTV only")
def staff_scope(request,permission):
    if getattr(request.state,"tenant_id",None)!="jjtv": raise HTTPException(403,"Support is available for JJTV only")
    require_permission(request,permission)

@public_router.get("/meta")
def meta(db:Session=Depends(get_db)):
    public_scope(db); config,_=service.settings(db)
    return dict(categories=config["categories"])

@public_router.get("/tickets")
def user_tickets(page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),user:User=Depends(require_user),db:Session=Depends(get_db)):
    public_scope(db); return service.list_tickets(db,user_id=user.id,page=page,page_size=page_size)

@public_router.post("/tickets",status_code=201)
def user_create(body:TicketIn,user:User=Depends(require_user),db:Session=Depends(get_db)):
    public_scope(db); ticket=service.create_ticket(db,user.id,body.model_dump());db.commit()
    return service.detail(db,ticket)

@public_router.get("/tickets/{number}")
def user_detail(number:str,user:User=Depends(require_user),db:Session=Depends(get_db)):
    public_scope(db);return service.detail(db,service.get_ticket(db,number,user.id))

@public_router.post("/tickets/{number}/messages")
def user_message(number:str,body:MessageIn,user:User=Depends(require_user),db:Session=Depends(get_db)):
    public_scope(db);ticket=service.get_ticket(db,number,user.id,True)
    service.add_message(db,ticket,body.body,user.id);db.commit();return service.detail(db,ticket)

@public_router.post("/tickets/{number}/close")
def user_close(number:str,user:User=Depends(require_user),db:Session=Depends(get_db)):
    public_scope(db);ticket=service.get_ticket(db,number,user.id,True)
    service.transition(db,ticket,"closed",user.id);db.commit();return service.detail(db,ticket)

@public_router.post("/tickets/{number}/csat")
def user_csat(number:str,body:CsatIn,user:User=Depends(require_user),db:Session=Depends(get_db)):
    public_scope(db);ticket=service.get_ticket(db,number,user.id,True)
    if ticket.status not in {"resolved","closed"}: raise HTTPException(409,"Rate a resolved or closed ticket")
    ticket.csat_score,ticket.csat_comment=body.score,body.comment
    service.event(db,ticket,"csat",user.id,{"score":body.score});db.commit();return service.detail(db,ticket)

@public_router.post("/tickets/{number}/read")
def user_read(number:str,user:User=Depends(require_user),db:Session=Depends(get_db)):
    public_scope(db);ticket=service.get_ticket(db,number,user.id,True);ticket.user_read_at=service.now();db.commit();return {"ok":True}

@admin_router.get("/agents")
def agents(request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    staff_scope(request,"support.view")
    config,_=service.settings(db)
    return {"items":[dict(id=u.id,name=u.display_name or u.username) for u in db.scalars(select(AdminUser).where(AdminUser.tenant_id=="jjtv",AdminUser.status=="active",AdminUser.role!="partner").order_by(AdminUser.id))],"teams":[dict(code=team["code"],name=team["name"]) for team in config["teams"]]}

@admin_router.get("/tickets")
def admin_tickets(request:Request,q:str=Query("",max_length=200),status:str|None=None,priority:str|None=None,assignee_id:int|None=None,team_code:str|None=Query(None,max_length=64),queue:str|None=None,sort:str="updated",page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    staff_scope(request,"support.view")
    return service.list_tickets(db,admin=True,actor_id=staff.id,q=q,status=status,priority=priority,assignee_id=assignee_id,team_code=team_code,queue=queue,sort=sort,page=page,page_size=page_size)

@admin_router.post("/tickets/bulk")
def bulk(body:BulkIn,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    staff_scope(request,"support.manage");changes=body.changes.model_dump(exclude_unset=True)
    if changes.get("status")=="merged": raise HTTPException(422,"Use the merge action")
    results=[]
    for number in dict.fromkeys(body.numbers):
        try:
            with db.begin_nested():
                ticket=service.get_ticket(db,number,lock=True);service.update_ticket(db,ticket,changes,staff.id)
            results.append(dict(number=number,ok=True))
        except HTTPException as exc: results.append(dict(number=number,ok=False,error=str(exc.detail)))
    db.commit();return dict(items=results)

@admin_router.get("/tickets/{number}")
def admin_detail(number:str,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    staff_scope(request,"support.view");return service.detail(db,service.get_ticket(db,number),True)

@admin_router.patch("/tickets/{number}")
def admin_update(number:str,body:UpdateIn,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    staff_scope(request,"support.manage");ticket=service.get_ticket(db,number,lock=True)
    data=body.model_dump(exclude_unset=True)
    if any(data.get(k) is None for k in ("status","priority") if k in data): raise HTTPException(422,"Status and priority cannot be null")
    service.update_ticket(db,ticket,data,staff.id);db.commit();return service.detail(db,ticket,True)

@admin_router.post("/tickets/{number}/claim")
def claim(number:str,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    staff_scope(request,"support.manage");ticket=service.get_ticket(db,number,lock=True)
    if ticket.assignee_id not in (None,staff.id): raise HTTPException(409,"Ticket is already assigned")
    service.update_ticket(db,ticket,{"assignee_id":staff.id,"status":"in_progress"},staff.id);db.commit();return service.detail(db,ticket,True)

@admin_router.post("/tickets/{number}/messages")
def admin_message(number:str,body:StaffMessageIn,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    staff_scope(request,"support.manage");ticket=service.get_ticket(db,number,lock=True)
    service.add_message(db,ticket,body.body,staff.id,True,body.internal);db.commit();return service.detail(db,ticket,True)

@admin_router.post("/tickets/{number}/merge")
def merge_ticket(number:str,body:MergeIn,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    staff_scope(request,"support.manage")
    # Consistent row locking prevents opposite-direction merges from deadlocking.
    tickets={t.number:t for t in db.scalars(select(SupportTicket).where(SupportTicket.tenant_id=="jjtv",SupportTicket.number.in_([number,body.target_number])).order_by(SupportTicket.id).with_for_update())}
    if number not in tickets or body.target_number not in tickets: raise HTTPException(404,"Ticket not found")
    service.merge(db,tickets[number],tickets[body.target_number],staff.id);db.commit();return service.detail(db,tickets[body.target_number],True)

@admin_router.get("/reports")
def reports(request:Request,days:int=Query(7,ge=1,le=90),staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    staff_scope(request,"support.reports");return service.reports(db,days)

@admin_router.get("/settings")
def settings(request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    staff_scope(request,"support.settings");config,version=service.settings(db);return dict(config=config,version=version)

@admin_router.post("/settings")
def save_settings(body:SettingsIn,request:Request,staff:AdminUser=Depends(require_admin),db:Session=Depends(get_db)):
    staff_scope(request,"support.settings");value=service.save_settings(db,body.config,body.version,staff.id);db.commit();return value
