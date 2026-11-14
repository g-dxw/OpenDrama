"""Public session context; no password, upstream credential or cross-tenant directory."""
from fastapi import HTTPException
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.permissions import effective_grants
from app.modules.tenancy.modules import enabled_modules

def user_info(user):
    return dict(id=user.id,username=user.username,display_name=user.display_name,role=user.role,account_scope=user.account_scope,tenant_id=user.tenant_id,must_change_password=user.must_change_password)

def tenant_info(tenant):
    from sqlalchemy.orm import object_session
    from app.modules.sites.models import SiteSettings
    db=object_session(tenant)
    settings=db.get(SiteSettings,tenant.id) if db else None
    config={key:getattr(settings,key) for key in ('target_markets','default_locale','locales','currency','timezone','web_url','h5_url','features')} if settings else {}
    return dict(id=tenant.id,code=tenant.code,name=tenant.name,kind=tenant.kind,status=tenant.status,version=tenant.version,**config)

def build_context(db,principal):
    user=principal.user
    result=dict(user=user_info(user),account_scope=principal.account_scope,session_kind=principal.kind,tenant=None,is_platform_admin=principal.account_scope=='platform',is_tenant_admin=False,permissions=[],menu_keys=[],role_name='平台管理员',enabled_modules=[])
    if principal.account_scope=='platform' and principal.kind!='support': return result
    tenant=db.get(Tenant,principal.tenant_id,populate_existing=True)
    if tenant is None: raise HTTPException(403,detail='租户上下文无效')
    result['tenant']=tenant_info(tenant); result['enabled_modules']=sorted(enabled_modules(db,tenant))
    if user.role=='partner' and principal.account_scope=='tenant' and user.tenant_id==tenant.id:
        from app.modules.ops.models import Supplier
        supplier=db.get(Supplier,user.supplier_id)
        if supplier is not None: result['user']['supplier']=dict(id=supplier.id,name=supplier.name,status=supplier.status,revenue_share_bps=supplier.revenue_share_bps)
        modules=set(result['enabled_modules']); menus=[]
        if 'dramas' in modules: menus+=['/partner/dramas','/partner/stats','/partner/api-keys']
        if 'settlements' in modules: menus.append('/partner/settlements')
        result.update(menu_keys=menus,role_name='上剧方')
        return result
    grants=effective_grants(db,user,tenant,principal)
    if tenant.kind=='native' and 'dramas' in result['enabled_modules'] and 'dramas.view' in grants['permissions']:
        grants['menu_keys']=list(dict.fromkeys([*grants['menu_keys'],'/library']))
    result.update(permissions=grants['permissions'],menu_keys=grants['menu_keys'],is_tenant_admin=grants['is_tenant_admin'],role_name=grants['role_name'],role_id=grants['role_id'])
    return result
