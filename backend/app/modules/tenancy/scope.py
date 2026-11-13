"""Native content ownership. Public requests always use the default tenant."""
from __future__ import annotations
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.orm import Session, with_loader_criteria
from app.modules.common.models import AuditLog
from app.modules.content.models import Drama, Episode, Tag, HomeGroup, DramaTag, DramaHomeGroup, MediaUpload
from app.modules.tenancy.models import LegacyBusinessBinding

@event.listens_for(Session, "do_orm_execute")
def scope_queries(state):
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
            if record.actor_scope != "platform":
                record.payload.setdefault("tenant_id", tenant)
                if record.tenant_id is None: record.tenant_id=tenant
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
