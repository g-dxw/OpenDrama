"""Ops service: staff authentication backed by Redis sessions.

Sessions live in Redis (short-lived state per the architecture's storage
split) under a dedicated key prefix with a TTL. Passwords are hashed with
stdlib pbkdf2_sha256 - no extra dependency.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import redis as redis_lib
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import load_env
from app.modules.ops.models import AdminUser
from app.modules.tenancy.identity import SessionPrincipal, StaffPrincipal, is_platform_account
from app.modules.tenancy.models import Tenant, TenantMember

SESSION_PREFIX = "jjtv_admin_session:v2:"
LEGACY_SESSION_PREFIX = "minitv_admin_session:"
SUPPORT_SESSION_TTL = timedelta(minutes=30)
SESSION_TTL = timedelta(hours=8)

_redis_client = None


class SessionStoreUnavailable(Exception):
    """Redis (the admin session store) is unreachable.

    The ops router maps this to HTTP 503 with an actionable message; a bare
    500 would hide the real cause (usually the local ``minitv-redis``
    container being down).
    """


def get_redis():
    global _redis_client
    if _redis_client is None:
        load_env()
        url = os.environ.get("REDIS_URL", "redis://:minitv_local@127.0.0.1:16379/0")
        _redis_client = redis_lib.Redis.from_url(url, decode_responses=True)
    return _redis_client


def reset_redis_for_tests() -> None:
    global _redis_client
    _redis_client = None


# ---- passwords ----

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 120_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, AttributeError):
        return False


# ---- sessions ----

def create_session(user: AdminUser, *, tenant_id: str | None = None, kind: str = 'normal') -> str:
    """Mint an opaque v2 credential with an immutable account/tenant context."""
    if user.status != 'active' or user.account_scope not in {'platform','tenant'}:
        raise ValueError('Cannot issue a session for an inactive or unscoped account')
    if kind not in {'normal','support'}: raise ValueError('Unsupported session kind')
    if kind == 'support':
        if not is_platform_account(user) or not tenant_id: raise ValueError('Support requires a platform account and target tenant')
        ttl = SUPPORT_SESSION_TTL
    elif user.account_scope == 'tenant':
        tenant_id = tenant_id or user.tenant_id
        if not tenant_id or tenant_id != user.tenant_id: raise ValueError('Session tenant does not own the account')
        ttl = SESSION_TTL
    else:
        if tenant_id is not None or user.tenant_id is not None: raise ValueError('Platform session cannot carry a business tenant')
        ttl = SESSION_TTL
    payload = dict(v=2, uid=user.id, account_scope=user.account_scope, tenant_id=tenant_id,
                   kind=kind, auth_version=user.auth_version)
    token = secrets.token_urlsafe(32)
    try:
        get_redis().set(SESSION_PREFIX+token, json.dumps(payload, separators=(',',':')), ex=int(ttl.total_seconds()))
    except redis_lib.RedisError as exc:
        raise SessionStoreUnavailable('redis session store is unavailable') from exc
    return token


def login(session: Session, *, username: str, password: str, tenant_code: str | None = None,
          account_scope: str = 'tenant') -> tuple[AdminUser, str] | None:
    if account_scope == 'platform':
        if tenant_code is not None: return None
        stmt = select(AdminUser).where(AdminUser.account_scope == 'platform', AdminUser.tenant_id.is_(None), AdminUser.username == username)
    elif account_scope == 'tenant':
        if not tenant_code: return None
        tenant = session.scalar(select(Tenant).where(Tenant.code == tenant_code, Tenant.status == 'active'))
        if tenant is None: return None
        stmt = select(AdminUser).where(AdminUser.account_scope == 'tenant', AdminUser.tenant_id == tenant.id, AdminUser.username == username)
    else: return None
    user = session.scalar(stmt)
    if user is None or user.status != 'active' or not verify_password(password, user.password_hash): return None
    if user.account_scope == 'tenant' and user.role != 'partner' and session.get(TenantMember, (user.tenant_id, user.id)) is None:
        return None
    token = create_session(user)
    user.last_login_at = datetime.now(timezone.utc)
    session.commit()
    return user, token


def create_support_session(session: Session, *, platform_user: AdminUser, target_tenant_id: str) -> str:
    platform_user = session.get(AdminUser, platform_user.id, populate_existing=True)
    if platform_user is None or not is_platform_account(platform_user) or platform_user.status != 'active' or platform_user.must_change_password:
        raise ValueError('Support sessions require an active platform account')
    tenant = session.get(Tenant, target_tenant_id, populate_existing=True)
    if tenant is None or tenant.status != 'active': raise ValueError('Support target is not active')
    return create_session(platform_user, tenant_id=tenant.id, kind='support')


def resolve_principal(session: Session, token: str) -> SessionPrincipal | None:
    try:
        raw = get_redis().get(SESSION_PREFIX+token)
    except redis_lib.RedisError as exc:
        raise SessionStoreUnavailable('redis session store is unavailable') from exc
    if raw is None: return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or type(data.get('v')) is not int or data.get('v') != 2: return None
        if type(data.get('uid')) is not int or type(data.get('auth_version')) is not int: return None
        scope, tenant_id, kind = data.get('account_scope'), data.get('tenant_id'), data.get('kind')
        if scope not in {'platform','tenant'} or kind not in {'normal','support'}: return None
        if tenant_id is not None and (not isinstance(tenant_id, str) or not tenant_id): return None
    except (TypeError, ValueError, UnicodeError): return None
    user = session.get(AdminUser, data['uid'], populate_existing=True)
    if user is None or user.status != 'active' or user.account_scope != scope or user.auth_version != data['auth_version']: return None
    if kind == 'support':
        if not is_platform_account(user) or tenant_id is None: return None
    elif scope == 'platform':
        if not is_platform_account(user) or tenant_id is not None: return None
    elif not tenant_id or user.tenant_id != tenant_id: return None
    if tenant_id is not None:
        tenant = session.get(Tenant, tenant_id, populate_existing=True)
        if tenant is None or tenant.status != 'active': return None
        if kind == 'normal' and user.role != 'partner' and session.get(TenantMember, (tenant_id,user.id), populate_existing=True) is None:
            return None
    return SessionPrincipal(user=user, account_scope=scope, tenant_id=tenant_id, kind=kind, auth_version=data['auth_version'])


def resolve_session_token(session: Session, token: str) -> AdminUser | None:
    principal = resolve_principal(session, token)
    return principal.user if principal is not None else None


def logout(token: str) -> None:
    try:
        get_redis().delete(SESSION_PREFIX+token)
    except redis_lib.RedisError as exc:
        raise SessionStoreUnavailable('redis session store is unavailable') from exc


def revoke_user_sessions(user_id: int) -> int:
    """Revoke only this account's v2 sessions, including support contexts."""
    try:
        client = get_redis()
        killed = 0
        for key in client.scan_iter(match=SESSION_PREFIX+'*', count=200):
            try: data = json.loads(client.get(key) or 'null')
            except (ValueError, TypeError, UnicodeError): continue
            if isinstance(data, dict) and data.get('uid') == user_id:
                client.delete(key); killed += 1
        # Legacy tokens are never resolved; remove owned remnants on password reset.
        for key in client.scan_iter(match=LEGACY_SESSION_PREFIX+'*', count=200):
            if client.get(key) == str(user_id): client.delete(key); killed += 1
        return killed
    except redis_lib.RedisError as exc:
        raise SessionStoreUnavailable('redis session store is unavailable') from exc
