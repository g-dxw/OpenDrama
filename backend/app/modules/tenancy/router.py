"""The fixed India content adapter; platform and tenant IAM live in management."""
from __future__ import annotations
import json
import os
import re
from uuid import UUID
from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy.orm import Session
from app.db import get_db
from app.modules.common.models import AuditLog
from app.modules.ops.models import AdminUser
from app.modules.ops.router import require_admin
from app.modules.ops import service as ops_service
from app.modules.tenancy import service
from app.modules.tenancy.models import INDIA_TENANT
from app.modules.tenancy.permissions import require_permission
from app.modules.tenancy.india import IndiaClient,IndiaError,normalize_login,normalize_staff
router=APIRouter(prefix='/admin/v1/tenants',tags=['india-content'])

class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

class ChallengeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_token: str = Field(min_length=43, max_length=43)

class MfaInput(ChallengeInput):
    code: str = Field(pattern=r"^[0-9]{6}$")

def audit(db,user,tenant_id,action,*,old_actor=None,request_id=None,outcome='success',code=None,resource_id=None):
    payload=dict(tenant_id=tenant_id,outcome=outcome)
    if old_actor: payload['upstream_actor_id']=old_actor
    if request_id: payload['upstream_request_id']=request_id
    if code: payload['upstream_code']=code
    if resource_id: payload['resource_id']=resource_id
    db.add(AuditLog(actor_type='admin',actor_id=user.id,actor_scope=db.info.get('actor_scope',user.account_scope),tenant_id=tenant_id,action=action,target_type='tenant',target_id=None,payload=payload)); db.commit()

def india_access(tenant_id,db,user,request,*,write=False):
    tenant=request.state.tenant
    if tenant.id!=tenant_id: raise HTTPException(403,detail=dict(code='tenant_scope_mismatch',message='当前会话不能切换租户'))
    if tenant.id!=INDIA_TENANT or tenant.kind!='india_legacy': raise HTTPException(404,detail='该租户没有旧站内容接口')
    require_permission(request,'dramas.view','reviews.view',any_of=True)
    service.connection_key(request)
    return tenant

def describe(data=None, *, configured=True):
    data = data or {}
    actor = data.get("user") or {}
    connected = data.get("state") == "connected"
    result = {"configured": configured, "connected": connected, "state": data.get("state", "disconnected")}
    if connected:
        result.update(username=actor.get("name") or actor.get("id"), role=actor.get("role"))
        if data.get("expires_at"): result["expires_at"] = data["expires_at"]
    if data.get("state") == "mfa_required":
        result.update(mfa_required=True, challenge_token=data["challenge_token"], expires_at=data["expires_at"], enrollment_required=data.get("enrollment_required", False))
    return result

def fail_india(error, db, request, user, *, expected_access_token=None):
    if error.code == "india_auth_required":
        service.clear_connection(db, request, user, expected_access_token=expected_access_token)
    messages = {"india_auth_required": "旧站授权已失效，请重新连接运营账号", "india_not_configured": "India 接入尚未配置",
                "india_connection_failed": "暂时无法连接旧站，请稍后重试", "india_timeout": "旧站请求超时，请查询状态后再决定是否重试"}
    raise HTTPException(error.status_code, detail={"code": error.code, "message": messages.get(error.code, "旧站拒绝或未完成此操作"),
        "upstream_code": error.upstream_code, "request_id": error.request_id, "retryable": error.retryable})

def active_connection(db, request, user):
    _, data = service.read_connection(db, request, user)
    if data.get("state") != "connected": raise HTTPException(409, detail={"code": "india_auth_required", "message": "请先连接旧站运营账号"})
    return data

def auth_limit(user):
    try:
        client = ops_service.get_redis()
        key = "jjtv:india:auth:" + str(user.id)
        count = client.incr(key)
        if count == 1: client.expire(key, 900)
        if count > 12: raise HTTPException(429, detail="验证请求过于频繁，请稍后重试")
    except HTTPException: raise
    except Exception: raise HTTPException(503, detail="授权验证暂时不可用") from None

@router.get("/{tenant_id}/india/session")
async def india_session(tenant_id: str, request: Request, response: Response, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    response.headers["Cache-Control"] = "no-store"
    india_access(tenant_id, db, user, request)
    if not os.environ.get("INDIA_API_BASE_URL") or not os.environ.get("TENANT_CONNECTION_KEY"):
        return describe(configured=False)
    _, data = service.read_connection(db, request, user)
    if data.get("state") == "connected":
        try:
            reply = await IndiaClient().call("me", access_token=data["access_token"])
            actor = normalize_staff(reply.data)
            if actor["id"] != data["user"]["id"]: raise IndiaError("india_auth_required", 409)
            data["user"] = actor
        except IndiaError as error:
            if error.code == "india_auth_required":
                service.clear_connection(db, request, user, expected_access_token=data.get("access_token"))
                _, remaining = service.read_connection(db, request, user)
                return describe(remaining)
            fail_india(error, db, request, user)
    return describe(data)

@router.post("/{tenant_id}/india/session")
async def india_login(tenant_id: str, body: LoginInput, request: Request, response: Response, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    with service.connection_mutation(request, user):
        response.headers["Cache-Control"] = "no-store"
        india_access(tenant_id, db, user, request); service.connection_cipher(); auth_limit(user)
        try:
            reply = await IndiaClient().call("login", payload=body.model_dump())
            data = normalize_login(reply.data)
            service.save_connection(db, request, user, data, commit=False)
            audit(db, user, tenant_id, "india.login", old_actor=(data.get("user") or {}).get("id"), request_id=reply.request_id)
            return describe(data)
        except IndiaError as error:
            audit(db, user, tenant_id, "india.login", outcome="rejected", code=error.upstream_code, request_id=error.request_id)
            fail_india(error, db, request, user)

def own_challenge(db, request, user, challenge):
    _, data = service.read_connection(db, request, user)
    if data.get("state") != "mfa_required" or data.get("challenge_token") != challenge:
        raise HTTPException(409, detail={"code": "india_challenge_invalid", "message": "验证已失效，请重新连接"})
    try: valid = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)
    except (ValueError, KeyError, TypeError): valid = False
    if not valid:
        service.clear_connection(db, request, user)
        raise HTTPException(409, detail={"code": "india_challenge_expired", "message": "验证码已过期，请重新连接"})
    return data

@router.post("/{tenant_id}/india/session/mfa")
async def india_mfa(tenant_id: str, body: MfaInput, request: Request, response: Response, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    with service.connection_mutation(request, user):
        response.headers["Cache-Control"] = "no-store"
        india_access(tenant_id, db, user, request); own_challenge(db, request, user, body.challenge_token); auth_limit(user)
        try:
            reply = await IndiaClient().call("mfa_verify", payload=body.model_dump())
            data = normalize_login(reply.data)
            if data["state"] != "connected": raise IndiaError("india_invalid_response")
            service.save_connection(db, request, user, data, commit=False)
            audit(db, user, tenant_id, "india.mfa", old_actor=data["user"]["id"], request_id=reply.request_id)
            return describe(data)
        except IndiaError as error: fail_india(error, db, request, user)

@router.post("/{tenant_id}/india/session/enrollment")
async def india_enrollment(tenant_id: str, body: ChallengeInput, request: Request, response: Response, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    with service.connection_mutation(request, user):
        response.headers["Cache-Control"] = "no-store"
        india_access(tenant_id, db, user, request)
        data = own_challenge(db, request, user, body.challenge_token)
        if not data.get("enrollment_required"): raise HTTPException(409, detail="当前账号不需要首次验证器登记")
        try:
            reply = await IndiaClient().call("mfa_enrollment", payload=body.model_dump())
            return {"data": reply.data, "request_id": reply.request_id}
        except IndiaError as error: fail_india(error, db, request, user)

@router.post("/{tenant_id}/india/session/cancel")
async def india_cancel(tenant_id: str, body: ChallengeInput, request: Request, response: Response, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    with service.connection_mutation(request, user):
        response.headers["Cache-Control"] = "no-store"
        india_access(tenant_id, db, user, request); own_challenge(db, request, user, body.challenge_token)
        try: await IndiaClient().call("mfa_cancel", payload=body.model_dump())
        except IndiaError: pass
        service.clear_connection(db, request, user)
        return describe()

@router.post("/{tenant_id}/india/session/refresh")
async def india_refresh(tenant_id: str, request: Request, response: Response, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    with service.connection_mutation(request, user):
        response.headers["Cache-Control"] = "no-store"
        india_access(tenant_id, db, user, request)
        data = active_connection(db, request, user)
        try:
            reply = await IndiaClient().call("refresh", payload={"refresh_token": data["refresh_token"]})
            # The upstream refresh rotates tokens but does not return a user object.
            updated = normalize_login({**reply.data, "user": data["user"]})
            service.save_connection(db, request, user, updated, commit=False)
            audit(db, user, tenant_id, "india.refresh", old_actor=updated["user"]["id"], request_id=reply.request_id)
            return describe(updated)
        except IndiaError as error: fail_india(error, db, request, user)

@router.delete("/{tenant_id}/india/session")
def india_disconnect(tenant_id: str, request: Request, response: Response, db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    with service.connection_mutation(request, user):
        response.headers["Cache-Control"] = "no-store"
        india_access(tenant_id, db, user, request)
        service.clear_connection(db, request, user, commit=False)
        audit(db, user, tenant_id, "india.disconnect")
        return describe()

# Exact content routes only: this is deliberately not a general URL proxy.
ROUTES = [
    ("GET", r"admin/series", "series_list"), ("POST", r"admin/series", "series_create"),
    ("GET", r"admin/series/([A-Za-z0-9_-]{1,160})", "series_detail"),
    ("PATCH", r"admin/series/([A-Za-z0-9_-]{1,160})", "series_edit"),
    ("POST", r"admin/series/([A-Za-z0-9_-]{1,160})/episodes", "episodes_create"),
    ("POST", r"admin/episodes/([A-Za-z0-9_-]{1,160})/uploads", "episode_upload_init"),
    ("POST", r"admin/uploads/([A-Za-z0-9_-]{1,160})/complete", "upload_complete"),
    ("GET", r"admin/jobs", "jobs_list"), ("GET", r"admin/jobs/([A-Za-z0-9_-]{1,160})", "job_detail"),
    ("POST", r"admin/jobs/([A-Za-z0-9_-]{1,160})/retry", "job_retry"),
    ("POST", r"admin/episodes/([A-Za-z0-9_-]{1,160})/subtitles", "subtitles"),
    ("POST", r"admin/series/([A-Za-z0-9_-]{1,160})/submit", "submit"),
    ("POST", r"admin/series/([A-Za-z0-9_-]{1,160})/review", "review"),
    ("POST", r"admin/series/([A-Za-z0-9_-]{1,160})/publish", "publish"),
    ("POST", r"admin/series/([A-Za-z0-9_-]{1,160})/unpublish", "unpublish"),
    ("GET", r"admin/series/([A-Za-z0-9_-]{1,160})/rights/evidence", "rights_list"),
    ("POST", r"admin/series/([A-Za-z0-9_-]{1,160})/rights/evidence", "rights_upload_init"),
    ("POST", r"admin/series/([A-Za-z0-9_-]{1,160})/cover/uploads", "cover_upload_init"),
    ("GET", r"admin/series/([A-Za-z0-9_-]{1,160})/publications", "series_publications"),
]
UPLOAD_ROUTES = [(r"admin/uploads/([A-Za-z0-9_-]{1,160})/content", "video"),
                 (r"admin/covers/([A-Za-z0-9_-]{1,160})/content", "cover"),
                 (r"admin/rights/evidence/([A-Za-z0-9_-]{1,160})/content", "rights")]

async def bounded_json(request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 256 * 1024: raise HTTPException(413, detail="请求内容过大")
    try: data = json.loads(raw or b"{}")
    except (ValueError, UnicodeError): raise HTTPException(400, detail="请求格式无效") from None
    if not isinstance(data, dict): raise HTTPException(400, detail="请求必须是 JSON 对象")
    return data

@router.api_route("/{tenant_id}/india/{upstream_path:path}", methods=["GET", "POST", "PATCH", "PUT"])
async def india_content(tenant_id: str, upstream_path: str, request: Request, response: Response,
                        db: Session = Depends(get_db), user: AdminUser = Depends(require_admin)):
    response.headers["Cache-Control"] = "no-store"
    india_access(tenant_id, db, user, request, write=request.method != "GET")
    action = None; resource_id = None; upload_kind = None
    for method, pattern, name in ROUTES:
        match = re.fullmatch(pattern, upstream_path)
        if method == request.method and match:
            action = name; resource_id = match.group(1) if match.groups() else None; break
    if request.method == "PUT":
        for pattern, kind in UPLOAD_ROUTES:
            match = re.fullmatch(pattern, upstream_path)
            if match: action = "upload_" + kind; resource_id = match.group(1); upload_kind = kind; break
    if not action: raise HTTPException(404, detail="没有该内容运营接口")
    if action in {'series_list','series_detail','rights_list','series_publications','jobs_list','job_detail'}:
        require_permission(request, 'dramas.view', 'reviews.view', any_of=True)
    elif action == 'series_edit': require_permission(request, 'dramas.edit', 'dramas.pricing', any_of=True)
    elif action in {'series_create','episodes_create','submit'}: require_permission(request, 'dramas.edit')
    elif action == 'review': require_permission(request, 'reviews.decide')
    elif action in {'publish','unpublish'}: require_permission(request, 'dramas.publish')
    else: require_permission(request, 'dramas.upload')
    key = None
    if request.method != "GET":
        try: key = str(UUID(request.headers.get("Idempotency-Key", "")))
        except ValueError: raise HTTPException(400, detail="操作需要有效 Idempotency-Key") from None
    data = active_connection(db, request, user)
    db.commit()  # Release the read connection before streaming a large upload.
    try:
        client = IndiaClient()
        if upload_kind:
            try: size = int(request.headers.get("Content-Length", ""))
            except ValueError: raise HTTPException(411, detail="上传需要声明文件长度") from None
            reply = await client.upload(upload_kind, resource_id, access_token=data["access_token"], chunks=request.stream(),
                size=size, content_type=request.headers.get("Content-Type", ""), idempotency_key=key)
        else:
            if len(request.query_params.multi_items()) != len(set(request.query_params.keys())): raise HTTPException(400, detail="查询条件不能重复")
            payload = None if request.method == "GET" else await bounded_json(request)
            if payload and action in {'series_create','series_edit'}:
                # PATCH requires only version upstream. Never forward a protected
                # field merely because a preflight snapshot appeared unchanged.
                for field in payload:
                    if field == 'version': continue
                    permission = 'dramas.pricing' if field in {'free_episode_count','member_window'} else 'dramas.edit'
                    require_permission(request, permission)
            reply = await client.call(action, access_token=data["access_token"], resource_id=resource_id,
                payload=payload, params=dict(request.query_params), idempotency_key=key)
        if request.method != "GET":
            audit(db, user, tenant_id, "india." + action, old_actor=data["user"]["id"], request_id=reply.request_id, resource_id=resource_id)
        return {"data": reply.data, "request_id": reply.request_id}
    except IndiaError as error:
        if request.method != "GET": audit(db, user, tenant_id, "india." + action, old_actor=data["user"]["id"],
            request_id=error.request_id, outcome="rejected", code=error.upstream_code, resource_id=resource_id)
        fail_india(error, db, request, user, expected_access_token=data.get("access_token"))
