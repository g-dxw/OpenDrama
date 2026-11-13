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

def tenant_access(db, user, tenant_id, *, paused=False):
    tenant = db.get(Tenant, tenant_id, populate_existing=True)
    if tenant is None: raise HTTPException(404, detail="租户不存在")
    grants = effective_grants(db, user, tenant)
    if not paused and tenant.status != "active":
        raise HTTPException(409, detail={"code": "tenant_paused", "message": "该租户已暂停接入"})
    return tenant, grants['own_role']

def configure_request_scope(request, db, user):
    path = request.url.path
    if path.startswith('/admin/v1/auth/') or path == '/admin/v1/tenants' or path.startswith('/admin/v1/tenants/'):
        return
    tenant_id = request.headers.get('X-JJTV-Tenant', DEFAULT_TENANT)
    tenant, role = tenant_access(db, user, tenant_id)
    if tenant.kind != 'native':
        raise HTTPException(409, detail=dict(code='tenant_content_route_required', message='India 内容请使用专用运营工作台'))
    if tenant_id != DEFAULT_TENANT and not any(re.fullmatch(pattern, path.removeprefix('/admin/v1/').rstrip('/')) and request.method == method for method, pattern, _ in CONTENT_RULES):
        raise HTTPException(403, detail=dict(code='tenant_content_only', message='当前租户仅开放内容运营'))
    grants = effective_grants(db, user, tenant)
    authorize_native_request(request, tenant, grants)
    request.state.tenant_id = tenant.id
    request.state.tenant_role = role
    db.info['content_tenant_id'] = tenant.id

def tenant_list(db, user):
    tenants = db.scalars(select(Tenant).order_by(Tenant.created_at, Tenant.id)).all()
    result = []
    for tenant in tenants:
        try: grants = effective_grants(db, user, tenant)
        except HTTPException as error:
            if error.status_code == 403: continue
            raise
        result.append(dict(id=tenant.id, name=tenant.name, kind=tenant.kind, status=tenant.status, **grants))
    return result

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
    tenant_access(db, user, INDIA_TENANT)
    key = connection_key(request)
    value = connection_cipher().encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()
    row = db.get(IndiaConnection, (INDIA_TENANT, user.id, key))
    if row is None:
        row = IndiaConnection(tenant_id=INDIA_TENANT, user_id=user.id, session_key=key, secret=value)
        db.add(row)
    else: row.secret = value
    if commit: db.commit()
    return row

def clear_connection(db, request, user, *, expected_access_token=None, commit=True):
    key = connection_key(request)
    stmt = delete(IndiaConnection).where(IndiaConnection.tenant_id == INDIA_TENANT, IndiaConnection.user_id == user.id, IndiaConnection.session_key == key)
    if expected_access_token is not None:
        row, current = read_connection(db, request, user)
        if row is None or current.get("access_token") != expected_access_token:
            return
        # A concurrent refresh must not be erased by an old request's 401.
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
