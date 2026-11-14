"""Support state machine, business-hour SLA and rule-based assignment."""
from __future__ import annotations
import copy
import secrets
import re
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import HTTPException
from sqlalchemy import select, func, or_, case
from sqlalchemy.orm import Session
from app.modules.account.models import User
from app.modules.ops.models import AdminUser
from app.modules.common.models import AuditLog
from app.modules.wallet.models import WalletAccount
from .models import SupportConfig, SupportTicket, SupportMessage, SupportEvent

TERMINAL = {"resolved", "closed", "merged"}
STATUSES = {"open", "in_progress", "waiting_customer", "resolved", "closed"}
PRIORITIES = {"low", "normal", "high", "urgent"}
DEFAULT_CONFIG = {
 "calendar": {"timezone":"America/Los_Angeles", "weekdays":[0,1,2,3,4], "start":"09:00", "end":"18:00", "holidays":[]},
 "sla": {"low":{"first_response_minutes":240,"resolution_minutes":1440},"normal":{"first_response_minutes":60,"resolution_minutes":480},"high":{"first_response_minutes":30,"resolution_minutes":240},"urgent":{"first_response_minutes":15,"resolution_minutes":120}},
 "teams":[{"code":"default","name":"客服组","members":[],"max_concurrent":20}], "default_team":"default",
 "categories":[{"code":"general","name_en":"General","name_zh":"一般问题","fields":[]},{"code":"billing","name_en":"Billing","name_zh":"支付与会员","fields":[]},{"code":"playback","name_en":"Playback","name_zh":"播放问题","fields":[]},{"code":"account","name_en":"Account","name_zh":"账号问题","fields":[]}],
 "rules":[], "assignment":"least_busy"}

def now(): return datetime.now(timezone.utc)
def aware(value): return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

def calendar_hours(calendar):
    """24:00 is an exclusive end-of-day boundary, never a start time."""
    opening = time.fromisoformat(calendar.get("start", "09:00"))
    end = calendar.get("end", "18:00")
    end_of_day = end == "24:00"
    closing = time.min if end_of_day else time.fromisoformat(end)
    if opening.tzinfo is not None or closing.tzinfo is not None:
        raise ValueError("Business hours must be local times")
    if opening.second or opening.microsecond or closing.second or closing.microsecond:
        raise ValueError("Business hours use minute precision")
    if not end_of_day and opening >= closing:
        raise ValueError("Business hours must end after opening")
    return opening, closing, end_of_day


def business_minutes(start: datetime, end: datetime, calendar: dict) -> float:
    """Integrate in UTC over local business days, including DST transitions."""
    start, end = aware(start), aware(end)
    if end <= start: return 0.0
    zone = ZoneInfo(calendar.get("timezone", DEFAULT_CONFIG["calendar"]["timezone"]))
    first, last = start.astimezone(zone).date(), end.astimezone(zone).date()
    opening, closing, end_of_day = calendar_hours(calendar)
    weekdays, holidays = set(calendar.get("weekdays", range(5))), set(calendar.get("holidays", []))
    total, day = 0.0, first
    while day <= last:
        if day.weekday() in weekdays and day.isoformat() not in holidays:
            left = max(start, datetime.combine(day, opening, zone).astimezone(timezone.utc))
            right = min(end, datetime.combine(day + timedelta(days=1) if end_of_day else day, closing, zone).astimezone(timezone.utc))
            total += max(0.0, (right-left).total_seconds()/60)
        day += timedelta(days=1)
    return total

def settings(db: Session):
    row = db.get(SupportConfig, "jjtv")
    return copy.deepcopy(row.config if row else DEFAULT_CONFIG), row.version if row else 0

def validate_settings(db, config):
    if not isinstance(config,dict): raise HTTPException(422, "Invalid support settings")
    result = copy.deepcopy(DEFAULT_CONFIG)
    result.update(config)
    calendar = result.get("calendar", {})
    if not isinstance(calendar,dict): raise HTTPException(422,"Invalid calendar")
    try:
        ZoneInfo(calendar["timezone"])
        calendar_hours(calendar)
        if not isinstance(calendar["weekdays"],list) or not calendar["weekdays"] or len(calendar["weekdays"])>7 or any(type(x) is not int or x<0 or x>6 for x in calendar["weekdays"]): raise ValueError()
        if not isinstance(calendar.get("holidays",[]),list) or len(calendar.get("holidays", [])) > 500: raise ValueError()
        for value in calendar.get("holidays", []): date.fromisoformat(value)
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        raise HTTPException(422,"Calendar requires a valid timezone, weekdays and same-day business hours") from None
    if result.get("assignment") not in {"least_busy","round_robin","manual"}: raise HTTPException(422,"Unknown assignment method")
    teams=result.get("teams",[])
    if not isinstance(teams,list) or not 1 <= len(teams) <= 30: raise HTTPException(422,"Configure 1 to 30 teams")
    codes=set()
    for team in teams:
        if not isinstance(team,dict): raise HTTPException(422,"Invalid team")
        code=team.get("code","")
        if not isinstance(code,str) or not code or len(code)>64 or code in codes: raise HTTPException(422,"Team codes must be unique")
        codes.add(code)
        if not isinstance(team.get("name"),str) or not team["name"].strip(): raise HTTPException(422,"Team name required")
        if type(team.get("max_concurrent",20)) is not int or not 1<=team.get("max_concurrent",20)<=500: raise HTTPException(422,"Invalid team concurrency")
        members=team.get("members",[])
        if not isinstance(members,list) or any(type(x) is not int or x<1 for x in members) or len(members)!=len(set(members)): raise HTTPException(422,"Duplicate team member")
        for identifier in members:
            staff=db.get(AdminUser,identifier)
            if staff is None or staff.tenant_id!="jjtv" or staff.status!="active" or staff.role=="partner": raise HTTPException(422,"Team members must be active JJTV staff")
    if result.get("default_team") not in codes: raise HTTPException(422,"Default team not found")
    categories=result.get("categories",[])
    category_codes=set()
    if not isinstance(categories,list) or not 1<=len(categories)<=40: raise HTTPException(422,"Configure 1 to 40 categories")
    for category in categories:
        if not isinstance(category,dict): raise HTTPException(422,"Invalid category")
        code=category.get("code","")
        if not isinstance(code,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",code) or code in category_codes: raise HTTPException(422,"Category codes must be unique")
        category_codes.add(code)
        if any(not isinstance(category.get(k),str) or not category[k].strip() or len(category[k])>100 for k in ("name_en","name_zh")): raise HTTPException(422,"Category names required in both languages")
        fields=category.get("fields",[])
        field_keys=set()
        if not isinstance(fields,list) or len(fields)>20: raise HTTPException(422,"At most 20 fields per category")
        for field in fields:
            if not isinstance(field,dict): raise HTTPException(422,"Invalid category field")
            key=field.get("key","")
            if not isinstance(key,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",key) or key in field_keys or field.get("type") not in {"text","select"}: raise HTTPException(422,"Invalid category field")
            field_keys.add(key)
            if type(field.get("required",False)) is not bool: raise HTTPException(422,"Invalid required field flag")
            if field["type"]=="select" and (not isinstance(field.get("options"),list) or not field["options"] or len(field["options"])>50 or any(not isinstance(x,str) or len(x)>200 for x in field["options"])): raise HTTPException(422,"Select field requires options")
    if not isinstance(result.get("sla"),dict): raise HTTPException(422,"Invalid SLA settings")
    for priority in PRIORITIES:
        target=result.get("sla",{}).get(priority,{})
        if not isinstance(target,dict): raise HTTPException(422,"Invalid SLA target")
        if any(type(target.get(key)) is not int or not 1<=target[key]<=43200 for key in ("first_response_minutes","resolution_minutes")): raise HTTPException(422,"Invalid SLA targets")
    rules=result.get("rules",[])
    if not isinstance(rules,list) or len(rules)>100: raise HTTPException(422,"At most 100 assignment rules")
    for rule in rules:
        if not isinstance(rule,dict) or not isinstance(rule.get("match",{}),dict) or type(rule.get("enabled",True)) is not bool: raise HTTPException(422,"Invalid assignment rule")
        if not isinstance(rule.get("team"),str) or rule.get("team") not in codes or rule.get("assignment","least_busy") not in {"least_busy","round_robin","manual"}: raise HTTPException(422,"Invalid rule destination")
        match=rule.get("match",{})
        if match.get("category") and (not isinstance(match["category"],str) or match["category"] not in category_codes): raise HTTPException(422,"Unknown rule category")
        if match.get("priority") and (not isinstance(match["priority"],str) or match["priority"] not in PRIORITIES): raise HTTPException(422,"Unknown rule priority")
        if match.get("subject_contains") and (not isinstance(match["subject_contains"],str) or len(match["subject_contains"])>200): raise HTTPException(422,"Invalid title condition")
        if set(rule.get("match",{}))-{"category","priority","subject_contains"}: raise HTTPException(422,"Unsupported rule condition")
    return result

def save_settings(db, config, version, actor_id):
    config=validate_settings(db,config)
    row=db.scalar(select(SupportConfig).where(SupportConfig.tenant_id=="jjtv").with_for_update())
    if (row.version if row else 0)!=version: raise HTTPException(409,"Settings changed; reload before saving")
    if row is None:
        row=SupportConfig(tenant_id="jjtv",config=config,version=1,assignment_cursor=0); db.add(row)
    else: row.config, row.version = config, row.version+1
    audit(db,actor_id,"support.settings",None,{"version":row.version})
    db.flush(); return dict(config=config,version=row.version)

def audit(db, actor_id, action, target, payload):
    db.add(AuditLog(tenant_id="jjtv",actor_type="admin",actor_id=actor_id,action=action,target_type="support_ticket",target_id=target,payload=payload))

def event(db, ticket, kind, actor_id=None, payload=None):
    db.add(SupportEvent(ticket_id=ticket.id,kind=kind,actor_id=actor_id,payload=payload or {}))

def get_ticket(db, number, user_id=None, lock=False):
    query=select(SupportTicket).where(SupportTicket.tenant_id=="jjtv",SupportTicket.number==number)
    if user_id is not None: query=query.where(SupportTicket.user_id==user_id)
    if lock: query=query.with_for_update()
    row=db.scalar(query)
    if row is None: raise HTTPException(404,"Ticket not found")
    return row

def choose_assignee(db, ticket, config):
    # Serialize automatic assignments so simultaneous arrivals cannot exceed capacity.
    setting=db.scalar(select(SupportConfig).where(SupportConfig.tenant_id=="jjtv").with_for_update())
    team_code=config["default_team"]; method=config.get("assignment","least_busy")
    for rule in config.get("rules",[]):
        if not rule.get("enabled",True): continue
        match=rule.get("match",{})
        if match.get("category") and match["category"]!=ticket.category: continue
        if match.get("priority") and match["priority"]!=ticket.priority: continue
        if match.get("subject_contains") and match["subject_contains"].lower() not in ticket.subject.lower(): continue
        team_code,method=rule["team"],rule.get("assignment","least_busy"); break
    team=next(t for t in config["teams"] if t["code"]==team_code)
    ticket.team_code=team_code
    if method=="manual": return
    candidates=[]
    for identifier in team.get("members",[]):
        staff=db.get(AdminUser,identifier)
        if not staff or staff.status!="active" or staff.tenant_id!="jjtv" or staff.role=="partner": continue
        count=db.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.tenant_id=="jjtv",SupportTicket.assignee_id==identifier,SupportTicket.status.notin_(TERMINAL))) or 0
        if count<team.get("max_concurrent",20): candidates.append((count,identifier))
    if not candidates: return
    if method=="round_robin":
        cursor=setting.assignment_cursor if setting else 0
        identifiers=sorted(x[1] for x in candidates)
        ticket.assignee_id=identifiers[cursor % len(identifiers)]
        if setting: setting.assignment_cursor+=1
    else: ticket.assignee_id=min(candidates)[1]
    ticket.status="in_progress"

def create_ticket(db, user_id, data):
    config,_=settings(db)
    category=next((c for c in config["categories"] if c["code"]==data.get("category","general")),None)
    if not category: raise HTTPException(422,"Unknown ticket category")
    fields=data.get("fields",{})
    if set(fields)-{f["key"] for f in category.get("fields",[])}: raise HTTPException(422,"Unknown category field")
    for field in category.get("fields",[]):
        value=fields.get(field["key"])
        if field.get("required") and (not isinstance(value,str) or not value.strip()): raise HTTPException(422,"Required category field: "+field["key"])
        if value is not None and (not isinstance(value,str) or len(value)>2000): raise HTTPException(422,"Invalid category field")
        if value and field["type"]=="select" and value not in field["options"]: raise HTTPException(422,"Invalid category option")
    if data.get("order_no"):
        from app.modules.billing.models import Order
        if db.scalar(select(Order.id).where(Order.order_no==data["order_no"],Order.user_id==user_id)) is None: raise HTTPException(422,"Order does not belong to this account")
    stamp=now()
    ticket=SupportTicket(tenant_id="jjtv",user_id=user_id,number="TK-"+secrets.token_hex(6).upper(),subject=data["subject"].strip(),category=category["code"],priority="normal",status="open",order_no=data.get("order_no"),fields=fields,version=1,created_at=stamp,updated_at=stamp,last_user_at=stamp,paused_minutes=0,sla_policy={**config["sla"]["normal"],"calendar":copy.deepcopy(config["calendar"])})
    choose_assignee(db,ticket,config)
    db.add(ticket); db.flush()
    db.add(SupportMessage(tenant_id="jjtv",ticket_id=ticket.id,author_type="user",author_id=user_id,body=data["body"].strip(),internal=False,created_at=stamp))
    event(db,ticket,"created",user_id,{"team":ticket.team_code,"assignee_id":ticket.assignee_id}); db.flush()
    return ticket

def sla(ticket, at=None):
    at=at or now(); policy=ticket.sla_policy or {}; calendar=policy.get("calendar",DEFAULT_CONFIG["calendar"])
    first=business_minutes(ticket.created_at,ticket.first_response_at or at,calendar)
    elapsed=business_minutes(ticket.created_at,ticket.resolved_at or at,calendar)-(ticket.paused_minutes or 0)
    if ticket.waiting_since: elapsed-=business_minutes(ticket.waiting_since,at,calendar)
    first_target=policy.get("first_response_minutes",60); resolve_target=policy.get("resolution_minutes",480)
    return dict(first_response_minutes=round(first,2),resolution_minutes=round(max(0,elapsed),2),first_response_target=first_target,resolution_target=resolve_target,first_response_met=first<=first_target if ticket.first_response_at else None,resolution_met=elapsed<=resolve_target if ticket.resolved_at else None,overdue=ticket.status not in TERMINAL and ((not ticket.first_response_at and first>first_target) or elapsed>resolve_target))

def ticket_out(db, row, admin=False):
    output={key:getattr(row,key) for key in ("id","number","subject","category","priority","status","order_no","fields","created_at","updated_at","csat_score","version")}
    output.update(can_reply=row.status not in {"closed","merged"},merged_into=db.get(SupportTicket,row.merged_into_id).number if row.merged_into_id else None)
    unread=db.scalar(select(func.count()).select_from(SupportMessage).where(SupportMessage.ticket_id==row.id,SupportMessage.internal.is_(False),SupportMessage.author_type=="admin",SupportMessage.created_at>(row.user_read_at or row.created_at))) or 0
    output["unread_count"]=unread
    if admin:
        user=db.get(User,row.user_id); staff=db.get(AdminUser,row.assignee_id) if row.assignee_id else None
        output.update(user_id=row.user_id,email=user.email if user else None,display_name=user.display_name if user else "",assignee_id=row.assignee_id,assignee_name=(staff.display_name or staff.username) if staff else None,team_code=row.team_code,sla=sla(row),first_response_at=row.first_response_at,resolved_at=row.resolved_at,csat_comment=row.csat_comment)
    return output

def detail(db, ticket, admin=False):
    query=select(SupportMessage).where(SupportMessage.ticket_id==ticket.id,SupportMessage.tenant_id=="jjtv").order_by(SupportMessage.created_at,SupportMessage.id)
    if not admin: query=query.where(SupportMessage.internal.is_(False))
    messages=[]
    for row in db.scalars(query):
        value=dict(id=row.id,body=row.body,author_type=row.author_type,created_at=row.created_at)
        if admin: value.update(internal=row.internal,author_id=row.author_id)
        messages.append(value)
    output=dict(ticket=ticket_out(db,ticket,admin),messages=messages)
    if admin:
        from app.modules.billing import service as billing
        from app.modules.billing.models import Order
        wallet=db.get(WalletAccount,ticket.user_id)
        output["customer"]=dict(membership=billing.active_membership(db,ticket.user_id),coins=wallet.balance if wallet else 0,orders=[dict(order_no=o.order_no,status=o.status,amount_cents=o.amount_cents,currency=o.currency) for o in db.scalars(select(Order).where(Order.user_id==ticket.user_id).order_by(Order.id.desc()).limit(5))])
        output["events"]=[dict(id=e.id,kind=e.kind,payload=e.payload,created_at=e.created_at) for e in db.scalars(select(SupportEvent).where(SupportEvent.ticket_id==ticket.id).order_by(SupportEvent.id))]
    return output

def transition(db,ticket,status,actor_id):
    if status not in STATUSES: raise HTTPException(422,"Unknown status")
    if ticket.status=="merged": raise HTTPException(409,"Merged ticket cannot be changed")
    if ticket.status==status: return
    stamp=now(); old=ticket.status
    if ticket.waiting_since:
        ticket.paused_minutes+=business_minutes(ticket.waiting_since,stamp,ticket.sla_policy.get("calendar",DEFAULT_CONFIG["calendar"]))
        ticket.waiting_since=None
    if status=="waiting_customer": ticket.waiting_since=stamp
    if status in {"resolved","closed"}: ticket.resolved_at=ticket.resolved_at or stamp
    else:
        if old in {"resolved","closed"}: event(db,ticket,"reopened",actor_id,{"within_7_days":bool(ticket.resolved_at and stamp-aware(ticket.resolved_at)<=timedelta(days=7))})
        ticket.resolved_at=None
    ticket.status=status; ticket.updated_at=stamp; ticket.version+=1
    event(db,ticket,"status",actor_id,{"from":old,"to":status})

def add_message(db,ticket,body,actor_id,admin=False,internal=False):
    if ticket.status=="merged": raise HTTPException(409,"Reply to the main ticket")
    if ticket.status=="closed" and not admin: raise HTTPException(409,"Closed ticket cannot receive replies")
    stamp=now()
    row=SupportMessage(tenant_id="jjtv",ticket_id=ticket.id,author_type="admin" if admin else "user",author_id=actor_id,body=body.strip(),internal=internal if admin else False,created_at=stamp)
    db.add(row)
    if admin and not internal:
        ticket.first_response_at=ticket.first_response_at or stamp; ticket.last_admin_at=stamp
        transition(db,ticket,"waiting_customer",actor_id)
    elif not admin:
        ticket.last_user_at=stamp
        if ticket.status in {"waiting_customer","resolved"}: transition(db,ticket,"in_progress" if ticket.assignee_id else "open",actor_id)
    ticket.updated_at=stamp; ticket.version+=1
    event(db,ticket,"internal_note" if internal else "reply",actor_id)
    if admin: audit(db,actor_id,"support.note" if internal else "support.reply",ticket.id,{})
    db.flush(); return row

def update_ticket(db,ticket,data,actor_id):
    if ticket.status=="merged": raise HTTPException(409,"Merged ticket cannot be changed")
    if data.get("version") is not None and data["version"]!=ticket.version: raise HTTPException(409,"Ticket changed; reload")
    if "assignee_id" in data:
        identifier=data["assignee_id"]
        if identifier is not None:
            staff=db.get(AdminUser,identifier)
            if not staff or staff.tenant_id!="jjtv" or staff.status!="active" or staff.role=="partner": raise HTTPException(422,"Assignee must be active JJTV staff")
        ticket.assignee_id=identifier
        event(db,ticket,"assigned",actor_id,{"assignee_id":identifier})
    if "priority" in data:
        priority=data["priority"]
        if priority not in PRIORITIES: raise HTTPException(422,"Unknown priority")
        ticket.priority=priority
        config,_=settings(db); ticket.sla_policy={**ticket.sla_policy,**config["sla"][priority]}
    if "status" in data: transition(db,ticket,data["status"],actor_id)
    ticket.updated_at=now();ticket.version+=1
    audit(db,actor_id,"support.update",ticket.id,{k:v for k,v in data.items() if k!="version"});db.flush()
    return ticket

def merge(db,source,target,actor_id):
    if source.tenant_id!="jjtv" or target.tenant_id!="jjtv": raise HTTPException(404,"Ticket not found")
    if source.id==target.id or source.status=="merged" or target.status=="merged": raise HTTPException(409,"Cannot merge self, merged tickets or create a merge chain")
    if source.user_id!=target.user_id: raise HTTPException(409,"Only tickets from the same customer may be merged")
    from sqlalchemy import update
    db.execute(update(SupportMessage).where(SupportMessage.ticket_id==source.id).values(ticket_id=target.id))
    source.status="merged";source.merged_into_id=target.id;source.updated_at=now();source.version+=1
    target.updated_at=now();target.version+=1
    event(db,source,"merged",actor_id,{"into":target.number});event(db,target,"merged_from",actor_id,{"number":source.number})
    audit(db,actor_id,"support.merge",source.id,{"into":target.number});db.flush()

def queue_counts(db, actor_id):
    """Unfiltered JJTV queue totals; search and pagination never alter badges."""
    active = SupportTicket.status.notin_(TERMINAL)
    totals = db.execute(select(
        func.count().filter(active).label("active"),
        func.count().filter(active, SupportTicket.assignee_id == actor_id if actor_id is not None else False).label("mine"),
        func.count().filter(active, SupportTicket.assignee_id.is_(None)).label("unassigned"),
        func.count().filter(SupportTicket.status == "waiting_customer").label("waiting_customer"),
    ).select_from(SupportTicket).where(SupportTicket.tenant_id == "jjtv")).mappings().one()
    # Each ticket freezes its own calendar. A SQL age threshold would count
    # weekends, holidays and paused customer time as active SLA time.
    stamp = now()
    overdue = sum(sla(ticket, stamp)["overdue"] for ticket in db.scalars(
        select(SupportTicket).where(SupportTicket.tenant_id == "jjtv", active)
    ))
    return {**dict(totals), "overdue": overdue}


def list_tickets(db,*,admin=False,user_id=None,actor_id=None,q="",status=None,priority=None,assignee_id=None,team_code=None,queue=None,page=1,page_size=25,sort="updated"):
    if queue not in {None,"all","active","mine","unassigned","waiting_customer","overdue"}:
        raise HTTPException(422,"Unknown support queue")
    if sort not in {"updated","oldest","priority"}:
        raise HTTPException(422,"Unknown support sort")
    query=select(SupportTicket).where(SupportTicket.tenant_id=="jjtv")
    if not admin: query=query.where(SupportTicket.user_id==user_id)
    if q:
        query=query.join(User,User.id==SupportTicket.user_id).where(or_(SupportTicket.number.ilike("%"+q+"%"),SupportTicket.subject.ilike("%"+q+"%"),SupportTicket.order_no.ilike("%"+q+"%"),User.email.ilike("%"+q+"%")))
    if status: query=query.where(SupportTicket.status==status)
    if priority: query=query.where(SupportTicket.priority==priority)
    if assignee_id is not None: query=query.where(SupportTicket.assignee_id==assignee_id)
    if team_code: query=query.where(SupportTicket.team_code==team_code)
    if queue=="mine": query=query.where(SupportTicket.assignee_id==actor_id,SupportTicket.status.notin_(TERMINAL))
    elif queue=="unassigned": query=query.where(SupportTicket.assignee_id.is_(None),SupportTicket.status.notin_(TERMINAL))
    elif queue=="waiting_customer": query=query.where(SupportTicket.status=="waiting_customer")
    elif queue in {"active","overdue"}: query=query.where(SupportTicket.status.notin_(TERMINAL))
    count=db.scalar(select(func.count()).select_from(query.subquery())) or 0
    if sort=="priority":
        query=query.order_by(case({"urgent":0,"high":1,"normal":2,"low":3},value=SupportTicket.priority,else_=4),SupportTicket.created_at.asc(),SupportTicket.id.asc())
    else:
        query=query.order_by(SupportTicket.created_at.asc() if sort=="oldest" else SupportTicket.updated_at.desc(),SupportTicket.id.desc())
    if queue=="overdue":
        stamp=now()
        rows=[t for t in db.scalars(query) if sla(t,stamp)["overdue"]];count=len(rows);rows=rows[(page-1)*page_size:page*page_size]
    else: rows=list(db.scalars(query.offset((page-1)*page_size).limit(page_size)))
    result=dict(items=[ticket_out(db,t,admin) for t in rows],total=count,page=page,page_size=page_size)
    if admin: result["counts"]=queue_counts(db,actor_id)
    return result

def percentile(values,p):
    if not values: return None
    values=sorted(values); index=(len(values)-1)*p;left=int(index);right=min(left+1,len(values)-1)
    return round(values[left]+(values[right]-values[left])*(index-left),2)

def reports(db,days=7):
    cutoff=now()-timedelta(days=days)
    rows=list(db.scalars(select(SupportTicket).where(SupportTicket.tenant_id=="jjtv",SupportTicket.created_at>=cutoff)))
    first=[sla(t)["first_response_minutes"] for t in rows if t.first_response_at];resolved=[sla(t)["resolution_minutes"] for t in rows if t.resolved_at and t.status!="merged"]
    first_met=[sla(t)["first_response_met"] for t in rows if t.first_response_at];resolution_met=[sla(t)["resolution_met"] for t in rows if t.resolved_at and t.status!="merged"]
    ratings=[t.csat_score for t in rows if t.csat_score is not None]
    reopened=db.scalar(select(func.count(func.distinct(SupportEvent.ticket_id))).join(SupportTicket,SupportTicket.id==SupportEvent.ticket_id).where(SupportTicket.tenant_id=="jjtv",SupportTicket.created_at>=cutoff,SupportEvent.kind=="reopened",SupportEvent.payload["within_7_days"].as_boolean().is_(True))) or 0
    def grouped(key):
        result={}
        for row in rows:
            label=str(getattr(row,key) or "unassigned");result[label]=result.get(label,0)+1
        return result
    return dict(days=days,new_tickets=len(rows),resolved_tickets=sum(t.status in {"resolved","closed"} for t in rows),backlog=db.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.tenant_id=="jjtv",SupportTicket.status.notin_(TERMINAL))) or 0,first_response_sla_rate=round(sum(first_met)/len(first_met),4) if first_met else None,resolution_sla_rate=round(sum(resolution_met)/len(resolution_met),4) if resolution_met else None,first_response_p50=percentile(first,.5),first_response_p90=percentile(first,.9),resolution_p50=percentile(resolved,.5),resolution_p90=percentile(resolved,.9),csat_average=round(sum(ratings)/len(ratings),2) if ratings else None,csat_responses=len(ratings),reopen_7d_rate=round(reopened/len(rows),4) if rows else None,by_status=grouped("status"),by_category=grouped("category"),by_assignee=grouped("assignee_id"))
