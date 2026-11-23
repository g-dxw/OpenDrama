"""Trusted admin-host bindings. Pending domains never authorize login or CORS."""
from urllib.parse import urlsplit
import os
from fastapi import HTTPException
from sqlalchemy import select,func
from app.modules.tenancy.models import TenantDomain

SHARED_HOSTS={'localhost','127.0.0.1','testserver'}

def shared_hosts():
    return SHARED_HOSTS|{item.strip().lower() for item in os.environ.get('PLATFORM_ADMIN_HOSTS','').split(',') if item.strip()}

def active_admin_domain(db,hostname):
    if not hostname: return None
    return db.scalar(select(TenantDomain).where(func.lower(TenantDomain.hostname)==hostname.lower(),TenantDomain.kind=='admin',TenantDomain.status=='active',TenantDomain.verified_at.is_not(None)))

def validate_entry_origin(request,db,tenant=None,*,platform=False):
    origin=request.headers.get('Origin')
    hostname=urlsplit(origin).hostname if origin else request.url.hostname
    if hostname in shared_hosts(): return
    binding=active_admin_domain(db,hostname)
    if binding is None or platform or tenant is None or binding.tenant_id!=tenant.id:
        raise HTTPException(403,detail=dict(code='tenant_domain_mismatch',message='登录入口与所属租户不一致，或域名尚未启用'))
