"""Business module availability and per-tenant opening. IAM is a separate system capability."""
from __future__ import annotations
from sqlalchemy import select
from app.modules.tenancy.models import TenantModule, LegacyBusinessBinding

MODULES = [
 dict(key='dramas', label='剧集管理', menu_key='/dramas', permissions=['dramas.view','dramas.edit','dramas.upload','dramas.publish','dramas.delete','dramas.pricing','episodes.delete'], backend='native'),
 dict(key='reviews', label='内容审核', menu_key='/reviews', permissions=['reviews.view','reviews.decide'], backend='native'),
 dict(key='tags', label='标签库', menu_key='/tags', permissions=['tags.view','tags.manage'], backend='native'),
 dict(key='groups', label='首页分组', menu_key='/groups', permissions=['groups.view','groups.manage'], backend='native'),
 dict(key='home_ops', label='首页运营', menu_key='/home-ops', permissions=['home_ops.view','home_ops.manage'], backend='native'),
]
for key, label, menu in [('dashboard','数据看板','/dashboard'),('users','用户管理','/users'),('rewards','金币与任务','/rewards'),('comments','评论审核','/comments'),('moderation','自动审核','/moderation'),('finance','财务订单','/finance'),('settlements','结算单','/settlements'),('plans','会员套餐','/plans'),('suppliers','上剧方管理','/suppliers')]:
    MODULES.append(dict(key=key,label=label,menu_key=menu,permissions=[key+'.view']+([] if key in {'dashboard','finance'} else [key+'.manage']),backend='legacy_jjtv'))
for key,label,menu,permissions in [
 ('login_settings','登录配置','/login-settings',['login_settings.view','login_settings.manage']),
 ('stripe','支付通道','/stripe-accounts',['stripe.view','stripe.manage']),
 ('coin_packs','金币商城','/coin-packs',['coin_packs.view','coin_packs.manage']),
 ('coins','金币消耗','/coin-ledger',['coins.view','coins.export']),
 ('attribution','投放归因','/attribution',['attribution.view','attribution.manage']),
 ('support','客服工单','/support/workbench',['support.view','support.queue','support.manage','support.reports','support.settings']),
]: MODULES.append(dict(key=key,label=label,menu_key=menu,permissions=permissions,backend='legacy_jjtv'))
next(item for item in MODULES if item['key']=='finance')['permissions'].append('finance.refund')
NATIVE_BUSINESS = {'dashboard','users','rewards','comments','finance','plans','login_settings','coin_packs','coins','stripe'}
for item in MODULES:
    if item['key'] in NATIVE_BUSINESS:item['backend']='native'
BY_KEY = {item['key']:item for item in MODULES}
LEGACY_MODULE_KEYS = tuple(item['key'] for item in MODULES if item['backend']=='legacy_jjtv')
# Native home_ops opens only channels/banners; its older global config routes
# still belong to the explicitly registered legacy owner.
LEGACY_BINDING_KEYS = LEGACY_MODULE_KEYS + ('home_ops',)
INDIA_KEYS = {'dramas.view','dramas.edit','dramas.upload','dramas.publish','dramas.pricing','reviews.view','reviews.decide'}
SYSTEM_PERMISSIONS = {'staff.view','staff.manage','roles.view','roles.manage','api_keys.view','api_keys.manage'}
SYSTEM_MENUS = ['/staff','/roles','/api-keys']

def seed_legacy_bindings(db, tenant_id='jjtv'):
    for key in LEGACY_BINDING_KEYS:
        if db.get(LegacyBusinessBinding, key) is None:
            db.add(LegacyBusinessBinding(module_key=key, tenant_id=tenant_id))
    db.flush()

def supported_keys(db, tenant):
    if tenant.kind == 'india_legacy': return {'dramas','reviews'}
    result = {'dramas','reviews','tags','groups','home_ops'} | NATIVE_BUSINESS
    result |= set(db.scalars(select(LegacyBusinessBinding.module_key).where(LegacyBusinessBinding.tenant_id==tenant.id))) & set(LEGACY_MODULE_KEYS)
    return result

def seed_modules(db, tenant, module_keys=None):
    supported = supported_keys(db, tenant)
    enabled = supported if module_keys is None else set(module_keys)
    for key in BY_KEY:
        if db.get(TenantModule, (tenant.id,key)) is None:
            db.add(TenantModule(tenant_id=tenant.id,module_key=key,enabled=key in enabled and key in supported))
    db.flush()

def enabled_modules(db, tenant):
    return set(db.scalars(select(TenantModule.module_key).where(TenantModule.tenant_id==tenant.id,TenantModule.enabled.is_(True)))) & supported_keys(db,tenant)

def capability_keys(db, tenant, *, enabled_only=True):
    modules = enabled_modules(db,tenant) if enabled_only else supported_keys(db,tenant)
    keys = {p for key in modules for p in BY_KEY[key]['permissions']}
    return keys & INDIA_KEYS if tenant.kind=='india_legacy' else keys

def module_catalog(db, tenant):
    supported, enabled = supported_keys(db,tenant), enabled_modules(db,tenant)
    return [dict(key=item['key'],label=item['label'],menu_key='/india-content' if tenant.kind=='india_legacy' and item['key'] in {'dramas','reviews'} else item['menu_key'], enabled=item['key'] in enabled,supported=item['key'] in supported,backend='india_legacy' if tenant.kind=='india_legacy' and item['key'] in supported else item['backend'],reason=None if item['key'] in supported else '该模块尚未完成此类租户的数据隔离适配') for item in MODULES]
