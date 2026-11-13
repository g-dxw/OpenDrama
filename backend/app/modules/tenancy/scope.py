"""Native content ownership. Public requests always use the default tenant."""
from __future__ import annotations
from fastapi import HTTPException
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session, with_loader_criteria
from app.modules.common.models import AuditLog
from app.modules.content.models import Drama, Episode, Tag, HomeGroup, DramaTag, DramaHomeGroup, MediaUpload
from app.modules.tenancy.models import LegacyBusinessBinding

@event.listens_for(Session, "do_orm_execute")
def scope_queries(state):
    if state.session.info.get('tenant_audit_context') and (state.is_insert or state.is_update or state.is_delete):
        state.session.info['_tenant_audit_mutation']=True
    tenant = state.session.info.get("content_tenant_id")
    if not tenant or state.session.info.get("content_tenant_bypass") or state.execution_options.get("include_all_tenants"):
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    dramas = select(Drama.id).where(Drama.tenant_id == tenant)
    episodes = select(Episode.id).where(Episode.drama_id.in_(dramas))
    rules = [(Drama, Drama.tenant_id == tenant), (Tag, Tag.tenant_id == tenant),
             (HomeGroup, HomeGroup.tenant_id == tenant), (Episode, Episode.drama_id.in_(dramas)),
             (MediaUpload, MediaUpload.episode_id.in_(episodes)),
             (DramaTag, DramaTag.drama_id.in_(dramas)), (DramaHomeGroup, DramaHomeGroup.drama_id.in_(dramas))]
    statement = state.statement
    for model, condition in rules:
        statement = statement.options(with_loader_criteria(model, condition, include_aliases=True))
    state.statement = statement

@event.listens_for(Session, "before_flush")
def scope_writes(session, flush_context, instances):
    tenant = session.info.get("content_tenant_id")
    if not tenant or session.info.get("content_tenant_bypass"):
        return
    context=session.info.get('tenant_audit_context')
    if context:
        created=session.info.setdefault('_tenant_audit_created',[])
        seen={id(record) for record in created}
        for record in session.new:
            if not isinstance(record,AuditLog) and id(record) not in seen:
                created.append(record)
        if any(not isinstance(record,AuditLog) for record in session.new | session.dirty | session.deleted):
            session.info['_tenant_audit_mutation']=True
    def owns(model, identifier):
        if identifier is None:
            return False
        record = session.get(model, identifier)
        if record is None:
            return False
        if isinstance(record, (Drama, Tag, HomeGroup)):
            return record.tenant_id == tenant
        if isinstance(record, Episode):
            return owns(Drama, record.drama_id)
        return False
    def refuse():
        raise HTTPException(404, detail={"code": "tenant_resource_not_found", "message": "当前租户中不存在此内容"})
    for record in session.new | session.dirty | session.deleted:
        if isinstance(record, AuditLog) and record in session.new:
            record.payload = dict(record.payload or {})
            if context:
                # Support acts as the real platform identity in a tenant context;
                # its business target is never a global/platform-only event.
                record.tenant_id=context['tenant_id']
                record.payload['tenant_id']=context['tenant_id']
                if record.actor_type=='admin':
                    record.actor_id=context['actor_id']
                    record.actor_scope=context['actor_scope']
                elif record.actor_scope in (None,'legacy'):
                    record.actor_scope=record.actor_type
            elif record.actor_scope != "platform":
                if record.tenant_id is None: record.tenant_id=tenant
                record.payload.setdefault("tenant_id", record.tenant_id)
            if record.actor_scope in (None, "legacy"): record.actor_scope=session.info.get("actor_scope", "tenant")
        if isinstance(record, (Drama, Tag, HomeGroup)):
            if record in session.new and not record.tenant_id:
                record.tenant_id = tenant
            if record.tenant_id != tenant:
                refuse()
            if isinstance(record, Drama) and record.supplier_id is not None and not session.scalar(select(LegacyBusinessBinding.module_key).where(LegacyBusinessBinding.module_key=='suppliers',LegacyBusinessBinding.tenant_id==tenant)):
                raise HTTPException(400, detail="上剧方配置仅属于 JJTV 自营")
        elif isinstance(record, Episode):
            if not owns(Drama, record.drama_id): refuse()
        elif isinstance(record, MediaUpload):
            if not owns(Episode, record.episode_id): refuse()
        elif isinstance(record, DramaTag):
            if not owns(Drama, record.drama_id) or not owns(Tag, record.tag_id): refuse()
        elif isinstance(record, DramaHomeGroup):
            if not owns(Drama, record.drama_id) or not owns(HomeGroup, record.group_id): refuse()


@event.listens_for(Session, 'before_commit')
def audit_business_transaction(session):
    """Record committed tenant commands even when a legacy handler has no audit."""
    context=session.info.get('tenant_audit_context')
    if not context or session.in_nested_transaction() or session.info.get('_tenant_audit_added'):
        return
    changed=session.info.get('_tenant_audit_mutation') or any(
        not isinstance(record,AuditLog) for record in session.new | session.dirty | session.deleted)
    if context['method'] not in {'POST','PUT','PATCH','DELETE'} and not changed:
        return
    # Flush once to obtain generated IDs; the normal commit flush persists the
    # request audit in the same transaction. No body/query/headers are retained.
    session.flush()
    resources=[]
    for record in session.info.get('_tenant_audit_created',[]):
        state=inspect(record)
        if not state.persistent or not state.identity or len(state.identity)!=1 or type(state.identity[0]) is not int:
            continue
        resource=dict(type=getattr(type(record),'__tablename__',type(record).__name__.lower())[:32],id=state.identity[0])
        if resource not in resources:
            resources.append(resource)
    target=resources[0] if resources else dict(type='request',id=None)
    payload=dict(method=context['method'],path=context['path'],path_params=dict(context['path_params']))
    if resources: payload['created_resources']=resources[:20]
    session.add(AuditLog(actor_type='admin',actor_id=context['actor_id'],actor_scope=context['actor_scope'],
        tenant_id=context['tenant_id'],action='tenant.request.commit',target_type=target['type'],
        target_id=target['id'],payload=payload))
    session.info['_tenant_audit_added']=True


@event.listens_for(Session, 'after_transaction_end')
def clear_business_transaction_audit(session, transaction):
    if transaction.parent is None:
        for key in ('_tenant_audit_created','_tenant_audit_mutation','_tenant_audit_added'):
            session.info.pop(key,None)
