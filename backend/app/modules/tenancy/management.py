"""Platform control plane and tenant-owned role administration."""
from __future__ import annotations
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import ipaddress
import re
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db import get_db
from app.modules.common.models import AuditLog
from app.modules.ops.models import AdminUser
from app.modules.ops import service as ops_service
from app.modules.ops.router import require_identity,require_platform_admin,require_tenant_admin,_bearer_token
from app.modules.tenancy.identity import SessionPrincipal
from app.modules.tenancy.models import Tenant,TenantRole,TenantMember,TenantModule,TenantDomain,IndiaConnection
from app.modules.tenancy import service
from app.modules.tenancy.permissions import seed_tenant_defaults,preset_role_id,permission_catalog,validate_permissions,preset_permissions
from app.modules.tenancy.modules import BY_KEY,module_catalog,supported_keys
from app.modules.tenancy.context import build_context,user_info,tenant_info
from app.modules.tenancy.domains import validate_entry_origin,shared_hosts

platform_router=APIRouter(prefix='/platform/v1',tags=['platform'])
tenant_router=APIRouter(prefix='/tenant/v1',tags=['tenant'])
legacy_router=APIRouter(prefix='/admin/v1/tenants',tags=['tenant-compatibility'])

class Strict(BaseModel): model_config=ConfigDict(extra='forbid')
class Login(Strict):
    username:str=Field(min_length=1,max_length=64)
    password:str=Field(min_length=1,max_length=128)
class TenantLogin(Login): tenant_code:str|None=Field(default=None,max_length=64)
class Password(Strict):
    old_password:str
    new_password:str=Field(min_length=8,max_length=128)
class CreateTenant(Strict):
    code:str=Field(pattern=r'^[a-z0-9][a-z0-9-]{1,63}$')
    name:str=Field(min_length=1,max_length=100)
    admin_username:str=Field(default='tenant-admin',min_length=3,max_length=64)
    modules:list[str]|None=Field(default=None,max_length=32)
class PatchTenant(Strict):
    version:int=Field(ge=1)
    name:str|None=Field(default=None,min_length=1,max_length=100)
    status:str|None=None
class Modules(Strict):
    version:int=Field(ge=1)
    module_keys:list[str]=Field(max_length=32)
class Domain(Strict):
    hostname:str=Field(min_length=3,max_length=253)
    kind:str=Field(pattern='^(admin|site)$')
class Administrator(Strict):
    username:str=Field(min_length=3,max_length=64)
    display_name:str=Field(default='',max_length=64)
class RoleCreate(Strict):
    name:str=Field(min_length=1,max_length=80)
    permissions:list[str]=Field(max_length=100)
class RolePatch(Strict):
    version:int=Field(ge=1)
    name:str|None=Field(default=None,min_length=1,max_length=80)
    permissions:list[str]|None=Field(default=None,max_length=100)


def clean_name(value):
    value=value.strip()
    if not value: raise HTTPException(422,detail='名称不能为空')
    return value

def audit(db,request,user,tenant_id,action,resource_id=None,payload=None):
    data=dict(payload or {})
    if tenant_id: data['tenant_id']=tenant_id
    if resource_id: data['resource_id']=resource_id
    principal=getattr(request.state,'principal',None)
    db.add(AuditLog(actor_type='admin',actor_id=user.id,actor_scope=principal.account_scope if principal else user.account_scope,tenant_id=tenant_id,action=action,target_type='tenant',target_id=None,payload=data))

def commit(db,message='配置发生冲突，请刷新后重试'):
    try: db.commit()
    except IntegrityError:
        db.rollback(); raise HTTPException(409,detail=message) from None

def platform_tenant(db,identifier,*,lock=False):
    query=select(Tenant).where(Tenant.id==identifier).execution_options(populate_existing=True)
    if lock: query=query.with_for_update()
    tenant=db.scalar(query)
    if tenant is None: raise HTTPException(404,detail='租户不存在')
    return tenant

def check_version(tenant,version):
    if tenant.version!=version: raise HTTPException(409,detail='租户配置已变化，请刷新后重试')

def allowed_modules(db,tenant,keys):
    values=set(keys)
    if len(values)!=len(keys) or not values<=supported_keys(db,tenant):
        raise HTTPException(422,detail='包含重复、未知或尚未适配的功能模块')
    return values

def validate_tenant_auth_context(request,db,principal):
    if not principal.tenant_id:
        raise HTTPException(403,detail=dict(code='tenant_context_required',message='请从租户入口登录'))
    if request.headers.get('X-JJTV-Tenant') not in (None,principal.tenant_id):
        raise HTTPException(403,detail=dict(code='tenant_scope_mismatch',message='当前会话不能切换租户'))
    tenant=platform_tenant(db,principal.tenant_id)
    validate_entry_origin(request,db,tenant)
    return tenant

def login_response(db,result):
    if result is None: raise HTTPException(401,detail='账号、密码或所属入口不正确')
    user,token=result
    principal=ops_service.resolve_principal(db,token)
    if principal is None: raise HTTPException(503,detail='会话服务暂时不可用')
    return dict(token=token,context=build_context(db,principal))

@platform_router.post('/auth/login')
def platform_login(body:Login,request:Request,db:Session=Depends(get_db)):
    validate_entry_origin(request,db,platform=True)
    return login_response(db,ops_service.login(db,username=body.username,password=body.password,account_scope='platform'))

@tenant_router.post('/auth/login')
def tenant_login(body:TenantLogin,request:Request,db:Session=Depends(get_db)):
    code=(body.tenant_code or '').strip()
    if not code: raise HTTPException(400,detail=dict(code='tenant_required',message='请使用平台入口或提供租户编码'))
    tenant=db.scalar(select(Tenant).where(Tenant.code==code))
    if tenant is None: raise HTTPException(401,detail='账号、密码或所属入口不正确')
    validate_entry_origin(request,db,tenant)
    return login_response(db,ops_service.login(db,username=body.username,password=body.password,tenant_code=tenant.code,account_scope='tenant'))

@platform_router.get('/context')
def platform_context(request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    return build_context(db,request.state.principal)

@tenant_router.get('/context')
def tenant_context(request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_identity)):
    principal=request.state.principal
    validate_tenant_auth_context(request,db,principal)
    return build_context(db,principal)

@platform_router.post('/auth/logout')
def platform_logout(request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    audit(db,request,user,None,'platform.logout'); ops_service.logout(_bearer_token(request)); db.commit()
    return dict(logged_out=True)

@tenant_router.post('/auth/logout')
def tenant_logout(request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_identity)):
    principal=request.state.principal
    validate_tenant_auth_context(request,db,principal)
    audit(db,request,user,principal.tenant_id,'tenant.support.exit' if principal.kind=='support' else 'tenant.logout')
    ops_service.logout(_bearer_token(request)); db.commit()
    return dict(logged_out=True)

def change_password(body,request,db,user,scope):
    principal=request.state.principal
    if principal.kind!='normal' or principal.account_scope!=scope:
        raise HTTPException(403,detail='请在对应账号的交互登录入口修改密码')
    from app.modules.ops.router import lock_password_account
    user=lock_password_account(db,user,principal)
    if not ops_service.verify_password(body.old_password,user.password_hash): raise HTTPException(400,detail='原密码不正确')
    if body.new_password==body.old_password: raise HTTPException(422,detail='新密码不能与原密码相同')
    from app.modules.ops.router import _validate_password
    _validate_password(body.new_password,user.username)
    user.password_hash=ops_service.hash_password(body.new_password); user.auth_version+=1; user.must_change_password=False
    ops_service.revoke_user_sessions(user.id)
    db.execute(delete(IndiaConnection).where(IndiaConnection.user_id==user.id))
    audit(db,request,user,principal.tenant_id,'account.password.change')
    token=ops_service.create_session(user)
    db.commit()
    return dict(token=token,context=build_context(db,SessionPrincipal(user,user.account_scope,user.tenant_id,'normal',user.auth_version)))

@platform_router.post('/auth/password')
def platform_password(body:Password,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    return change_password(body,request,db,user,'platform')

@tenant_router.post('/auth/password')
def tenant_password(body:Password,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_identity)):
    validate_tenant_auth_context(request,db,request.state.principal)
    return change_password(body,request,db,user,'tenant')

@platform_router.get('/tenants')
@legacy_router.get('')
def tenants(request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    return [tenant_info(t) for t in db.scalars(select(Tenant).order_by(Tenant.created_at,Tenant.id))]

@platform_router.post('/tenants',status_code=201)
def create_tenant(body:CreateTenant,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    from app.modules.tenancy.tenant_staff import create_tenant_account
    tenant=Tenant(id='tenant-'+uuid4().hex,code=body.code,name=clean_name(body.name),kind='native',status='active',version=1)
    db.add(tenant)
    try: db.flush()
    except IntegrityError: db.rollback(); raise HTTPException(409,detail='租户编码已存在') from None
    if body.modules is not None: allowed_modules(db,tenant,body.modules)
    seed_tenant_defaults(db,tenant,body.modules)
    owner,password=create_tenant_account(db,tenant,body.admin_username,'租户管理员',preset_role_id(tenant.id,'tenant_admin'))
    audit(db,request,user,tenant.id,'platform.tenant.create')
    commit(db,'租户编码或管理员账号已存在')
    return dict(tenant=tenant_info(tenant),administrator=dict(id=owner.id,username=owner.username,temporary_password=password))

@legacy_router.post('',status_code=201)
def legacy_create_tenant(body:dict,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    if set(body)-{'code','name','admin_username','modules'}: raise HTTPException(422,detail='包含不支持的租户字段')
    try:
        value=CreateTenant(code=body.get('code') or 'tenant-'+uuid4().hex,name=body.get('name',''),admin_username=body.get('admin_username','tenant-admin'),modules=body.get('modules'))
    except ValueError: raise HTTPException(422,detail='租户字段无效') from None
    return create_tenant(value,request,db,user)['tenant']

@platform_router.patch('/tenants/{tenant_id}')
def update_tenant(tenant_id:str,body:PatchTenant,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    tenant=platform_tenant(db,tenant_id,lock=True); check_version(tenant,body.version)
    if 'name' in body.model_fields_set:
        if body.name is None: raise HTTPException(422,detail='名称不能为空')
        tenant.name=clean_name(body.name)
    if 'status' in body.model_fields_set:
        if body.status not in {'active','paused'}: raise HTTPException(422,detail='后台接入状态无效')
        tenant.status=body.status
        if body.status=='paused': db.execute(delete(IndiaConnection).where(IndiaConnection.tenant_id==tenant.id))
    tenant.version+=1; audit(db,request,user,tenant.id,'platform.tenant.update'); commit(db)
    return tenant_info(tenant)

@legacy_router.patch('/{tenant_id}')
def legacy_update_tenant(tenant_id:str,body:dict,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    tenant=platform_tenant(db,tenant_id)
    try:
        value=PatchTenant(version=body.get('version',tenant.version),**{k:v for k,v in body.items() if k!='version'})
    except ValueError: raise HTTPException(422,detail='租户字段无效') from None
    return update_tenant(tenant_id,value,request,db,user)

@platform_router.get('/tenants/{tenant_id}/modules')
def get_modules(tenant_id:str,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    tenant=platform_tenant(db,tenant_id)
    return dict(version=tenant.version,items=module_catalog(db,tenant))

@platform_router.put('/tenants/{tenant_id}/modules')
def set_modules(tenant_id:str,body:Modules,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    tenant=platform_tenant(db,tenant_id,lock=True); check_version(tenant,body.version)
    keys=allowed_modules(db,tenant,body.module_keys)
    for key in BY_KEY:
        row=db.get(TenantModule,(tenant.id,key))
        if row is None: db.add(TenantModule(tenant_id=tenant.id,module_key=key,enabled=key in keys))
        else: row.enabled=key in keys
    tenant.version+=1; audit(db,request,user,tenant.id,'platform.modules.update',payload={'module_keys':sorted(keys)}); commit(db)
    return dict(version=tenant.version,items=module_catalog(db,tenant))

@platform_router.post('/tenants/{tenant_id}/enter')
def enter_tenant(tenant_id:str,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    if request.state.principal.kind!='normal': raise HTTPException(403,detail='进入租户必须使用平台交互登录')
    tenant=platform_tenant(db,tenant_id)
    if tenant.status!='active': raise HTTPException(409,detail='此租户已暂停后台接入')
    token=ops_service.create_support_session(db,platform_user=user,target_tenant_id=tenant.id)
    audit(db,request,user,tenant.id,'platform.tenant.enter'); commit(db)
    return dict(token=token,context=build_context(db,SessionPrincipal(user,'platform',tenant.id,'support',user.auth_version)),entry_url='/admin/s/'+tenant.code,expires_at=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat())


def domain_out(row): return dict(id=row.id,hostname=row.hostname,kind=row.kind,status=row.status,verified_at=row.verified_at.isoformat() if row.verified_at else None)

@platform_router.get('/tenants/{tenant_id}/domains')
def get_domains(tenant_id:str,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    platform_tenant(db,tenant_id)
    return dict(items=[domain_out(row) for row in db.scalars(select(TenantDomain).where(TenantDomain.tenant_id==tenant_id).order_by(TenantDomain.hostname))])

@platform_router.post('/tenants/{tenant_id}/domains',status_code=201)
def add_domain(tenant_id:str,body:Domain,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    platform_tenant(db,tenant_id)
    try:
        hostname=body.hostname.strip().rstrip('.').encode('idna').decode().lower()
        if hostname in shared_hosts(): raise ValueError()
        if len(hostname.split('.'))<2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',part) for part in hostname.split('.')): raise ValueError()
        try: ipaddress.ip_address(hostname)
        except ValueError: pass
        else: raise ValueError()
    except (ValueError,UnicodeError): raise HTTPException(422,detail='请输入完整域名，不包含协议、路径或端口') from None
    row=TenantDomain(tenant_id=tenant_id,hostname=hostname,kind=body.kind,status='pending')
    db.add(row); audit(db,request,user,tenant_id,'platform.domain.create',payload={'hostname':hostname,'kind':body.kind}); commit(db,'域名已绑定，请先解除原绑定')
    return domain_out(row)

@platform_router.delete('/tenants/{tenant_id}/domains/{domain_id}')
def remove_domain(tenant_id:str,domain_id:str,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    row=db.scalar(select(TenantDomain).where(TenantDomain.id==domain_id,TenantDomain.tenant_id==tenant_id))
    if row is None: raise HTTPException(404,detail='域名绑定不存在')
    db.delete(row); audit(db,request,user,tenant_id,'platform.domain.delete',resource_id=domain_id); commit(db)
    return dict(deleted=True)

@platform_router.get('/tenants/{tenant_id}/administrators')
def get_administrators(tenant_id:str,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    from app.modules.tenancy.tenant_staff import staff_out
    platform_tenant(db,tenant_id)
    rows=db.scalars(select(AdminUser).join(TenantMember,TenantMember.user_id==AdminUser.id).join(TenantRole,TenantRole.id==TenantMember.role_id).where(AdminUser.account_scope=='tenant',AdminUser.tenant_id==tenant_id,TenantRole.kind=='tenant_admin').order_by(AdminUser.username)).all()
    return dict(items=[staff_out(db,row) for row in rows])

@platform_router.post('/tenants/{tenant_id}/administrators',status_code=201)
def add_administrator(tenant_id:str,body:Administrator,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    from app.modules.tenancy.tenant_staff import create_tenant_account,staff_out
    tenant=platform_tenant(db,tenant_id,lock=True)
    account,password=create_tenant_account(db,tenant,body.username,body.display_name,preset_role_id(tenant.id,'tenant_admin'))
    audit(db,request,user,tenant.id,'platform.administrator.create',resource_id=str(account.id)); commit(db,'租户内账号已存在')
    return dict(**staff_out(db,account),temporary_password=password)

@platform_router.post('/tenants/{tenant_id}/administrators/{user_id}/reset-password')
def reset_administrator(tenant_id:str,user_id:int,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    from app.modules.tenancy.tenant_staff import reset_tenant_password
    platform_tenant(db,tenant_id,lock=True)
    account=db.scalar(select(AdminUser).join(TenantMember,TenantMember.user_id==AdminUser.id).join(TenantRole,TenantRole.id==TenantMember.role_id).where(AdminUser.id==user_id,AdminUser.tenant_id==tenant_id,AdminUser.account_scope=='tenant',TenantRole.kind=='tenant_admin'))
    if account is None: raise HTTPException(404,detail='租户管理员不存在')
    password=reset_tenant_password(db,account)
    audit(db,request,user,tenant_id,'platform.administrator.password.reset',resource_id=str(user_id)); commit(db)
    return dict(username=account.username,temporary_password=password)


def audit_rows(db,tenant_id=None):
    query=select(AuditLog)
    if tenant_id is not None: query=query.where(AuditLog.tenant_id==tenant_id)
    return [dict(id=row.id,actor_id=row.actor_id,actor_scope=row.actor_scope,tenant_id=row.tenant_id,action=row.action,payload=row.payload,created_at=row.created_at.isoformat()) for row in db.scalars(query.order_by(AuditLog.created_at.desc(),AuditLog.id.desc()).limit(200))]

@platform_router.get('/audit')
def platform_audit(request:Request,tenant_id:str|None=None,db:Session=Depends(get_db),user:AdminUser=Depends(require_platform_admin)):
    return dict(items=audit_rows(db,tenant_id))

@tenant_router.get('/audit')
def tenant_audit(request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_tenant_admin)):
    return dict(items=audit_rows(db,request.state.tenant.id))


def role_out(db,tenant,role):
    return dict(id=role.id,name=role.name,permissions=preset_permissions(tenant,'tenant_admin',db) if role.kind=='tenant_admin' else sorted(role.permissions),version=role.version,builtin_key=role.builtin_key,kind=role.kind)

def role_rows(db,tenant): return [role_out(db,tenant,r) for r in db.scalars(select(TenantRole).where(TenantRole.tenant_id==tenant.id).order_by(TenantRole.created_at,TenantRole.id))]

@tenant_router.get('/roles')
def roles(request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_tenant_admin)):
    tenant=request.state.tenant
    return dict(roles=role_rows(db,tenant),catalog=permission_catalog(tenant,db))

@tenant_router.post('/roles',status_code=201)
def create_role(body:RoleCreate,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_tenant_admin)):
    tenant=request.state.tenant
    role=TenantRole(id='role-'+uuid4().hex,tenant_id=tenant.id,name=clean_name(body.name),kind='business',permissions=validate_permissions(tenant,body.permissions,db),version=1)
    db.add(role); audit(db,request,user,tenant.id,'tenant.role.create',resource_id=role.id); commit(db,'角色名称已存在')
    return role_out(db,tenant,role)

def locked_role(db,tenant,identifier):
    role=db.scalar(select(TenantRole).where(TenantRole.id==identifier,TenantRole.tenant_id==tenant.id).with_for_update().execution_options(populate_existing=True))
    if role is None: raise HTTPException(404,detail='当前租户中不存在此角色')
    return role

@tenant_router.patch('/roles/{role_id}')
def update_role(role_id:str,body:RolePatch,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_tenant_admin)):
    tenant=request.state.tenant; role=locked_role(db,tenant,role_id)
    if role.kind=='tenant_admin': raise HTTPException(409,detail='租户管理员是受保护的系统角色，其权限由已开通功能决定')
    if role.version!=body.version: raise HTTPException(409,detail='角色已被其他人修改，请刷新后重试')
    if 'name' in body.model_fields_set:
        if body.name is None: raise HTTPException(422,detail='名称不能为空')
        role.name=clean_name(body.name)
    if 'permissions' in body.model_fields_set:
        if body.permissions is None: raise HTTPException(422,detail='权限列表不能为null')
        role.permissions=validate_permissions(tenant,body.permissions,db,role.permissions)
    role.version+=1; audit(db,request,user,tenant.id,'tenant.role.update',resource_id=role.id); commit(db,'角色名称已存在')
    return role_out(db,tenant,role)

@tenant_router.delete('/roles/{role_id}')
def delete_role(role_id:str,version:int,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_tenant_admin)):
    tenant=request.state.tenant; role=locked_role(db,tenant,role_id)
    if role.kind=='tenant_admin' or role.builtin_key: raise HTTPException(409,detail='系统角色和预设角色不可删除')
    if role.version!=version: raise HTTPException(409,detail='角色已变化，请刷新后重试')
    if db.scalar(select(TenantMember.user_id).where(TenantMember.tenant_id==tenant.id,TenantMember.role_id==role.id).limit(1)) is not None:
        raise HTTPException(409,detail='请先为使用此角色的成员分配其他角色')
    db.delete(role); audit(db,request,user,tenant.id,'tenant.role.delete',resource_id=role.id); commit(db)
    return dict(deleted=True)


def assert_legacy_tenant(request,identifier):
    if request.state.tenant.id!=identifier: raise HTTPException(403,detail=dict(code='tenant_scope_mismatch',message='当前会话不能切换租户'))

@legacy_router.get('/{tenant_id}/roles')
def legacy_roles(tenant_id:str,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_tenant_admin)):
    assert_legacy_tenant(request,tenant_id); return roles(request,db,user)
@legacy_router.post('/{tenant_id}/roles',status_code=201)
def legacy_create_role(tenant_id:str,body:RoleCreate,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_tenant_admin)):
    assert_legacy_tenant(request,tenant_id); return create_role(body,request,db,user)
@legacy_router.patch('/{tenant_id}/roles/{role_id}')
def legacy_update_role(tenant_id:str,role_id:str,body:RolePatch,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_tenant_admin)):
    assert_legacy_tenant(request,tenant_id); return update_role(role_id,body,request,db,user)
@legacy_router.delete('/{tenant_id}/roles/{role_id}')
def legacy_delete_role(tenant_id:str,role_id:str,version:int,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_tenant_admin)):
    assert_legacy_tenant(request,tenant_id); return delete_role(role_id,version,request,db,user)

@legacy_router.get('/{tenant_id}/members')
def legacy_members(tenant_id:str,request:Request,db:Session=Depends(get_db),user:AdminUser=Depends(require_tenant_admin)):
    from app.modules.tenancy.tenant_staff import staff_out
    assert_legacy_tenant(request,tenant_id)
    rows=db.scalars(select(AdminUser).where(AdminUser.account_scope=='tenant',AdminUser.tenant_id==tenant_id).order_by(AdminUser.username)).all()
    items=[staff_out(db,row) for row in rows]
    return dict(members=[dict(user_id=item['id'],**{k:v for k,v in item.items() if k!='id'}) for item in items],staff=items,roles=role_rows(db,request.state.tenant))

@legacy_router.put('/{tenant_id}/members')
def legacy_replace_members(tenant_id:str,request:Request,user:AdminUser=Depends(require_tenant_admin)):
    assert_legacy_tenant(request,tenant_id)
    raise HTTPException(409,detail=dict(code='tenant_staff_api_required',message='员工账号已经归属租户，请通过员工管理逐个创建、变更角色或停用，不能替换全平台成员清单'))
