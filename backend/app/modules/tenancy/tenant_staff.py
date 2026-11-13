"""Tenant-owned employee lifecycle, with compatibility routes for existing UI."""
from __future__ import annotations
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db import get_db
from app.modules.common.models import AuditLog
from app.modules.ops import service as ops_service
from app.modules.ops.models import AdminUser, Supplier
from app.modules.ops.router import require_tenant_admin
from app.modules.tenancy.models import LegacyBusinessBinding, Tenant, TenantMember, TenantModule, TenantRole
from app.modules.tenancy.permissions import preset_role_id

tenant_router = APIRouter(prefix='/tenant/v1', tags=['tenant-staff'])
legacy_staff_router = APIRouter(prefix='/admin/v1', tags=['tenant-staff-compatibility'])

class StaffCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=3,max_length=64)
    display_name: str = Field(default='',max_length=64)
    role_id: str = Field(min_length=1,max_length=64)
    password: str | None = Field(default=None,min_length=8,max_length=128)

class LegacyCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=3,max_length=64)
    display_name: str = Field(default='',max_length=64)
    role: str = 'ops'
    role_id: str | None = Field(default=None,max_length=64)
    password: str | None = Field(default=None,min_length=8,max_length=128)
    supplier_id: int | None = None

class StaffPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str | None = Field(default=None,max_length=64)
    role_id: str | None = Field(default=None,max_length=64)
    status: str | None = Field(default=None,pattern="^(active|disabled)$")

class LegacyPatch(StaffPatch):
    role: str | None = None

class StatusInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: str = Field(pattern="^(active|disabled)$")
    reason: str | None = Field(default=None,max_length=200)

class LegacyPassword(BaseModel):
    model_config = ConfigDict(extra='forbid')
    new_password: str = Field(min_length=8,max_length=128)


def _password(value: str, username: str) -> str:
    if len(value) < 8 or len(value) > 128: raise HTTPException(422,detail='密码长度必须为8至128位')
    if value != value.strip(): raise HTTPException(422,detail='密码首尾不能是空格')
    if value.lower() == username.lower(): raise HTTPException(422,detail='密码不能与账号相同')
    return value


def _role(db, tenant_id, identifier):
    role = db.scalar(select(TenantRole).where(TenantRole.id == identifier,TenantRole.tenant_id == tenant_id))
    if role is None: raise HTTPException(404,detail='当前租户不存在此角色')
    return role


def _member(db, user):
    return db.get(TenantMember,(user.tenant_id,user.id),populate_existing=True)


def create_tenant_account(db, tenant, username, display_name, role_id, password=None, must_change=True):
    username = username.strip()
    if len(username) < 3 or len(username) > 64: raise HTTPException(422,detail='账号长度必须为3至64位')
    role = _role(db,tenant.id,role_id)
    temporary = _password(password or secrets.token_urlsafe(18),username)
    user = AdminUser(username=username,password_hash=ops_service.hash_password(temporary),display_name=display_name.strip(),
        role='finance' if role.builtin_key == 'finance' else 'ops',status='active',account_scope='tenant',tenant_id=tenant.id,
        auth_version=1,must_change_password=must_change)
    db.add(user)
    try: db.flush()
    except IntegrityError:
        db.rollback(); raise HTTPException(409,detail='此租户中账号已存在') from None
    legacy = 'admin' if role.kind == 'tenant_admin' or role.builtin_key == 'admin' else 'finance' if role.builtin_key == 'finance' else 'ops'
    db.add(TenantMember(tenant_id=tenant.id,user_id=user.id,role=legacy,role_id=role.id))
    db.flush()
    return user,temporary


def staff_out(db, user):
    member = _member(db,user) if user.account_scope == 'tenant' and user.role != 'partner' else None
    role = db.get(TenantRole,member.role_id) if member else None
    return dict(id=user.id,username=user.username,display_name=user.display_name,role=user.role,status=user.status,
        account_scope=user.account_scope,tenant_id=user.tenant_id,role_id=role.id if role else None,role_name=role.name if role else '上剧方',
        is_tenant_admin=bool(role and role.kind == 'tenant_admin'),is_partner=user.role == 'partner',
        must_change_password=user.must_change_password,disable_reason=user.disable_reason,
        created_at=user.created_at.isoformat() if user.created_at else None,last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
        supplier_id=user.supplier_id)


def reset_tenant_password(db, user):
    if user.account_scope != 'tenant': raise HTTPException(403,detail='只能重置租户账号')
    temporary = secrets.token_urlsafe(18)
    try: ops_service.revoke_user_sessions(user.id)
    except ops_service.SessionStoreUnavailable as exc: raise HTTPException(503,detail='会话服务暂时不可用，密码未重置') from exc
    user.password_hash = ops_service.hash_password(temporary)
    user.auth_version += 1
    user.must_change_password = True
    return temporary


def _context(db, request, *, lock=False):
    principal = getattr(request.state,'principal',None)
    if principal is None or not principal.tenant_id: raise HTTPException(403,detail='需要租户上下文')
    stmt = select(Tenant).where(Tenant.id == principal.tenant_id)
    if lock: stmt = stmt.with_for_update().execution_options(populate_existing=True)
    tenant = db.scalar(stmt)
    if tenant is None: raise HTTPException(404,detail='租户不存在')
    return tenant


def _owned_user(db,tenant_id,user_id):
    user = db.scalar(select(AdminUser).where(AdminUser.id == user_id,AdminUser.account_scope == 'tenant',AdminUser.tenant_id == tenant_id).execution_options(populate_existing=True))
    if user is None: raise HTTPException(404,detail='当前租户不存在此员工')
    return user


def _role_out(role):
    return dict(id=role.id,name=role.name,kind=role.kind,builtin_key=role.builtin_key,permissions=role.permissions,version=role.version)


def _audit(db,request,actor,tenant_id,action,target,payload):
    principal = request.state.principal
    db.add(AuditLog(actor_type='admin',actor_id=actor.id,actor_scope=principal.account_scope,tenant_id=tenant_id,
        action=action,target_type='admin_user',target_id=target,payload=payload))


def _commit(db):
    try: db.commit()
    except IntegrityError:
        db.rollback(); raise HTTPException(409,detail='账号或角色已发生变化，请刷新后重试') from None


def _remaining_admins(db,tenant_id,except_id):
    return db.scalar(select(func.count(AdminUser.id)).join(TenantMember,(TenantMember.user_id == AdminUser.id) & (TenantMember.tenant_id == tenant_id))
        .join(TenantRole,(TenantRole.id == TenantMember.role_id) & (TenantRole.tenant_id == tenant_id))
        .where(AdminUser.account_scope == 'tenant',AdminUser.tenant_id == tenant_id,AdminUser.status == 'active',AdminUser.id != except_id,TenantRole.kind == 'tenant_admin')) or 0


@tenant_router.get('/staff')
@legacy_staff_router.get('/staff')
def list_staff(request: Request,q: str | None = None,role_id: str | None = None,role: str | None = None,status: str | None = None,
               db: Session = Depends(get_db),actor: AdminUser = Depends(require_tenant_admin)):
    tenant = _context(db,request)
    stmt = select(AdminUser).where(AdminUser.account_scope == 'tenant',AdminUser.tenant_id == tenant.id)
    if q and q.strip(): stmt = stmt.where(or_(AdminUser.username.ilike('%'+q.strip()+'%'),AdminUser.display_name.ilike('%'+q.strip()+'%')))
    if status:
        if status not in {'active','disabled'}: raise HTTPException(422,detail='账号状态无效')
        stmt = stmt.where(AdminUser.status == status)
    if role_id: stmt = stmt.where(AdminUser.id.in_(select(TenantMember.user_id).where(TenantMember.tenant_id == tenant.id,TenantMember.role_id == role_id)))
    elif role == 'admin':
        stmt = stmt.where(AdminUser.id.in_(select(TenantMember.user_id).join(TenantRole,TenantRole.id == TenantMember.role_id).where(TenantMember.tenant_id == tenant.id,TenantRole.kind == 'tenant_admin')))
    elif role: stmt = stmt.where(AdminUser.role == role)
    users = db.scalars(stmt.order_by(AdminUser.id).limit(200)).all()
    roles = db.scalars(select(TenantRole).where(TenantRole.tenant_id == tenant.id).order_by(TenantRole.created_at,TenantRole.id)).all()
    return dict(items=[staff_out(db,user) for user in users],roles=[_role_out(role) for role in roles])


@tenant_router.post('/staff',status_code=201)
def create_staff(body: StaffCreate,request: Request,db: Session = Depends(get_db),actor: AdminUser = Depends(require_tenant_admin)):
    tenant = _context(db,request,lock=True)
    user,password = create_tenant_account(db,tenant,body.username,body.display_name,body.role_id,body.password)
    _audit(db,request,actor,tenant.id,'tenant.staff.create',user.id,dict(username=user.username,role_id=body.role_id))
    _commit(db)
    return {**staff_out(db,user),'temporary_password':password}


@legacy_staff_router.post('/staff',status_code=201)
def legacy_create_staff(body: LegacyCreate,request: Request,db: Session = Depends(get_db),actor: AdminUser = Depends(require_tenant_admin)):
    tenant = _context(db,request,lock=True)
    if body.role == 'partner':
        binding = db.get(LegacyBusinessBinding,'suppliers')
        supplier = db.get(Supplier,body.supplier_id) if body.supplier_id else None
        module = db.get(TenantModule,(tenant.id,'suppliers'))
        if body.role_id or binding is None or binding.tenant_id != tenant.id or module is None or not module.enabled or supplier is None or supplier.status != 'active':
            raise HTTPException(422,detail='上剧方账号必须绑定本租户的有效供应商')
        username = body.username.strip()
        if len(username) < 3: raise HTTPException(422,detail='账号长度必须为3至64位')
        password = _password(body.password or secrets.token_urlsafe(18),username)
        user = AdminUser(username=username,password_hash=ops_service.hash_password(password),display_name=body.display_name.strip(),
            role='partner',supplier_id=supplier.id,status='active',account_scope='tenant',tenant_id=tenant.id,auth_version=1,must_change_password=True)
        db.add(user)
        try: db.flush()
        except IntegrityError:
            db.rollback(); raise HTTPException(409,detail='账号或供应商已绑定') from None
    else:
        if body.supplier_id is not None: raise HTTPException(422,detail='普通员工不能绑定供应商')
        if body.role not in {'admin','ops','finance'}: raise HTTPException(422,detail='员工角色无效')
        role_id = body.role_id or preset_role_id(tenant.id,'tenant_admin' if body.role == 'admin' else body.role)
        user,password = create_tenant_account(db,tenant,body.username,body.display_name,role_id,body.password)
    _audit(db,request,actor,tenant.id,'tenant.staff.create',user.id,dict(username=user.username,role_id=body.role_id,legacy_role=body.role))
    _commit(db)
    return {**staff_out(db,user),'temporary_password':password}


def _patch_user(db,request,actor,staff_id,body,disable_reason=None):
    tenant = _context(db,request,lock=True)
    user = _owned_user(db,tenant.id,staff_id)
    before = staff_out(db,user)
    member = _member(db,user) if user.role != 'partner' else None
    old_role = db.get(TenantRole,member.role_id) if member else None
    new_role = old_role
    role_id = body.role_id
    if 'role_id' in body.model_fields_set and role_id is None: raise HTTPException(422,detail='角色不能为空')
    if 'status' in body.model_fields_set and body.status is None: raise HTTPException(422,detail='账号状态不能为空')
    legacy = getattr(body,'role',None)
    if legacy is not None:
        if legacy not in {'admin','ops','finance','partner'}: raise HTTPException(422,detail='员工角色无效')
        if user.role == 'partner' or legacy == 'partner':
            if legacy != user.role or role_id: raise HTTPException(400,detail='上剧方账号的角色不可修改，请改绑供应商')
        elif role_id is None: role_id = preset_role_id(tenant.id,'tenant_admin' if legacy == 'admin' else legacy)
    if role_id is not None:
        if user.role == 'partner': raise HTTPException(400,detail='上剧方账号的角色不可修改')
        new_role = _role(db,tenant.id,role_id)
        if member is None: raise HTTPException(409,detail='员工角色绑定缺失，请刷新后重试')
        if member.role_id != new_role.id and user.id == actor.id and request.url.path.startswith('/admin/'):
            raise HTTPException(400,detail='不能修改自己的角色')
    new_status = body.status or user.status
    if new_status not in {'active','disabled'}: raise HTTPException(422,detail='账号状态无效')
    if new_status == 'disabled' and user.id == actor.id and request.url.path.startswith('/admin/'):
        raise HTTPException(400,detail='不能禁用自己')
    if old_role and old_role.kind == 'tenant_admin' and user.status == 'active' and (new_status != 'active' or new_role.kind != 'tenant_admin') and _remaining_admins(db,tenant.id,user.id) == 0:
        raise HTTPException(409,detail='至少要保留一个启用状态的租户管理员')
    killed = 0
    if new_status == 'disabled' and user.status != 'disabled':
        try: killed = ops_service.revoke_user_sessions(user.id)
        except ops_service.SessionStoreUnavailable as exc: raise HTTPException(503,detail='会话服务暂时不可用，账号未停用') from exc
        user.auth_version += 1
    if body.display_name is not None: user.display_name = body.display_name.strip()
    if member is not None and new_role is not None:
        member.role_id = new_role.id
        member.role = 'admin' if new_role.kind == 'tenant_admin' or new_role.builtin_key == 'admin' else 'finance' if new_role.builtin_key == 'finance' else 'ops'
        user.role = 'finance' if new_role.builtin_key == 'finance' else 'ops'
    user.status = new_status
    if new_status == 'active': user.disable_reason = None
    elif disable_reason is not None: user.disable_reason = disable_reason.strip() or None
    elif before['status'] != 'disabled': user.disable_reason = None
    _audit(db,request,actor,tenant.id,'tenant.staff.update',user.id,dict(before_role_id=before['role_id'],after_role_id=new_role.id if new_role else None,before_status=before['status'],after_status=user.status,disable_reason=user.disable_reason,revoked_sessions=killed))
    _commit(db)
    return before,staff_out(db,user),killed


@tenant_router.patch('/staff/{staff_id}')
def patch_staff(staff_id: int,body: StaffPatch,request: Request,db: Session = Depends(get_db),actor: AdminUser = Depends(require_tenant_admin)):
    return _patch_user(db,request,actor,staff_id,body)[1]


@legacy_staff_router.patch('/staff/{staff_id}')
def legacy_patch_staff(staff_id: int,body: LegacyPatch,request: Request,db: Session = Depends(get_db),actor: AdminUser = Depends(require_tenant_admin)):
    before,after,_ = _patch_user(db,request,actor,staff_id,body)
    return dict(before=before,after=after)


@legacy_staff_router.post('/staff/{staff_id}/status')
def legacy_staff_status(staff_id: int,body: StatusInput,request: Request,db: Session = Depends(get_db),actor: AdminUser = Depends(require_tenant_admin)):
    before,after,killed = _patch_user(db,request,actor,staff_id,StaffPatch(status=body.status),disable_reason=body.reason)
    return dict(id=after['id'],status=after['status'],disable_reason=after['disable_reason'],revoked_sessions=killed)


@tenant_router.post('/staff/{staff_id}/reset-password')
def reset_staff_password(staff_id: int,request: Request,db: Session = Depends(get_db),actor: AdminUser = Depends(require_tenant_admin)):
    tenant = _context(db,request,lock=True)
    user = _owned_user(db,tenant.id,staff_id)
    if user.id == actor.id: raise HTTPException(400,detail='请从修改我的密码入口修改自己的密码')
    password = reset_tenant_password(db,user)
    _audit(db,request,actor,tenant.id,'tenant.staff.password_reset',user.id,dict(auth_version=user.auth_version))
    _commit(db)
    return dict(username=user.username,temporary_password=password)


@legacy_staff_router.post('/staff/{staff_id}/password',status_code=204)
def legacy_reset_staff_password(staff_id: int,body: LegacyPassword,request: Request,db: Session = Depends(get_db),actor: AdminUser = Depends(require_tenant_admin)):
    tenant = _context(db,request,lock=True)
    user = _owned_user(db,tenant.id,staff_id)
    if user.id == actor.id: raise HTTPException(400,detail='请从修改我的密码入口修改自己的密码')
    _password(body.new_password,user.username)
    reset_tenant_password(db,user)
    user.password_hash = ops_service.hash_password(body.new_password)
    _audit(db,request,actor,tenant.id,'tenant.staff.password_reset',user.id,dict(auth_version=user.auth_version))
    _commit(db)
    return Response(status_code=204)
