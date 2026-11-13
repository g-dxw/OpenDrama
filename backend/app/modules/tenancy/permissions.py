"""Tenant roles and the shared menu/action permission catalog.

A view permission exposes its menu. Every HTTP action is checked separately;
platform administration remains subject to the original platform identity.
"""
from __future__ import annotations
import hashlib
import re
from fastapi import HTTPException
from sqlalchemy import select
from app.modules.tenancy.models import DEFAULT_TENANT, INDIA_TENANT, TenantRole, TenantMember

CATALOG = []
def define(key, label, group, *, requires=(), menu_key=None):
    item = dict(key=key, label=label, group=group, requires=list(requires))
    if menu_key: item['menu_key'] = menu_key
    CATALOG.append(item)

define('dramas.view', '查看剧集（菜单）', '剧集管理', menu_key='/dramas')
for key, label in [('edit','编辑资料与分集'),('upload','上传视频、封面和字幕'),('publish','上下架与定时发布'),('delete','删除整部剧集'),('pricing','设置免费集数和价格')]:
    define('dramas.'+key, label, '剧集管理', requires=['dramas.view'])
define('episodes.delete', '删除单集', '剧集管理', requires=['dramas.view'])
define('reviews.view', '查看待审核（菜单）', '内容审核', menu_key='/reviews')
define('reviews.decide', '通过或驳回审核', '内容审核', requires=['reviews.view'])
for code, label, menu in [('tags','标签库','/tags'),('groups','首页分组','/groups')]:
    define(code+'.view', '查看'+label+'（菜单）', label, menu_key=menu)
    define(code+'.manage', '管理'+label, label, requires=[code+'.view'])
CONTENT_KEYS = {item['key'] for item in CATALOG}
GLOBAL_MODULES = [('dashboard','数据看板','/dashboard'),('users','用户管理','/users'),('home_ops','首页运营','/home-ops'),('rewards','金币与任务','/rewards'),('comments','评论审核','/comments'),('moderation','自动审核','/moderation'),('finance','财务订单','/finance'),('settlements','结算单','/settlements'),('plans','会员套餐','/plans'),('staff','账号管理','/staff'),('api_keys','API 密钥','/api-keys'),('suppliers','上剧方管理','/suppliers')]
for code, label, menu in GLOBAL_MODULES:
    define(code+'.view', '查看'+label+'（菜单）', label, menu_key=menu)
    if code not in ('dashboard','finance'):
        define(code+'.manage', '管理'+label, label, requires=[code+'.view'])
ALL_KEYS = {item['key'] for item in CATALOG}
STAFF_READ_KEYS = {item['key'] for item in CATALOG if item['key'].endswith('.view')} - CONTENT_KEYS - {'staff.view','api_keys.view','settlements.view'}
INDIA_KEYS = {'dramas.view','dramas.edit','dramas.upload','dramas.publish','dramas.pricing','reviews.view','reviews.decide'}
OPS_GLOBAL = {'dashboard.view','home_ops.view','home_ops.manage','rewards.view','rewards.manage','comments.view','comments.manage','moderation.view'}
FINANCE_GLOBAL = {'dashboard.view','users.view','finance.view','settlements.view','settlements.manage','plans.view'}

def capability_keys(tenant):
    if tenant.kind == 'india_legacy': return set(INDIA_KEYS)
    return set(ALL_KEYS if tenant.id == DEFAULT_TENANT else CONTENT_KEYS)

def permission_catalog(tenant):
    available = capability_keys(tenant)
    result = []
    for item in CATALOG:
        if item['key'] not in available: continue
        value = dict(item)
        if tenant.kind == 'india_legacy' and value.get('menu_key'): value['menu_key'] = '/india-content'
        result.append(value)
    return result

def preset_role_id(tenant_id, key):
    return 'role-' + hashlib.sha256((tenant_id+':'+key).encode()).hexdigest()[:40]

def preset_permissions(tenant, key):
    content = capability_keys(tenant) & CONTENT_KEYS
    if key == 'admin': return sorted(capability_keys(tenant))
    if key == 'finance': return sorted({'dramas.view'} | (FINANCE_GLOBAL if tenant.id == DEFAULT_TENANT else set()))
    return sorted((content - {'dramas.delete'}) | (OPS_GLOBAL if tenant.id == DEFAULT_TENANT else set()))

def seed_roles(db, tenant):
    for key, name in [('admin','内容管理员'),('ops','内容运营'),('finance','财务 / 只读')]:
        identifier = preset_role_id(tenant.id, key)
        if db.get(TenantRole, identifier) is None:
            db.add(TenantRole(id=identifier, tenant_id=tenant.id, builtin_key=key, name=name, permissions=preset_permissions(tenant, key), version=1))
    db.flush()

def validate_permissions(tenant, values):
    keys = set(values)
    if len(values) != len(keys) or not keys <= capability_keys(tenant):
        raise HTTPException(422, detail='包含重复、未知或当前租户不支持的权限')
    for item in CATALOG:
        if item['key'] in keys and not set(item['requires']) <= keys:
            raise HTTPException(422, detail='操作权限必须同时启用所属菜单的查看权限')
    return sorted(keys)

def effective_grants(db, user, tenant):
    if user.role == 'admin':
        keys = capability_keys(tenant)
        return dict(role_id=None, role_name='平台管理员', own_role='admin', permissions=sorted(keys), menu_keys=menus_for(tenant, keys), platform_admin=True)
    member = db.get(TenantMember, (tenant.id, user.id), populate_existing=True)
    if member is None: raise HTTPException(403, detail=dict(code='tenant_access_denied', message='没有该租户的操作权限'))
    role = db.get(TenantRole, member.role_id, populate_existing=True)
    if role is None or role.tenant_id != tenant.id:
        raise HTTPException(403, detail=dict(code='tenant_role_invalid', message='租户角色已失效，请联系平台管理员'))
    keys = set(role.permissions) & capability_keys(tenant)
    if tenant.id == DEFAULT_TENANT:
        # A tenant content role cannot turn an employee into a platform admin.
        ceiling = (STAFF_READ_KEYS | OPS_GLOBAL) if user.role == 'ops' else (STAFF_READ_KEYS | FINANCE_GLOBAL) if user.role == 'finance' else set()
        keys &= CONTENT_KEYS | ceiling
    return dict(role_id=role.id, role_name=role.name, own_role=role.builtin_key or 'custom', permissions=sorted(keys), menu_keys=menus_for(tenant, keys), platform_admin=False)

def menus_for(tenant, keys):
    if tenant.kind == 'india_legacy':
        return ['/india-content'] if keys & {'dramas.view','reviews.view'} else []
    return [item['menu_key'] for item in CATALOG if item['key'] in keys and item.get('menu_key')]

def deny(required):
    raise HTTPException(403, detail=dict(code='tenant_permission_denied', message='当前租户角色没有此操作权限', required=list(required)))

def require_permission(request, *keys, any_of=False):
    granted = set(getattr(request.state, 'tenant_permissions', ()))
    if not (bool(granted & set(keys)) if any_of else set(keys) <= granted): deny(keys)

# Exact registered content commands, rather than a permissive write-prefix rule.
# Every result is an OR-list; mixed-field commands add checks inside the handler.
READ_CONTENT = ('dramas.view','reviews.view')
CONTENT_RULES = [
 ('GET', r'dramas', ('dramas.view','reviews.view','groups.view','home_ops.view')),
 ('GET', r'dramas/[0-9]+(?:/(?:episodes|preview))?', READ_CONTENT),
 ('GET', r'episodes/[0-9]+/preview-url', READ_CONTENT),
 ('GET', r'dramas/[0-9]+/groups', ('dramas.view','groups.view')),
 ('GET', r'search', READ_CONTENT),
 ('POST', r'dramas', ('dramas.edit',)),
 ('PATCH', r'dramas/[0-9]+', ('dramas.edit','dramas.pricing','dramas.upload')),
 ('POST', r'dramas/[0-9]+/episodes(?:/bulk)?', ('dramas.edit',)),
 ('PATCH', r'episodes/[0-9]+', ('dramas.edit',)),
 ('DELETE', r'episodes/[0-9]+', ('episodes.delete',)),
 ('POST', r'episodes/[0-9]+/(?:upload-session|upload-headers|sync-status|upload-failed)', ('dramas.upload',)),
 ('POST', r'dev-tus/[A-Za-z0-9_-]+', ('dramas.upload',)),
 ('HEAD', r'dev-tus/[A-Za-z0-9_-]+', ('dramas.upload',)),
 ('PATCH', r'dev-tus/[A-Za-z0-9_-]+', ('dramas.upload',)),
 ('POST', r'dramas/[0-9]+/cover', ('dramas.upload',)),
 ('DELETE', r'dramas/[0-9]+/cover', ('dramas.upload',)),
 ('POST', r'episodes/[0-9]+/subtitle', ('dramas.upload',)),
 ('POST', r'episodes/[0-9]+/(?:publish|unpublish)', ('dramas.publish',)),
 ('POST', r'episodes/bulk-status', ('dramas.publish',)),
 ('POST', r'dramas/[0-9]+/status', ('dramas.edit','dramas.publish')),
 ('POST', r'dramas/[0-9]+/schedule', ('dramas.publish',)),
 ('POST', r'schedules/apply', ('dramas.publish',)),
 ('POST', r'dramas/[0-9]+/delete', ('dramas.delete',)),
 ('GET', r'reviews', ('reviews.view',)),
 ('POST', r'reviews/(?:batch|[0-9]+/decision)', ('reviews.decide',)),
 ('POST', r'reviews/[0-9]+/publish', ('dramas.publish',)),
 ('GET', r'tags', ('tags.view','dramas.view','reviews.view')),
 ('POST', r'tags', ('tags.manage',)),
 ('PATCH', r'tags/[0-9]+', ('tags.manage',)),
 ('DELETE', r'tags/[0-9]+', ('tags.manage',)),
 ('GET', r'groups(?:/[0-9]+/dramas)?', ('groups.view','home_ops.view')),
 ('POST', r'groups(?:/[0-9]+/dramas)?', ('groups.manage','home_ops.manage')),
 ('PATCH', r'groups/[0-9]+(?:/dramas/[0-9]+)?', ('groups.manage',)),
 ('DELETE', r'groups/[0-9]+/dramas/[0-9]+', ('groups.manage',)),
]
GLOBAL_PREFIXES = [
 (r'dashboard', 'dashboard'), (r'users(?:/.*)?', 'users'),
 (r'finance(?:/.*)?', 'finance'), (r'settlements(?:/.*)?', 'settlements'),
 (r'(?:plans|billing/settings)(?:/.*)?', 'plans'), (r'staff(?:/.*)?', 'staff'),
 (r'api-keys(?:/.*)?', 'api_keys'), (r'suppliers(?:/.*)?', 'suppliers'),
 (r'rewards(?:/.*)?', 'rewards'), (r'comments(?:/.*)?', 'comments'),
 (r'moderation(?:/.*)?', 'moderation'),
 (r'(?:tabs|banners|copies|hot-searches|marketing/stats|legal|stores|pushes)(?:/.*)?', 'home_ops'),
]

def authorize_native_request(request, tenant, grants):
    path = request.url.path.removeprefix('/admin/v1/').rstrip('/')
    request.state.tenant_permissions = grants['permissions']
    for method, pattern, required in CONTENT_RULES:
        if request.method == method and re.fullmatch(pattern, path):
            require_permission(request, *required, any_of=True)
            request.state.tenant_content_authorized = True
            return
    if tenant.id == DEFAULT_TENANT:
        for pattern, module in GLOBAL_PREFIXES:
            if re.fullmatch(pattern, path):
                view = request.method in ('GET','HEAD','OPTIONS') or (module == 'moderation' and path == 'moderation/test')
                if view and module == 'plans' and path in {'plans','billing/settings'}:
                    # User membership and order details use the public plan catalog.
                    require_permission(request, 'plans.view','users.view','finance.view', any_of=True)
                else:
                    require_permission(request, module + ('.view' if view else '.manage'))
                return
    deny(('registered_tenant_action',))
