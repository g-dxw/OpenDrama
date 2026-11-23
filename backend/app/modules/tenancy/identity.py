"""Identity context shared by interactive sessions and machine credentials."""
from __future__ import annotations
from dataclasses import dataclass
from fastapi import HTTPException
from app.modules.ops.models import AdminUser

@dataclass(frozen=True)
class SessionPrincipal:
    user: AdminUser
    account_scope: str
    tenant_id: str | None
    kind: str
    auth_version: int

    @property
    def is_support(self) -> bool:
        return self.kind == 'support'

StaffPrincipal = SessionPrincipal

def account_tenant_matches(user: AdminUser, tenant_id: str | None) -> bool:
    return user.account_scope == 'tenant' and tenant_id is not None and user.tenant_id == tenant_id

def require_account_tenant(user: AdminUser, tenant_id: str | None) -> None:
    if not account_tenant_matches(user, tenant_id):
        raise HTTPException(403, detail=dict(code='account_tenant_mismatch', message='账号不属于当前租户'))

def is_platform_account(user: AdminUser) -> bool:
    return user.account_scope == 'platform' and user.tenant_id is None
