"""Platform content desk and explicitly authorized tenant import routes."""
from __future__ import annotations

from datetime import datetime
import os
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.common import bunny
from app.modules.content.models import Drama
from app.modules.library import service
from app.modules.library.models import DistributionGrant, LibraryEpisode, LibraryRevision, LibraryTitle, MediaAsset
from app.modules.ops.models import AdminUser
from app.modules.ops.router import require_admin, require_platform_admin
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.permissions import require_permission

platform_router = APIRouter(prefix='/platform/v1/library', tags=['company-content-library'])
tenant_router = APIRouter(prefix='/tenant/v1/library', tags=['tenant-content-library'])


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TitleCreate(Strict):
    title: str = Field(min_length=1, max_length=200)
    synopsis: str = Field(default='', max_length=20000)
    cover_url: str = Field(default='', max_length=500)
    original_language: str = Field(default='en', max_length=16)
    rights_note: str = Field(default='', max_length=20000)

    @field_validator('title')
    @classmethod
    def meaningful_title(cls, value):
        if not value.strip():
            raise ValueError('名称不能为空')
        return value.strip()


class TitlePatch(Strict):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    synopsis: str | None = Field(default=None, max_length=20000)
    cover_url: str | None = Field(default=None, max_length=500)
    original_language: str | None = Field(default=None, max_length=16)
    rights_note: str | None = Field(default=None, max_length=20000)
    status: str | None = Field(default=None, pattern='^(draft|archived)$')


class Subtitle(Strict):
    language: str = Field(min_length=2, max_length=16)
    label: str | None = Field(default=None, max_length=80)
    url: str = Field(min_length=1, max_length=500)


class EpisodeCreate(Strict):
    episode_number: int = Field(ge=1, le=10000)
    title: str = Field(default='', max_length=200)
    language: str = Field(default='en', max_length=16)
    subtitles: list[Subtitle] = Field(default_factory=list, max_length=32)


class EpisodePatch(Strict):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, max_length=16)
    subtitles: list[Subtitle] | None = Field(default=None, max_length=32)


class Upload(Strict):
    filename: str = Field(min_length=1, max_length=255)
    mime: str = Field(max_length=64)
    size_bytes: int = Field(gt=0, le=200 * 1024 * 1024)
    width: int = Field(ge=0, le=16384)
    height: int = Field(ge=0, le=16384)
    duration_seconds: int = Field(default=0, ge=0)
    language: str | None = Field(default=None, max_length=16)


class Version(Strict):
    version: int = Field(ge=1)


class GrantCreate(Strict):
    tenant_id: str = Field(min_length=1, max_length=64)
    title_version: int | None = Field(default=None, ge=1)
    countries: list[str] = Field(default_factory=list, max_length=250)
    languages: list[str] = Field(default_factory=list, max_length=50)
    starts_at: datetime | None = None
    sales_until: datetime | None = None
    play_until: datetime | None = None


class GrantPatch(Strict):
    version: int = Field(ge=1)
    status: str | None = Field(default=None, pattern='^(active|revoked)$')
    countries: list[str] | None = Field(default=None, max_length=250)
    languages: list[str] | None = Field(default=None, max_length=50)
    starts_at: datetime | None = None
    sales_until: datetime | None = None
    play_until: datetime | None = None


class Import(Strict):
    grant_id: int = Field(ge=1)
    adopt_new_version: bool = False
    title: str | None = Field(default=None, min_length=1, max_length=200)
    synopsis: str | None = Field(default=None, max_length=20000)
    cover_url: str | None = Field(default=None, max_length=500)


def tenant_access(request: Request, user=Depends(require_admin)):
    tenant = request.state.tenant
    if tenant.kind != 'native':
        service.refuse('native_library_only', '旧站连接器不能导入中央内容库', 409)
    require_permission(request, 'dramas.edit' if request.method == 'POST' else 'dramas.view')
    return user


def asset_for(db, asset_id, *, lock=False):
    stmt = select(MediaAsset).where(MediaAsset.id == asset_id)
    if lock:
        stmt = stmt.with_for_update()
    asset = db.scalar(stmt)
    if asset is None:
        service.refuse('library_asset_not_found', '视频资源不存在', 404)
    return asset


@platform_router.get('')
def list_titles(q: str = '', page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
                db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    stmt = select(LibraryTitle)
    if q.strip():
        stmt = stmt.where(LibraryTitle.title.ilike('%' + q.strip() + '%'))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(LibraryTitle.id.desc()).offset((page - 1) * page_size).limit(page_size))
    return dict(items=[service.title_out(db, row) for row in rows], total=total, page=page, page_size=page_size)


@platform_router.post('', status_code=201)
def create_title(body: TitleCreate, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    values = body.model_dump()
    values['original_language'] = service.language(values['original_language'])
    title = LibraryTitle(**values, created_by=actor.id)
    db.add(title)
    db.flush()
    service.audit(db, actor, 'library_create', title.id)
    service.commit(db)
    return service.title_out(db, title, detail=True)


@platform_router.get('/assets/{asset_id}/preview-url')
def preview_asset(asset_id: int, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    asset = asset_for(db, asset_id)
    if asset.status != 'ready':
        service.refuse('library_media_not_ready', '视频尚未就绪')
    if bunny.live():
        return bunny.sign_stream_playback(asset.provider_video_id, ttl=300)
    from app.modules.playback.service import sign_path, video_secret
    return sign_path('/videos/' + asset.provider_video_id + '/', video_secret())


@platform_router.post('/assets/{asset_id}/sync-status')
def sync_asset(asset_id: int, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    try:
        return service.sync_asset(db, asset_for(db, asset_id, lock=True))
    except bunny.BunnyError:
        raise HTTPException(502, detail='视频服务暂时不可用，请稍后同步') from None


@platform_router.api_route('/assets/{asset_id}/dev-tus', methods=['POST', 'PATCH', 'HEAD'])
async def fake_tus(asset_id: int, request: Request, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    # An observed fake upload is useful in a self-hosted dev environment. It is
    # never a production shortcut allowing a browser to claim media is ready.
    if bunny.live() or os.environ.get('APP_ENV') == 'production':
        raise HTTPException(404)
    asset = asset_for(db, asset_id, lock=True)
    headers = {'Tus-Resumable': '1.0.0', 'Upload-Offset': str(asset.upload_offset), 'Upload-Length': str(asset.size_bytes),
               'Location': str(request.url.path)}
    if request.method == 'HEAD':
        return Response(status_code=200, headers=headers)
    if request.method == 'POST':
        if request.headers.get('Upload-Length') not in (None, str(asset.size_bytes)):
            service.refuse('upload_length_mismatch', '上传大小不匹配', 422)
        return Response(status_code=201, headers=headers)
    if request.headers.get('Upload-Offset') != str(asset.upload_offset) or asset.status not in {'created', 'transcoding'}:
        service.refuse('upload_offset_mismatch', '上传偏移或状态不匹配')
    data = await request.body()
    if asset.upload_offset + len(data) > asset.size_bytes:
        service.refuse('upload_length_mismatch', '上传内容超出声明大小', 422)
    asset.upload_offset += len(data)
    asset.status = 'ready' if asset.upload_offset == asset.size_bytes else 'transcoding'
    service.commit(db)
    headers['Upload-Offset'] = str(asset.upload_offset)
    return Response(status_code=204, headers=headers)


@platform_router.get('/{title_id}')
def get_title(title_id: int, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    return service.title_out(db, service.title_for(db, title_id), detail=True)


@platform_router.patch('/{title_id}')
def patch_title(title_id: int, body: TitlePatch, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    title = service.title_for(db, title_id, lock=True)
    service.check_version(title, body.version)
    values = body.model_dump(exclude_unset=True, exclude={'version'})
    if any(value is None for value in values.values()):
        service.refuse('library_null_field', '内容字段不能设为空值', 422)
    if 'title' in values:
        values['title'] = values['title'].strip()
        if not values['title']:
            service.refuse('library_empty_title', '名称不能为空', 422)
    if 'original_language' in values:
        values['original_language'] = service.language(values['original_language'])
    requested_status = values.pop('status', None)
    if values:
        service.touch(title)
        for key, value in values.items():
            setattr(title, key, value)
    if requested_status:
        title.status = requested_status
        if not values:
            title.version += 1
    service.audit(db, actor, 'library_update', title.id, dict(fields=sorted(values), status=title.status, version=title.version))
    service.commit(db)
    return service.title_out(db, title, detail=True)


@platform_router.delete('/{title_id}')
def archive_title(title_id: int, version: int = Query(ge=1), db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    title = service.title_for(db, title_id, lock=True)
    service.check_version(title, version)
    title.status = 'archived'
    title.version += 1
    service.audit(db, actor, 'library_archive', title.id)
    service.commit(db)
    return dict(archived=True, version=title.version)


@platform_router.post('/{title_id}/episodes', status_code=201)
def create_episode(title_id: int, body: EpisodeCreate, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    title = service.title_for(db, title_id, lock=True)
    service.touch(title)
    ep = LibraryEpisode(library_title_id=title.id, episode_number=body.episode_number, title=body.title,
                        language=service.language(body.language),
                        subtitles=service.normalize_subtitles([track.model_dump(exclude_none=True) for track in body.subtitles]))
    db.add(ep)
    service.audit(db, actor, 'library_episode_create', title.id, dict(episode_number=body.episode_number))
    service.commit(db)
    return dict(id=ep.id, episode_number=ep.episode_number, title=ep.title, language=ep.language,
                subtitles=ep.subtitles, version=title.version, media_asset_id=None, asset=None)


@platform_router.patch('/{title_id}/episodes/{episode_id}')
def patch_episode(title_id: int, episode_id: int, body: EpisodePatch, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    title = service.title_for(db, title_id, lock=True)
    service.check_version(title, body.version)
    ep = service.episode_for(db, title_id, episode_id)
    values = body.model_dump(exclude_unset=True, exclude={'version'})
    if any(value is None for value in values.values()):
        service.refuse('library_null_field', '分集字段不能设为空值', 422)
    if values:
        service.touch(title)
        for key, value in values.items():
            if key == 'language': value = service.language(value)
            if key == 'subtitles': value = service.normalize_subtitles(value)
            setattr(ep, key, value)
    service.audit(db, actor, 'library_episode_update', title.id, dict(episode_id=ep.id, fields=sorted(values)))
    service.commit(db)
    return service.title_out(db, title, detail=True)


@platform_router.delete('/{title_id}/episodes/{episode_id}')
def delete_episode(title_id: int, episode_id: int, version: int = Query(ge=1), db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    title = service.title_for(db, title_id, lock=True)
    service.check_version(title, version)
    ep = service.episode_for(db, title_id, episode_id)
    if db.scalar(select(MediaAsset.id).where(MediaAsset.library_episode_id == ep.id).limit(1)) is not None:
        service.refuse('library_shared_media_protected', '已有上传资源的中央分集不能删除，请归档内容')
    service.touch(title)
    db.delete(ep)
    service.audit(db, actor, 'library_episode_delete', title.id, dict(episode_id=episode_id))
    service.commit(db)
    return dict(deleted=True, version=title.version)


@platform_router.post('/{title_id}/episodes/{episode_id}/upload-session')
def upload_episode(title_id: int, episode_id: int, body: Upload, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    title = service.title_for(db, title_id, lock=True)
    ep = service.episode_for(db, title_id, episode_id)
    try:
        return service.create_upload(db, title, ep, actor=actor, values=body.model_dump(exclude_none=True))
    except bunny.BunnyError:
        raise HTTPException(502, detail='视频服务暂时不可用，请稍后重试') from None


@platform_router.post('/{title_id}/approve')
def approve_title(title_id: int, body: Version, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    return service.approve(db, service.title_for(db, title_id, lock=True), version=body.version, actor=actor)


@platform_router.get('/{title_id}/grants')
def list_grants(title_id: int, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    service.title_for(db, title_id)
    return dict(items=[service.grant_out(row) for row in db.scalars(select(DistributionGrant).where(DistributionGrant.library_title_id == title_id).order_by(DistributionGrant.id))])


@platform_router.post('/{title_id}/grants', status_code=201)
def create_grant(title_id: int, body: GrantCreate, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    title = service.title_for(db, title_id, lock=True)
    if title.status == 'archived' or title.approved_version is None:
        service.refuse('library_not_approved', '内容尚未通过质检或已经归档')
    version = body.title_version or title.approved_version
    service.revision_for(db, title_id, version)
    tenant = db.get(Tenant, body.tenant_id)
    if tenant is None or tenant.kind != 'native' or tenant.status != 'active':
        service.refuse('library_site_not_available', '目标站点不存在、已暂停或不是原生站点', 422)
    row = DistributionGrant(library_title_id=title.id, title_version=version, tenant_id=tenant.id,
                            countries=service.normalize_countries(body.countries), languages=service.normalize_languages(body.languages),
                            starts_at=service.utc(body.starts_at), sales_until=service.utc(body.sales_until),
                            play_until=service.utc(body.play_until), created_by=actor.id)
    service.validate_window(row)
    db.add(row)
    service.audit(db, actor, 'library_grant_create', title.id, dict(tenant_id=tenant.id, title_version=version))
    service.commit(db)
    return service.grant_out(row)


@platform_router.patch('/{title_id}/grants/{grant_id}')
def patch_grant(title_id: int, grant_id: int, body: GrantPatch, db: Session = Depends(get_db), actor: AdminUser = Depends(require_platform_admin)):
    row = db.scalar(select(DistributionGrant).where(DistributionGrant.id == grant_id, DistributionGrant.library_title_id == title_id).with_for_update())
    if row is None:
        service.refuse('library_grant_not_found', '分发记录不存在', 404)
    service.check_version(row, body.version)
    values = body.model_dump(exclude_unset=True, exclude={'version'})
    for key, value in values.items():
        if key in {'status', 'countries', 'languages'} and value is None:
            service.refuse('library_null_field', '授权字段不能设为空值', 422)
        if key == 'countries': value = service.normalize_countries(value)
        if key == 'languages': value = service.normalize_languages(value)
        if key.endswith('_at') or key.endswith('_until'): value = service.utc(value)
        setattr(row, key, value)
    service.validate_window(row)
    row.version += 1
    service.audit(db, actor, 'library_grant_update', title_id, dict(grant_id=grant_id, fields=sorted(values), version=row.version))
    service.commit(db)
    return service.grant_out(row)


def available(db, tenant):
    rows = db.scalars(select(DistributionGrant).join(LibraryTitle, LibraryTitle.id == DistributionGrant.library_title_id)
                     .where(DistributionGrant.tenant_id == tenant.id, DistributionGrant.status == 'active', LibraryTitle.status != 'archived')
                     .order_by(DistributionGrant.title_version.desc(), DistributionGrant.id.desc()))
    results = {}
    for grant in rows:
        try:
            service.grant_active(grant, purpose='import')
        except HTTPException:
            continue
        if grant.library_title_id in results:
            continue
        revision = service.revision_for(db, grant.library_title_id, grant.title_version)
        snapshot = revision.snapshot
        if grant.languages and (snapshot['original_language'] not in grant.languages or any(ep['language'] not in grant.languages for ep in snapshot['episodes'])):
            continue
        local_versions = list(db.scalars(select(Drama).where(Drama.tenant_id == tenant.id,
             Drama.library_title_id == grant.library_title_id).order_by(Drama.library_version.desc(), Drama.id.desc())))
        matching = next((row for row in local_versions if row.library_version == grant.title_version), None)
        imported = matching or (local_versions[0] if local_versions else None)
        results[grant.library_title_id] = dict(id=grant.library_title_id, title=snapshot['title'], synopsis=snapshot['synopsis'],
             cover_url=snapshot['cover_url'], original_language=snapshot['original_language'], version=grant.title_version,
             status='approved', grant_id=grant.id, grant=service.grant_out(grant), imported_drama_id=imported.id if imported else None,
             imported_version=imported.library_version if imported else None,
             update_available=bool(imported and grant.title_version > imported.library_version),
             episode_count=len(snapshot['episodes']))
    return results


@tenant_router.get('')
def list_available(request: Request, db: Session = Depends(get_db), actor: AdminUser = Depends(tenant_access)):
    return dict(items=list(available(db, request.state.tenant).values()))


@tenant_router.get('/{title_id}')
def get_available(title_id: int, request: Request, db: Session = Depends(get_db), actor: AdminUser = Depends(tenant_access)):
    value = available(db, request.state.tenant).get(title_id)
    if value is None:
        service.refuse('library_not_available', '当前站点没有此内容的分发授权', 404)
    snapshot = service.revision_for(db, title_id, value['version']).snapshot
    value['episodes'] = [dict(id=ep['id'], episode_number=ep['episode_number'], title=ep['title'], language=ep['language'],
                              subtitles=service.permitted_subtitles(ep.get('subtitles', []), value['grant']['languages']),
                              asset=service.asset_out(db.get(MediaAsset, ep['media_asset_id']))) for ep in snapshot['episodes']]
    return value


@tenant_router.post('/{title_id}/import')
def import_available(title_id: int, body: Import, request: Request, db: Session = Depends(get_db), actor: AdminUser = Depends(tenant_access)):
    overrides = body.model_dump(exclude_unset=True, exclude={'grant_id', 'adopt_new_version'}, exclude_none=True)
    if 'title' in overrides:
        overrides['title'] = overrides['title'].strip()
        if not overrides['title']:
            service.refuse('library_empty_title', '名称不能为空', 422)
    return service.import_title(db, tenant=request.state.tenant, title_id=title_id, grant_id=body.grant_id,
                                actor=actor, overrides=overrides, adopt_new_version=body.adopt_new_version)
