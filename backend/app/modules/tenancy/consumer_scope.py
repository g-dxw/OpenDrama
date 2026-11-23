"""Site ownership for consumer records, including administrative read models.

The trusted request resolver/session binder sets content_tenant_id. Background
jobs may opt out explicitly; normal HTTP callers never choose that value.
"""
from contextlib import contextmanager
from fastapi import HTTPException
from sqlalchemy import event, inspect, select, UniqueConstraint, ForeignKeyConstraint
from sqlalchemy.orm import Session, with_loader_criteria
from app.db import Base

SCOPED_TABLES = set('users third_party_identities verification_codes plans plan_versions subscriptions orders billing_settings coin_packs coin_pack_versions stripe_price_bindings coin_sources coin_allocations membership_grants commerce_settlements commerce_refunds stripe_subscription_lifecycle pancake_period_evidence wallet_accounts coin_ledger entitlements playback_events playback_progress daily_metrics reactions comments inbox_messages push_records hot_searches copy_entries marketing_stats testimonials store_links legal_pages reward_configs checkins ad_claims task_claims moderation_settings support_config support_tickets support_messages support_events attribution_pixels attribution_sources attribution_plans attribution_visits user_attribution attribution_events episodes'.split())
# Public codes and calendar caches belong to a site; provider identity belongs
# to the merchant and is handled separately by the commerce service.
SCOPED_UNIQUES = {
 'users': [('email',)], 'third_party_identities': [('provider','provider_user_id')],
 'plans':[('code',)], 'coin_packs':[('code',)], 'copy_entries':[('key',)],
 'store_links':[('platform',)], 'daily_metrics':[('business_day',)],
 'reward_configs':[('code','effective_on')],
}
MODELS = {}

def current_site(db):
    return db.info.get('content_tenant_id') or 'jjtv'

def require_owned(db, model, identifier, *, missing_ok=False):
    row = db.get(model, identifier)
    if row is None or (hasattr(row,'tenant_id') and row.tenant_id != current_site(db)):
        if missing_ok: return None
        raise HTTPException(404, detail='Resource not found in this site')
    return row

@contextmanager
def site_context(db, tenant_id):
    previous = db.info.get('content_tenant_id')
    db.info['content_tenant_id'] = tenant_id
    try: yield
    finally:
        if previous is None: db.info.pop('content_tenant_id',None)
        else: db.info['content_tenant_id']=previous

def install():
    if MODELS: return
    for mapper in Base.registry.mappers:
        table=mapper.local_table
        if table.name in SCOPED_TABLES:
            MODELS[table.name]=mapper.class_
    for name,model in MODELS.items():
        table=model.__table__
        pk=[c.name for c in table.primary_key.columns]
        if 'tenant_id' not in pk:
            table.append_constraint(UniqueConstraint('tenant_id',*pk,name=('uq_site_'+name+'_identity')[:63]))
        replacements=SCOPED_UNIQUES.get(name,[])
        for constraint in list(table.constraints):
            if isinstance(constraint,UniqueConstraint) and tuple(c.name for c in constraint.columns) in replacements:
                table.constraints.remove(constraint)
        for cols in replacements:
            table.append_constraint(UniqueConstraint('tenant_id',*cols,name=('uq_site_'+name+'_'+'_'.join(cols))[:63]))
    for name,model in MODELS.items():
        table=model.__table__
        # Retain existing FK names for compatibility, add ownership in a second
        # composite FK so raw SQL cannot join a viewer to another site's order.
        links=[]
        for fk in list(table.foreign_keys):
            target=fk.column.table
            if fk.parent.name=='tenant_id' or 'tenant_id' not in target.c or fk.column.name=='tenant_id': continue
            key=(fk.parent.name,target.name,fk.column.name)
            if key in links:continue
            links.append(key)
            target_pk=[c.name for c in target.primary_key.columns]
            if target.name not in MODELS and target.name!='dramas':continue
            # Composite references need a stable unique target key.
            if len(target_pk)==1 and fk.column.name==target_pk[0]:
                table.append_constraint(ForeignKeyConstraint(['tenant_id',fk.parent.name],[target.name+'.tenant_id',target.name+'.'+fk.column.name],name=('fk_site_'+name+'_'+fk.parent.name)[:63]))

@event.listens_for(Session,'do_orm_execute')
def scope_consumer_queries(state):
    tenant=state.session.info.get('content_tenant_id')
    if not tenant or state.session.info.get('consumer_tenant_bypass') or state.execution_options.get('include_all_tenants'):return
    if not (state.is_select or state.is_update or state.is_delete):return
    statement=state.statement
    for model in MODELS.values():
        statement=statement.options(with_loader_criteria(model,model.tenant_id==tenant,include_aliases=True))
    state.statement=statement

@event.listens_for(Session,'before_flush')
def own_consumer_writes(db,flush_context,instances):
    if db.info.get('consumer_tenant_bypass'):return
    tenant=db.info.get('content_tenant_id')
    with db.no_autoflush:
        for row in db.new | db.dirty | db.deleted:
            name=getattr(type(row),'__tablename__','')
            if name not in MODELS:continue
            value=getattr(row,'tenant_id',None)
            # ORM defaults are applied after before_flush: set the trusted scope
            # now so PostgreSQL's legacy jjtv server default cannot cross sites.
            if row in db.new and value is None:
                owners=set()
                for fk in row.__table__.foreign_keys:
                    if fk.parent.name=='tenant_id':continue
                    target=fk.column.table
                    if 'tenant_id' not in target.c or len(target.primary_key.columns)!=1:continue
                    ident=getattr(row,fk.parent.name,None)
                    if ident is None:continue
                    parent=next((m.class_ for m in Base.registry.mappers if m.local_table.name==target.name),None)
                    linked=db.get(parent,ident) if parent is not None else None
                    if linked is not None and linked.tenant_id:owners.add(linked.tenant_id)
                if len(owners)>1:raise HTTPException(404,detail='Related resources belong to different sites')
                value=tenant or (next(iter(owners)) if owners else 'jjtv');row.tenant_id=value
            if tenant and value!=tenant:raise HTTPException(404,detail='Resource not found in this site')
            if name=='reactions':
                from app.modules.content.models import Drama,Episode
                from app.modules.social.models import Comment
                parent={'drama':Drama,'episode':Episode,'comment':Comment}.get(row.target_type)
                if parent is not None:
                    linked=db.get(parent,row.target_id)
                    if (linked is not None and linked.tenant_id!=value) or (linked is None and tenant):raise HTTPException(404,detail='Reaction target not found in this site')
            if inspect(row).attrs.tenant_id.history.has_changes() and row not in db.new:
                raise HTTPException(409,detail='Site ownership is immutable')
            for fk in row.__table__.foreign_keys:
                key=fk.parent.name
                parent=MODELS.get(fk.column.table.name)
                if parent is None or key=='tenant_id' or len(parent.__mapper__.primary_key)!=1:continue
                ident=getattr(row,key,None)
                if ident is None:continue
                linked=db.get(parent,ident)
                if linked is None or linked.tenant_id!=value:raise HTTPException(404,detail='Related resource not found in this site')

install()


def site_timezone(db=None):
    from zoneinfo import ZoneInfo
    if db is not None:
        from app.modules.sites.models import SiteSettings
        value=db.scalar(select(SiteSettings.timezone).where(SiteSettings.tenant_id==current_site(db)))
        if value:return ZoneInfo(value)
    return ZoneInfo('America/Los_Angeles')
