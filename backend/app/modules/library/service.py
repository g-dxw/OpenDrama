"""Reviewed snapshots, shared-media uploads, and site-local distribution commands."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.modules.common import bunny
from app.modules.common.models import AuditLog
from app.modules.content.models import Drama, Episode, EpisodeSubtitle
from app.modules.library.models import DistributionGrant, LibraryEpisode, LibraryRevision, LibraryTitle, MediaAsset
from app.modules.tenancy.models import Tenant


def now_utc():
    return datetime.now(timezone.utc)


def utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def refuse(code, message, status=409):
    raise HTTPException(status, detail=dict(code=code, message=message))


def language(value):
    value = value.strip().lower()
    if not re.fullmatch(r'[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,2}', value) or len(value) > 16:
        refuse('invalid_language', '请使用有效的语言编码，例如 en、hi 或 pt-br', 422)
    return value


def normalize_countries(values):
    countries = sorted(set(value.strip().upper() for value in values))
    if any(not re.fullmatch(r'[A-Z]{2}', value) for value in countries):
        refuse('invalid_country', '地区必须使用两个字母的国家编码', 422)
    return countries


def normalize_languages(values):
    return sorted(set(language(value) for value in values))


def normalize_subtitles(values):
    """Central subtitle references are data, never executable or remote fetches."""
    if not isinstance(values, list) or len(values) > 32:
        refuse('library_invalid_subtitles', '每集最多配置 32 条字幕', 422)
    result, languages = [], set()
    for item in values:
        if not isinstance(item, dict) or set(item) - {'language', 'label', 'url'}:
            refuse('library_invalid_subtitles', '字幕仅支持 language、label、url 字段', 422)
        code = language(item.get('language') or '')
        if code in languages:
            refuse('library_duplicate_subtitle', '每集字幕语言不能重复', 422)
        label = (item.get('label') or code).strip() or code
        url = (item.get('url') or '').strip()
        if len(label) > 80 or not url or len(url) > 500 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url) or '\\' in url:
            refuse('library_invalid_subtitle_url', '字幕地址和标签无效', 422)
        try:
            parsed = urlsplit(url)
            # Accessing port also rejects malformed explicit port values.
            parsed.port
            relative = url.startswith('/') and not url.startswith('//') and not parsed.netloc and not parsed.scheme
            absolute = parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
        except ValueError:
            relative = absolute = False
        if not (relative or absolute):
            refuse('library_invalid_subtitle_url', '字幕地址必须为 HTTPS 或同源绝对路径', 422)
        languages.add(code)
        result.append(dict(language=code, label=label, url=url))
    return result


def permitted_subtitles(values, languages):
    tracks = normalize_subtitles(values)
    return [track for track in tracks if not languages or track['language'] in languages]


def audit(db, actor, action, target_id, payload=None, *, tenant_id=None):
    db.add(AuditLog(actor_type='admin', actor_id=actor.id, actor_scope='tenant' if tenant_id else 'platform',
                    tenant_id=tenant_id, action=action, target_type='library', target_id=target_id,
                    payload=dict(payload or {})))


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        refuse('library_conflict', '内容已存在或配置发生冲突，请刷新后重试')


def title_for(db, title_id, *, lock=False):
    stmt = select(LibraryTitle).where(LibraryTitle.id == title_id).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    row = db.scalar(stmt)
    if row is None:
        refuse('library_not_found', '内容不存在', 404)
    return row


def episode_for(db, title_id, episode_id):
    row = db.scalar(select(LibraryEpisode).where(LibraryEpisode.id == episode_id, LibraryEpisode.library_title_id == title_id))
    if row is None:
        refuse('library_episode_not_found', '分集不存在', 404)
    return row


def check_version(title, version):
    if title.version != version:
        refuse('library_version_conflict', '内容已变化，请刷新后重试')


def touch(title):
    if title.status == 'archived':
        refuse('library_archived', '内容已归档，不能继续修改')
    title.version += 1
    title.status = 'draft'


def revision_for(db, title_id, version):
    row = db.scalar(select(LibraryRevision).where(LibraryRevision.library_title_id == title_id, LibraryRevision.version == version))
    if row is None:
        refuse('library_not_approved', '此版本尚未通过内容质检')
    return row


def asset_out(asset, *, private=False):
    if asset is None:
        return None
    value = dict(id=asset.id, version=asset.version, language=asset.language, status=asset.status,
                 duration_seconds=asset.duration_seconds, width=asset.width, height=asset.height,
                 size_bytes=asset.size_bytes, mime=asset.mime, filename=asset.filename)
    if private:
        value.update(provider=asset.provider, video_id=asset.provider_video_id)
    return value


def grant_out(row):
    return dict(id=row.id, library_title_id=row.library_title_id, tenant_id=row.tenant_id,
                title_version=row.title_version, version=row.version, status=row.status,
                countries=row.countries, languages=row.languages,
                starts_at=row.starts_at, sales_until=row.sales_until, play_until=row.play_until)


def title_out(db, title, *, detail=False):
    episodes = list(db.scalars(select(LibraryEpisode).where(LibraryEpisode.library_title_id == title.id).order_by(LibraryEpisode.episode_number)))
    value = dict(id=title.id, title=title.title, synopsis=title.synopsis, cover_url=title.cover_url,
                 original_language=title.original_language, rights_note=title.rights_note,
                 version=title.version, approved_version=title.approved_version, status=title.status,
                 episode_count=len(episodes))
    if detail:
        value['episodes'] = [dict(id=ep.id, episode_number=ep.episode_number, title=ep.title,
                                  language=ep.language, subtitles=ep.subtitles or [], media_asset_id=ep.media_asset_id,
                                  asset=asset_out(db.get(MediaAsset, ep.media_asset_id) if ep.media_asset_id else None, private=True)) for ep in episodes]
        value['grants'] = [grant_out(row) for row in db.scalars(select(DistributionGrant).where(DistributionGrant.library_title_id == title.id).order_by(DistributionGrant.id))]
    return value


def approve(db, title, *, version, actor):
    check_version(title, version)
    if title.status == 'archived':
        refuse('library_archived', '已归档内容不能通过质检')
    episodes = list(db.scalars(select(LibraryEpisode).where(LibraryEpisode.library_title_id == title.id).order_by(LibraryEpisode.episode_number)))
    if not episodes or [ep.episode_number for ep in episodes] != list(range(1, len(episodes) + 1)):
        refuse('library_incomplete', '分集必须从第 1 集开始连续排列，且至少有一集')
    snapshot_episodes = []
    for ep in episodes:
        asset = db.get(MediaAsset, ep.media_asset_id) if ep.media_asset_id else None
        if asset is None or asset.library_episode_id != ep.id or asset.status != 'ready':
            refuse('library_media_not_ready', f'第 {ep.episode_number} 集视频尚未转码完成')
        snapshot_episodes.append(dict(id=ep.id, episode_number=ep.episode_number, title=ep.title,
                                     language=asset.language, media_asset_id=asset.id,
                                     subtitles=normalize_subtitles(ep.subtitles or [])))
    existing = db.scalar(select(LibraryRevision).where(LibraryRevision.library_title_id == title.id, LibraryRevision.version == title.version))
    if existing is None:
        db.add(LibraryRevision(library_title_id=title.id, version=title.version, approved_by=actor.id,
                               snapshot=dict(title=title.title, synopsis=title.synopsis, cover_url=title.cover_url,
                                             original_language=title.original_language, rights_note=title.rights_note,
                                             episodes=snapshot_episodes)))
    title.status = 'approved'
    title.approved_version = title.version
    audit(db, actor, 'library_approve', title.id, dict(version=title.version, episode_count=len(episodes)))
    commit(db)
    return title_out(db, title, detail=True)


def validate_window(grant):
    starts, sales, plays = utc(grant.starts_at), utc(grant.sales_until), utc(grant.play_until)
    if sales and plays and sales > plays:
        refuse('invalid_distribution_window', '停止销售时间不能晚于最后播放时间', 422)
    if starts and ((plays and starts >= plays) or (sales and starts >= sales)):
        refuse('invalid_distribution_window', '授权开始时间必须早于结束时间', 422)


def grant_active(grant, *, purpose='play', now=None, country=None):
    now = utc(now) or now_utc()
    if grant.status != 'active':
        refuse('distribution_revoked', '此站点的内容分发授权已撤销', 403)
    if grant.starts_at and now < utc(grant.starts_at):
        refuse('distribution_not_started', '内容分发授权尚未开始', 403)
    if grant.play_until and now >= utc(grant.play_until):
        refuse('distribution_expired', '内容播放授权已到期', 403)
    if purpose in {'sale', 'publish', 'import'} and grant.sales_until and now >= utc(grant.sales_until):
        refuse('distribution_sales_ended', '此内容已停止新增销售和上架', 403)
    if grant.countries and purpose in {'play', 'sale'}:
        if not country or country.upper() not in grant.countries:
            refuse('distribution_region_blocked', '此地区不能使用该内容授权', 403)


def assert_distribution_allowed(db, drama, *, purpose='play', country=None, now=None, episode=None):
    """Shared guard for publication, checkout/unlock and every playback URL.

    Existing private records have no library refs and retain their old behavior.
    Regions come from the trusted site/edge request context, never query filters.
    """
    if drama.library_title_id is None:
        if episode is not None and episode.media_asset_id is not None:
            refuse('distribution_source_invalid', '站点内容来源不完整', 403)
        return None
    scoped_tenant = db.info.get('content_tenant_id')
    if scoped_tenant and scoped_tenant != drama.tenant_id:
        refuse('distribution_wrong_site', '内容不属于当前站点', 404)
    grant = db.get(DistributionGrant, drama.distribution_grant_id, populate_existing=True)
    title = db.get(LibraryTitle, drama.library_title_id, populate_existing=True)
    if (grant is None or title is None or title.status == 'archived' or
            grant.tenant_id != drama.tenant_id or grant.library_title_id != drama.library_title_id or
            grant.title_version != drama.library_version):
        refuse('distribution_source_invalid', '内容分发来源无效或已归档', 403)
    grant_active(grant, purpose=purpose, now=now, country=db.info.get('viewer_country', country))
    snapshot = revision_for(db, title.id, drama.library_version).snapshot
    selected_language = (drama.language or snapshot['original_language']).lower()
    if grant.languages and selected_language not in grant.languages:
        refuse('distribution_language_blocked', '当前语言不在授权范围内', 403)
    expected = {entry['id']: entry for entry in snapshot['episodes']}
    episodes = [episode] if episode is not None else list(db.scalars(select(Episode).where(Episode.drama_id == drama.id)))
    if not episodes:
        refuse('distribution_media_missing', '分发内容没有可用分集')
    for item in episodes:
        source = expected.get(item.library_episode_id)
        asset = db.get(MediaAsset, item.media_asset_id, populate_existing=True) if item.media_asset_id else None
        if (item.drama_id != drama.id or source is None or source['media_asset_id'] != item.media_asset_id or
                asset is None or asset.status != 'ready' or asset.library_episode_id != item.library_episode_id or
                item.bunny_video_id != asset.provider_video_id):
            refuse('distribution_media_not_ready', '分发视频来源无效或尚未就绪', 403)
        if grant.languages and asset.language not in grant.languages:
            refuse('distribution_language_blocked', '分集语言不在授权范围内', 403)
    return grant


def playback_ttl(grant, *, maximum=300):
    if grant is None or grant.play_until is None:
        return maximum
    return max(0, min(maximum, math.floor((utc(grant.play_until) - now_utc()).total_seconds())))


def assert_site_media_editable(episode):
    if episode.media_asset_id is not None:
        refuse('shared_library_media', '此视频来自中央内容库，请在内容库创建新版本；站点不能替换共享母版')


def import_title(db, *, tenant, title_id, grant_id, actor, overrides, adopt_new_version=False):
    # Serialize all imports to a site so concurrent repeated clicks return its
    # existing local IDs instead of a unique-key failure or two separate series.
    db.scalar(select(Tenant).where(Tenant.id == tenant.id).with_for_update())
    grant = db.scalar(select(DistributionGrant).where(DistributionGrant.id == grant_id,
                     DistributionGrant.library_title_id == title_id, DistributionGrant.tenant_id == tenant.id).with_for_update())
    title = title_for(db, title_id)
    if grant is None or title.status == 'archived':
        refuse('library_not_available', '当前站点没有此内容的分发授权', 404)
    grant_active(grant, purpose='import')
    revision = revision_for(db, title_id, grant.title_version)
    snapshot = revision.snapshot
    if grant.languages and (snapshot['original_language'] not in grant.languages or
                            any(row['language'] not in grant.languages for row in snapshot['episodes'])):
        refuse('distribution_language_blocked', '此内容版本的语言不在授权范围内', 403)
    # Each approved edition has its own site identities. A normal import never
    # replaces an older live edition; the operator must explicitly adopt a new
    # edition, which creates a separate draft with no inherited viewer purchases.
    local_versions = list(db.scalars(select(Drama).where(Drama.tenant_id == tenant.id,
                      Drama.library_title_id == title_id).order_by(Drama.library_version.desc(), Drama.id.desc())))
    matching = next((row for row in local_versions if row.library_version == grant.title_version), None)
    newest = local_versions[0] if local_versions else None
    existing = matching or (newest if newest is not None and
                           (not adopt_new_version or grant.title_version <= newest.library_version) else None)
    if existing is not None:
        count = db.scalar(select(func.count()).select_from(Episode).where(Episode.drama_id == existing.id)) or 0
        return dict(drama_id=existing.id, created=False, episode_count=count)
    default_title = snapshot['title']
    if newest is not None:
        suffix = ' · v' + str(grant.title_version)
        default_title = default_title[:200 - len(suffix)] + suffix
    drama = Drama(tenant_id=tenant.id, library_title_id=title_id, distribution_grant_id=grant.id,
                  library_version=grant.title_version, title=overrides.get('title') or default_title,
                  synopsis=overrides.get('synopsis', snapshot['synopsis']), cover_url=overrides.get('cover_url', snapshot['cover_url']),
                  language=snapshot['original_language'], country_allowlist=','.join(grant.countries), status='draft',
                  free_episode_count=0, coin_price_per_episode=0)
    db.add(drama)
    try:
        db.flush()
        for row in snapshot['episodes']:
            asset = db.get(MediaAsset, row['media_asset_id'])
            if asset is None or asset.status != 'ready':
                refuse('library_media_not_ready', '中央视频未就绪，暂时不能分发')
            values = dict(drama_id=drama.id, library_episode_id=row['id'], media_asset_id=asset.id,
                          episode_number=row['episode_number'], title=row['title'], status='in_review',
                          bunny_video_id=asset.provider_video_id, width=asset.width, height=asset.height,
                          duration_seconds=asset.duration_seconds, size_bytes=asset.size_bytes, mime=asset.mime)
            subtitles = permitted_subtitles(row.get('subtitles', []), grant.languages)
            values['subtitle_url'] = subtitles[0]['url'] if subtitles else ''
            if hasattr(Episode, 'tenant_id'):
                values['tenant_id'] = tenant.id
            site_episode = Episode(**values)
            db.add(site_episode)
            db.flush()
            for index, track in enumerate(subtitles):
                db.add(EpisodeSubtitle(episode_id=site_episode.id, language=track['language'],
                    label=track['label'], url=track['url'], is_default=index == 0))
        audit(db, actor, 'library_import', title_id, dict(drama_id=drama.id, grant_id=grant.id,
              library_version=grant.title_version, adopted_from_drama_id=newest.id if newest is not None else None), tenant_id=tenant.id)
        commit(db)
    except IntegrityError:
        db.rollback()
        refuse('library_import_conflict', '站点已有同名内容，请修改标题后重试')
    return dict(drama_id=drama.id, created=True, episode_count=len(snapshot['episodes']))


def create_upload(db, title, episode, *, actor, values):
    from app.modules.content.upload_service import ALLOWED_MIME, MAX_BYTES, sign_callback
    if values['mime'] not in ALLOWED_MIME or not 0 < values['size_bytes'] <= MAX_BYTES:
        refuse('library_upload_invalid', '请上传不超过 200MB 的 MP4 或 MOV 文件', 422)
    if title.status == 'archived':
        refuse('library_archived', '归档内容不能上传')
    current = db.get(MediaAsset, episode.media_asset_id) if episode.media_asset_id else None
    if current is not None and current.status in {'created', 'transcoding'}:
        refuse('library_upload_in_progress', '此分集已有上传任务，请先完成或确认失败')
    version = (db.scalar(select(func.max(MediaAsset.version)).where(MediaAsset.library_episode_id == episode.id)) or 0) + 1
    video_id = bunny.create_stream_video(title=values['filename']) if bunny.live() else secrets.token_hex(16)
    asset = MediaAsset(library_episode_id=episode.id, provider_video_id=video_id, version=version,
                       filename=values['filename'], mime=values['mime'], size_bytes=values['size_bytes'],
                       width=values['width'], height=values['height'], duration_seconds=values.get('duration_seconds', 0),
                       language=language(values.get('language') or episode.language), status='created')
    db.add(asset)
    db.flush()
    episode.media_asset_id = asset.id
    touch(title)
    audit(db, actor, 'library_upload_created', title.id, dict(episode_id=episode.id, asset_id=asset.id))
    commit(db)
    if bunny.live():
        upload_url, headers = bunny.STREAM_TUS_ENDPOINT, bunny.tus_headers(video_id)
    else:
        upload_url = f'/platform/v1/library/assets/{asset.id}/dev-tus'
        headers = dict(AuthorizationSignature=sign_callback(video_id.encode()))
    return dict(asset_id=asset.id, video_id=video_id, upload_url=upload_url, headers=headers,
                status=asset.status, episode_id=episode.id, version=title.version)


def sync_asset(db, asset):
    if not bunny.live():
        return asset_out(asset, private=True)
    remote = bunny.stream_video(asset.provider_video_id)
    if remote.get('guid') not in (None, asset.provider_video_id):
        refuse('library_media_mismatch', '视频服务返回了不匹配的资源', 502)
    status = remote.get('status')
    if status == 4:
        asset.status = 'ready'
        asset.duration_seconds = max(0, int(remote.get('length') or asset.duration_seconds))
        asset.width = max(0, int(remote.get('width') or asset.width))
        asset.height = max(0, int(remote.get('height') or asset.height))
    elif status in {5, 6}:
        asset.status = 'failed'
    elif asset.status not in {'ready', 'retired'}:
        asset.status = 'transcoding'
    commit(db)
    return asset_out(asset, private=True)
