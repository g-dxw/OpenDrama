"""Tenant access checks and encrypted, session-bound India authorization storage."""
from __future__ import annotations
import hashlib
import secrets
from contextlib import contextmanager
import json
import os
import re
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select, delete
from app.modules.ops.models import AdminUser
from app.modules.tenancy.models import DEFAULT_TENANT, INDIA_TENANT, Tenant, TenantMember, IndiaConnection

from app.modules.tenancy.permissions import effective_grants, authorize_native_request, CONTENT_RULES

def tenant_access(db,user,tenant_id,*,paused=False,principal=None):
    tenant=db.get(Tenant,tenant_id,populate_existing=True)
    if tenant is None: raise HTTPException(404,detail='租户不存在')
    grants=effective_grants(db,user,tenant,principal)
    if not paused and tenant.status!='active': raise HTTPException(409,detail=dict(code='tenant_paused',message='此租户已暂停后台接入'))
    return tenant,grants['own_role']

def bind_tenant_context(request,db,user):
    principal=getattr(request.state,'principal',None)
    if principal is None or not principal.tenant_id or (principal.account_scope=='platform' and principal.kind!='support'):
        raise HTTPException(403,detail=dict(code='tenant_context_required',message='请先登录租户账号，或从平台显式进入租户'))
    selected=request.headers.get('X-JJTV-Tenant')
    if selected and selected!=principal.tenant_id:
        raise HTTPException(403,detail=dict(code='tenant_scope_mismatch',message='当前会话不能切换所属租户'))
    tenant,_=tenant_access(db,user,principal.tenant_id,principal=principal)
    from app.modules.tenancy.domains import validate_entry_origin
    validate_entry_origin(request,db,tenant)
    grants=effective_grants(db,user,tenant,principal)
    request.state.tenant=tenant; request.state.tenant_id=tenant.id
    request.state.tenant_role=grants['own_role']; request.state.tenant_grants=grants
    request.state.tenant_permissions=grants['permissions']; request.state.db_for_tenant=db
    db.info['content_tenant_id']=tenant.id
    db.info['actor_scope']=principal.account_scope
    # Only explicit tenant business requests bind audit context. The default
    # public JJTV scope must not turn platform control-plane events into JJTV events.
    route=getattr(request.scope.get('route'),'path',None) or request.url.path
    safe_params={}
    for key,value in request.path_params.items():
        if any(part in key.lower() for part in ('token','password','secret','credential','signature','session')):
            continue
        if type(value) is int or isinstance(value,str):
            safe_params[key[:64]]=value if type(value) is int else value[:128]
    db.info['tenant_audit_context']=dict(actor_id=user.id,actor_scope=principal.account_scope,
        tenant_id=tenant.id,method=request.method,path=route[:512],path_params=safe_params)
    return tenant

def configure_request_scope(request,db,user):
    path=request.url.path
    if path in {'/admin/v1/auth/me','/admin/v1/auth/logout','/admin/v1/auth/password'} or path.startswith('/platform/'):
        return
    bind_tenant_context(request,db,user)
    if path.startswith('/tenant/v1/') or path.startswith('/admin/v1/tenants') or (path=='/admin/v1/auth/keys' and request.method=='GET'):
        return
    tenant=request.state.tenant
    if tenant.kind!='native': raise HTTPException(409,detail=dict(code='tenant_content_route_required',message='India 内容请使用专用运营入口'))
    authorize_native_request(request,tenant,request.state.tenant_grants)

def tenant_list(db,user):
    if user.account_scope=='platform':
        tenants=db.scalars(select(Tenant).order_by(Tenant.created_at,Tenant.id)).all()
        return [dict(id=t.id,code=t.code,name=t.name,kind=t.kind,status=t.status,version=t.version) for t in tenants]
    tenant=db.get(Tenant,user.tenant_id)
    if tenant is None: return []
    return [dict(id=tenant.id,code=tenant.code,name=tenant.name,kind=tenant.kind,status=tenant.status,version=tenant.version,**effective_grants(db,user,tenant))]

def connection_cipher():
    secret = os.environ.get("TENANT_CONNECTION_KEY", "").strip()
    if not secret:
        raise HTTPException(503, detail={"code": "india_not_configured", "message": "旧站授权存储尚未配置"})
    try: return Fernet(secret.encode("ascii"))
    except (ValueError, UnicodeError):
        raise HTTPException(503, detail={"code": "india_not_configured", "message": "旧站授权存储配置无效"}) from None

def connection_key(request):
    if getattr(request.state, "api_key_id", None) is not None:
        raise HTTPException(403, detail="旧站授权需要交互式员工登录")
    auth = request.headers.get("Authorization", "")
    return hashlib.sha256(auth.encode()).hexdigest()

def read_connection(db, request, user):
    row = db.get(IndiaConnection, (INDIA_TENANT, user.id, connection_key(request)), populate_existing=True)
    if row is None: return None, {}
    try:
        data = json.loads(connection_cipher().decrypt(row.secret.encode()).decode())
        return row, data
    except (InvalidToken, ValueError, UnicodeError):
        # Delete only the corrupt value that was read, preserving a concurrent reconnect.
        db.execute(delete(IndiaConnection).where(
            IndiaConnection.tenant_id == INDIA_TENANT, IndiaConnection.user_id == user.id,
            IndiaConnection.session_key == row.session_key, IndiaConnection.secret == row.secret))
        db.commit()
        raise HTTPException(409, detail={"code": "india_auth_required", "message": "旧站授权已失效，请重新连接"}) from None

def save_connection(db, request, user, data, *, commit=True):
    tenant, _ = tenant_access(db, user, INDIA_TENANT, principal=getattr(request.state,'principal',None))
    if not set(effective_grants(db, user, tenant, getattr(request.state,'principal',None))['permissions']) & {'dramas.view','reviews.view'}:
        from app.modules.tenancy.permissions import deny
        deny(('dramas.view','reviews.view'))
    key = connection_key(request)
    value = connection_cipher().encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()
    row = db.get(IndiaConnection, (INDIA_TENANT, user.id, key))
    if row is None:
        row = IndiaConnection(tenant_id=INDIA_TENANT, user_id=user.id, session_key=key, secret=value)
        db.add(row)
    else: row.secret = value
    if commit: db.commit()
    return row

def clear_connection(db, request, user, *, expected_access_token=None, expected_challenge_token=None, commit=True):
    key = connection_key(request)
    stmt = delete(IndiaConnection).where(IndiaConnection.tenant_id == INDIA_TENANT, IndiaConnection.user_id == user.id, IndiaConnection.session_key == key)
    if expected_access_token is not None or expected_challenge_token is not None:
        row, current = read_connection(db, request, user)
        if row is None:
            return
        if expected_access_token is not None and current.get("access_token") != expected_access_token:
            return
        if expected_challenge_token is not None and (current.get("state") != "mfa_required"
                                                   or current.get("challenge_token") != expected_challenge_token):
            return
        # An old token/challenge failure must not erase a concurrent replacement.
        stmt = stmt.where(IndiaConnection.secret == row.secret)
    db.execute(stmt)
    if commit: db.commit()


@contextmanager
def connection_mutation(request, user):
    # A refresh token is single-use. Distributed, fail-fast serialization avoids
    # an event-loop-blocking DB lock and prevents concurrent refresh replay.
    from app.modules.ops.service import get_redis
    key = "jjtv:india:connection:" + str(user.id) + ":" + connection_key(request)
    owner = secrets.token_urlsafe(24)
    try:
        client = get_redis()
        acquired = client.set(key, owner, nx=True, ex=90)
    except Exception:
        raise HTTPException(503, detail="授权会话存储暂时不可用") from None
    if not acquired:
        raise HTTPException(409, detail={"code": "india_connection_busy", "message": "旧站授权操作正在处理，请稍后重试"})
    try:
        yield
    finally:
        try:
            client.eval("if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end", 1, key, owner)
        except Exception:
            pass  # The bounded lease expires; never delete another owner's lock.
