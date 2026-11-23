"""Tenant access checks and request scoping."""
from __future__ import annotations
from fastapi import HTTPException
from sqlalchemy import select
from app.modules.ops.models import AdminUser
from app.modules.tenancy.models import Tenant, TenantMember

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
    selected=request.headers.get('X-OpenDrama-Tenant')
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
