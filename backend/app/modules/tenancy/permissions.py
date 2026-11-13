"""Tenant roles and the shared menu/action permission catalog.

A view permission exposes its menu. Every HTTP action is checked separately;
platform administration remains subject to the original platform identity.
"""
from __future__ import annotations
import hashlib
import re
from fastapi import HTTPException
from sqlalchemy import select
from app.modules.tenancy.models import DEFAULT_TENANT, INDIA_TENANT, TenantRole, TenantMember, TenantModule
from app.modules.tenancy import modules as business_modules
from app.modules.tenancy.modules import seed_modules, seed_legacy_bindings, SYSTEM_PERMISSIONS, SYSTEM_MENUS

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
for code,label,menu in [('login_settings','登录配置','/login-settings'),('stripe','支付通道','/stripe-accounts'),('coin_packs','金币商城','/coin-packs'),('attribution','投放归因','/attribution')]:
    define(code+'.view','查看'+label,label,menu_key=menu)
    define(code+'.manage','管理'+label,label,requires=[code+'.view'])
define('coins.view','查看金币消耗','金币消耗',menu_key='/coin-ledger')
define('coins.export','导出金币流水','金币消耗',requires=['coins.view'])
define('finance.refund','未消费订单全额退款','财务订单',requires=['finance.view'])
define('support.view','客服工作台','客服',menu_key='/support/workbench')
define('support.queue','工单队列','客服',requires=['support.view'],menu_key='/support/queue')
define('support.reports','服务质量','客服',requires=['support.view'],menu_key='/support/reports')
define('support.settings','工单设置','客服',requires=['support.view'],menu_key='/support/settings')
define('support.manage','回复、指派和处理工单','客服',requires=['support.view'])
ALL_KEYS = {item['key'] for item in CATALOG}
STAFF_READ_KEYS = {item['key'] for item in CATALOG if item['key'].endswith('.view')} - CONTENT_KEYS - {'staff.view','api_keys.view','settlements.view'}
INDIA_KEYS = {'dramas.view','dramas.edit','dramas.upload','dramas.publish','dramas.pricing','reviews.view','reviews.decide'}
OPS_GLOBAL = {'dashboard.view','home_ops.view','home_ops.manage','rewards.view','rewards.manage','comments.view','comments.manage','moderation.view'}
FINANCE_GLOBAL = {'dashboard.view','users.view','finance.view','settlements.view','settlements.manage','plans.view'}

def capability_keys(tenant, db=None, *, enabled_only=True):
    if db is None: return set(INDIA_KEYS if tenant.kind=='india_legacy' else CONTENT_KEYS)
    return business_modules.capability_keys(db,tenant,enabled_only=enabled_only)

def permission_catalog(tenant, db=None):
    supported,enabled=capability_keys(tenant,db,enabled_only=False),capability_keys(tenant,db)
    result=[]
    for item in CATALOG:
        if item['key'] not in supported or item['key'] in SYSTEM_PERMISSIONS: continue
        value=dict(item,enabled=item['key'] in enabled)
        if tenant.kind=='india_legacy' and value.get('menu_key'): value['menu_key']='/india-content'
        result.append(value)
    return result

def preset_role_id(tenant_id,key):
    return 'role-'+hashlib.sha256((tenant_id+':'+key).encode()).hexdigest()[:40]

def preset_permissions(tenant,key,db=None):
    available=capability_keys(tenant,db)
    if key=='tenant_admin': return sorted(available|SYSTEM_PERMISSIONS)
    # Newly adapted HomeOps is assigned deliberately by the tenant administrator;
    # preserve the original self-operation preset for the explicit legacy owner.
    if db is not None:
        from app.modules.tenancy.models import LegacyBusinessBinding
        binding=db.get(LegacyBusinessBinding,'home_ops')
        if binding is None or binding.tenant_id!=tenant.id:
            available-= {'home_ops.view','home_ops.manage'}
    if key=='admin': return sorted(available)
    if key=='finance': return sorted(available & ({'dramas.view'}|FINANCE_GLOBAL))
    return sorted(available & ((CONTENT_KEYS-{'dramas.delete'})|OPS_GLOBAL))

def seed_roles(db,tenant):
    if db.scalar(select(TenantModule.module_key).where(TenantModule.tenant_id==tenant.id).limit(1)) is None:
        seed_modules(db,tenant)
    for key,name in [('tenant_admin','租户管理员'),('admin','内容管理员'),('ops','内容运营'),('finance','财务 / 内容只读')]:
        identifier=preset_role_id(tenant.id,key)
        if db.get(TenantRole,identifier) is None:
            db.add(TenantRole(id=identifier,tenant_id=tenant.id,builtin_key=key,kind='tenant_admin' if key=='tenant_admin' else 'business',name=name,permissions=[] if key=='tenant_admin' else preset_permissions(tenant,key,db),version=1))
    db.flush()

def seed_tenant_defaults(db,tenant,module_keys=None):
    if not tenant.code: tenant.code=tenant.id
    db.flush(); seed_modules(db,tenant,module_keys); seed_roles(db,tenant)

def validate_permissions(tenant,values,db=None,existing=()):
    keys=set(values)
    supported,enabled=capability_keys(tenant,db,enabled_only=False),capability_keys(tenant,db)
    if len(values)!=len(keys) or not keys<=supported or not (keys-set(existing))<=enabled:
        raise HTTPException(422,detail='包含重复、未知、未开通或当前租户尚未支持的权限')
    for item in CATALOG:
        if item['key'] in keys and not set(item['requires'])<=keys:
            raise HTTPException(422,detail='操作权限必须同时启用所属菜单的查看权限')
    return sorted(keys)

def is_support_for(principal,tenant):
    return principal is not None and principal.account_scope=='platform' and principal.kind=='support' and principal.tenant_id==tenant.id

def effective_grants(db,user,tenant,principal=None):
    if is_support_for(principal,tenant):
        keys=capability_keys(tenant,db)|SYSTEM_PERMISSIONS
        return dict(role_id=None,role_name='平台进入（租户管理员）',own_role='tenant_admin',permissions=sorted(keys),menu_keys=menus_for(tenant,keys,True),is_tenant_admin=True,platform_admin=True)
    if user.account_scope!='tenant' or user.tenant_id!=tenant.id:
        raise HTTPException(403,detail=dict(code='account_tenant_mismatch',message='账号不属于当前租户'))
    member=db.get(TenantMember,(tenant.id,user.id),populate_existing=True)
    if member is None: raise HTTPException(403,detail=dict(code='tenant_access_denied',message='没有该租户的操作权限'))
    role=db.get(TenantRole,member.role_id,populate_existing=True)
    if role is None or role.tenant_id!=tenant.id:
        raise HTTPException(403,detail=dict(code='tenant_role_invalid',message='租户角色已失效，请联系租户管理员'))
    administrator=role.kind=='tenant_admin'
    keys=(capability_keys(tenant,db)|SYSTEM_PERMISSIONS) if administrator else (set(role.permissions)&capability_keys(tenant,db))
    return dict(role_id=role.id,role_name=role.name,own_role='tenant_admin' if administrator else role.builtin_key or 'custom',permissions=sorted(keys),menu_keys=menus_for(tenant,keys,administrator),is_tenant_admin=administrator,platform_admin=False)

def menus_for(tenant,keys,administrator=False):
    if tenant.kind=='india_legacy': menus=['/india-content'] if keys & {'dramas.view','reviews.view'} else []
    else: menus=[item['menu_key'] for item in CATALOG if item['key'] in keys and item.get('menu_key') and item['key'] not in SYSTEM_PERMISSIONS]
    return menus+(SYSTEM_MENUS if administrator else [])

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
 ('GET', r'episodes/[0-9]+/subtitles', READ_CONTENT),
 ('POST', r'episodes/[0-9]+/subtitles', ('dramas.upload',)),
 ('PATCH', r'episodes/[0-9]+/subtitles/default', ('dramas.upload',)),
 ('DELETE', r'episodes/[0-9]+/subtitles/[a-z0-9-]+', ('dramas.upload',)),
 ('PATCH', r'episodes/[0-9]+/pricing', ('dramas.pricing',)),
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
 (r'login(?:/.*)?','login_settings'), (r'stripe(?:/.*)?','stripe'),
 (r'coin-packs(?:/.*)?','coin_packs'), (r'coins(?:/.*)?','coins'),
 (r'attr(?:/.*)?','attribution'), (r'support(?:/.*)?','support'),
 (r'commerce/plans(?:/.*)?','plans'), (r'commerce(?:/.*)?','finance'), (r'billing/channels(?:/.*)?','stripe'),
 (r'dashboard', 'dashboard'), (r'users(?:/.*)?', 'users'),
 (r'finance(?:/.*)?', 'finance'), (r'settlements(?:/.*)?', 'settlements'),
 (r'(?:plans|billing/settings)(?:/.*)?', 'plans'), (r'staff(?:/.*)?', 'staff'),
 (r'api-keys(?:/.*)?', 'api_keys'), (r'suppliers(?:/.*)?', 'suppliers'),
 (r'rewards(?:/.*)?', 'rewards'), (r'comments(?:/.*)?', 'comments'),
 (r'moderation(?:/.*)?', 'moderation'),
 (r'(?:tabs|banners|copies|hot-searches|marketing/stats|legal|stores|pushes)(?:/.*)?', 'home_ops'),
]

def authorize_native_request(request,tenant,grants):
    path=request.url.path.removeprefix('/admin/v1/').rstrip('/')
    request.state.tenant_permissions=grants['permissions']
    # Selector permissions never reopen a function explicitly disabled by the platform.
    target=path.split('/',1)[0]
    modules=business_modules.enabled_modules(request.state.db_for_tenant,tenant)
    if target in {'tags','groups'} and target not in modules:
        deny((target+'.view',))
    if target in {'dramas','episodes','search','schedules','dev-tus'} and 'dramas' not in modules:
        # Review remains a supported consumer of read-only drama details.
        if request.method not in {'GET','HEAD'} or 'reviews.view' not in grants['permissions']:
            deny(('dramas.view',))
    if path=='coins/ladder' and request.method=='GET':
        require_permission(request,'coin_packs.view','coins.view',any_of=True)
        request.state.tenant_business_authorized=True
        return
    for method,pattern,required in CONTENT_RULES:
        if request.method==method and re.fullmatch(pattern,path):
            require_permission(request,*required,any_of=True)
            request.state.tenant_content_authorized=True
            request.state.tenant_business_authorized=True
            return
    for pattern,module in GLOBAL_PREFIXES:
        if re.fullmatch(pattern,path):
            if module=='home_ops' and not re.fullmatch(r'(?:tabs|banners)(?:/[0-9]+)?',path):
                from app.modules.tenancy.models import LegacyBusinessBinding
                binding=request.state.db_for_tenant.get(LegacyBusinessBinding,'home_ops')
                if binding is None or binding.tenant_id!=tenant.id:
                    deny(('adapted_home_ops_action',))
            if module not in {'staff','api_keys','roles'} and module not in business_modules.enabled_modules(request.state.db_for_tenant,tenant): deny((module+'.view',))
            read=request.method in ('GET','HEAD','OPTIONS')
            special = None
            if module=='finance' and path.endswith('/refund'): special='finance.refund'
            elif module=='coins': special='coins.export' if path.endswith('/export') else 'coins.view' if read else 'coin_packs.manage'
            elif module=='support': special='support.settings' if path.startswith('support/settings') else 'support.reports' if path.startswith('support/reports') else 'support.view' if read else 'support.manage'
            if special:
                require_permission(request,special)
                request.state.tenant_business_authorized=True
                return
            view=request.method in ('GET','HEAD','OPTIONS') or (module=='moderation' and path=='moderation/test')
            if view and module=='plans' and path in {'plans','billing/settings'}: require_permission(request,'plans.view','users.view','finance.view',any_of=True)
            else: require_permission(request,module+('.view' if view else '.manage'))
            request.state.tenant_business_authorized=True
            return
    deny(('registered_tenant_action',))
