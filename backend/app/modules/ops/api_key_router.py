"""Admin endpoints for API key management.

Six operations plus two readers, matching docs/admin-api-key-remediation.md §6.
Everything except ``/auth/keys`` requires the ``admin`` role: issuing a
long-lived credential is a super-admin action, and an ``ops`` account must not
be able to mint one for itself.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.ops import api_keys as service
from app.modules.ops.models import STATUS_ACTIVE, AdminApiKey, AdminUser, ApiKeyEvent
from app.modules.ops.router import require_admin
from app.modules.tenancy.permissions import require_permission

router = APIRouter(prefix="/admin/v1", tags=["admin-api-keys"])


def _context_tenant(request: Request) -> str:
    principal = getattr(request.state, 'principal', None)
    if principal is None or not principal.tenant_id:
        raise HTTPException(403, detail=dict(code='tenant_context_required', message='密钥管理需要当前租户上下文'))
    return principal.tenant_id

def require_key_reader(request: Request, actor: AdminUser = Depends(require_admin)) -> AdminUser:
    _context_tenant(request)
    require_permission(request, 'api_keys.view')
    return actor

def require_key_manager(request: Request, actor: AdminUser = Depends(require_admin)) -> AdminUser:
    _context_tenant(request)
    require_permission(request, 'api_keys.manage')
    return actor

# ---- schemas ----


class KeyCreateIn(BaseModel):
    user_id: int
    name: str = Field(min_length=1, max_length=64)
    scopes: list[str] | None = None
    # ``None`` explicitly means "never expires" and is admin-only by design
    # (the endpoint itself is admin-only).
    expires_in_days: int | None = Field(default=service.DEFAULT_TTL_DAYS)


class KeyUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    scopes: list[str] | None = None
    expires_at: datetime | None = None


class RevokeIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class RotateIn(BaseModel):
    expires_in_days: int | None = Field(default=service.DEFAULT_TTL_DAYS)


# ---- serialisation ----


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _key_out(key: AdminApiKey, user: AdminUser | None) -> dict:
    """Never serialise key_hash — not even a prefix of it."""
    return {
        "id": key.id,
        "account_scope": key.account_scope,
        "tenant_id": key.tenant_id,
        "name": key.name,
        "user_id": key.admin_user_id,
        "username": user.username if user else None,
        "display_name": user.display_name if user else "",
        "key_prefix": key.key_prefix,
        "scopes": list(key.scopes or []),
        "state": service.key_state(key),
        "status": key.status,
        "expires_at": _iso(key.expires_at),
        "last_used_at": _iso(key.last_used_at),
        "created_at": _iso(key.created_at),
        "revoked_at": _iso(key.revoked_at),
        "revoke_reason": key.revoke_reason,
    }


def _issue_out(key: AdminApiKey, user: AdminUser | None, plaintext: str) -> dict:
    """Create / rotate response — the only place the plaintext ever appears."""
    return {**_key_out(key, user), "key": plaintext}


def _get_or_404(db: Session, key_id: int, tenant_id: str) -> AdminApiKey:
    key = db.scalar(select(AdminApiKey).where(AdminApiKey.id == key_id, AdminApiKey.account_scope == "tenant", AdminApiKey.tenant_id == tenant_id))
    if key is None:
        raise HTTPException(
            status_code=404, detail={"code": "key_not_found", "message": "密钥不存在"}
        )
    return key


# ---- read ----


@router.get("/api-keys")
def list_keys(
    request: Request,
    user_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_key_reader),
):
    """Global key list. ``state`` carries the four-way status the UI paints."""
    conditions = [AdminApiKey.account_scope == "tenant", AdminApiKey.tenant_id == _context_tenant(request)]
    if user_id is not None:
        conditions.append(AdminApiKey.admin_user_id == user_id)
    if status in ("active", "revoked"):
        conditions.append(AdminApiKey.status == status)
    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        conditions.append(
            or_(
                AdminApiKey.name.ilike(like),
                AdminApiKey.key_prefix.ilike(like),
                AdminUser.username.ilike(like),
                AdminUser.display_name.ilike(like),
            )
        )

    count_stmt = select(func.count(AdminApiKey.id)).join(
        AdminUser, AdminUser.id == AdminApiKey.admin_user_id
    )
    stmt = select(AdminApiKey, AdminUser).join(
        AdminUser, AdminUser.id == AdminApiKey.admin_user_id
    )
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        stmt = stmt.where(*conditions)
    total = db.scalar(count_stmt) or 0
    rows = db.execute(
        stmt.order_by(AdminApiKey.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()

    # Global counts, unaffected by filters (same idea as the drama list header).
    summary = {"active": 0, "expiring": 0, "expired": 0, "revoked": 0}
    for row in db.scalars(select(AdminApiKey).where(AdminApiKey.account_scope == "tenant", AdminApiKey.tenant_id == _context_tenant(request))):
        summary[service.key_state(row)] += 1

    return {
        "items": [_key_out(key, user) for key, user in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "summary": {**summary, "total": sum(summary.values())},
    }


@router.get("/api-keys/{key_id}/events")
def list_key_events(
    request: Request,
    key_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_key_reader),
):
    _get_or_404(db, key_id, _context_tenant(request))
    rows = db.scalars(
        select(ApiKeyEvent)
        .where(ApiKeyEvent.key_id == key_id)
        .order_by(ApiKeyEvent.id.desc())
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "method": row.method,
                "path": row.path,
                "status_code": row.status_code,
                "duration_ms": row.duration_ms,
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ]
    }


@router.get("/auth/keys")
def list_own_keys(
    request: Request,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_admin),
):
    """Self-service view: a staff member can audit their own credentials."""
    tenant_id = _context_tenant(request)
    if actor.account_scope != 'tenant' or actor.tenant_id != tenant_id: return {'items': []}
    rows = db.scalars(
        select(AdminApiKey)
        .where(AdminApiKey.admin_user_id == actor.id, AdminApiKey.account_scope == 'tenant', AdminApiKey.tenant_id == tenant_id)
        .order_by(AdminApiKey.id.desc())
    ).all()
    return {"items": [_key_out(key, actor) for key in rows]}


# ---- write ----


@router.post("/api-keys", status_code=201)
def create_key(
    request: Request,
    body: KeyCreateIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_key_manager),
):
    owner = db.get(AdminUser, body.user_id)
    if owner is None or owner.account_scope != 'tenant' or owner.tenant_id != _context_tenant(request):
        raise HTTPException(404, detail=dict(code='staff_not_found', message='当前租户不存在此员工'))
    key, plaintext = service.create_key(
        db,
        admin_user_id=body.user_id,
        name=body.name,
        scopes=body.scopes,
        expires_in_days=body.expires_in_days,
        actor_id=actor.id,
    )
    return _issue_out(key, db.get(AdminUser, key.admin_user_id), plaintext)


@router.patch("/api-keys/{key_id}")
def update_key(
    request: Request,
    key_id: int,
    body: KeyUpdateIn,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_key_manager),
):
    key = _get_or_404(db, key_id, _context_tenant(request))
    data = body.model_dump(exclude_unset=True)
    key = service.update_key(
        db,
        key,
        name=data.get("name"),
        # ``scopes: null`` is treated as "leave alone" rather than "reset".
        scopes=data.get("scopes") if data.get("scopes") is not None else None,
        expires_at=data.get("expires_at"),
        expires_at_set="expires_at" in data,
        actor_id=actor.id,
    )
    return _key_out(key, db.get(AdminUser, key.admin_user_id))


@router.post("/api-keys/{key_id}/revoke")
def revoke_key(
    request: Request,
    key_id: int,
    body: RevokeIn | None = None,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_key_manager),
):
    key = _get_or_404(db, key_id, _context_tenant(request))
    key = service.revoke_key(
        db, key, reason=body.reason if body else None, actor_id=actor.id
    )
    return _key_out(key, db.get(AdminUser, key.admin_user_id))


@router.post("/api-keys/{key_id}/rotate", status_code=201)
def rotate_key(
    request: Request,
    key_id: int,
    body: RotateIn | None = None,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_key_manager),
):
    key = _get_or_404(db, key_id, _context_tenant(request))
    fresh, plaintext = service.rotate_key(
        db,
        key,
        expires_in_days=body.expires_in_days if body else service.DEFAULT_TTL_DAYS,
        actor_id=actor.id,
    )
    return _issue_out(fresh, db.get(AdminUser, fresh.admin_user_id), plaintext)


@router.delete("/api-keys/{key_id}", status_code=204)
def delete_key(
    request: Request,
    key_id: int,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_key_manager),
):
    key = _get_or_404(db, key_id, _context_tenant(request))
    service.delete_key(db, key, actor_id=actor.id)
    return Response(status_code=204)

# A second mount preserves endpoint behaviour while the original admin routes
# remain explicit compatibility aliases. Root's request guard binds the tenant.
tenant_router = APIRouter(prefix='/tenant/v1', tags=['tenant-api-keys'])
for _route in router.routes:
    tenant_router.add_api_route(_route.path.removeprefix('/admin/v1'), _route.endpoint,
        methods=list(_route.methods), status_code=_route.status_code, response_model=_route.response_model,
        response_class=_route.response_class, name=_route.name, include_in_schema=_route.include_in_schema)
