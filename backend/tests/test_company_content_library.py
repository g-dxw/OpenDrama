"""Two independent sites receive reviewed snapshots without copying media/jobs."""
from datetime import datetime, timedelta, timezone
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.common import bunny
from app.modules.content.models import Drama, Episode, MediaUpload
from app.modules.library.models import DistributionGrant, LibraryRevision, LibraryTitle, MediaAsset
from app.modules.library.service import assert_distribution_allowed
from app.modules.ops import service as auth
from app.modules.sites.service import provision_site
from app.modules.tenancy.models import Tenant, TenantDomain
from app.modules.tenancy.permissions import seed_tenant_defaults

PLATFORM = '/platform/v1/library'
TENANT = '/tenant/v1/library'


def bearer(person):
    return {'Authorization': 'Bearer ' + auth.create_session(person)}


@pytest.fixture()
def library_env(session, make):
    sites = []
    for code in ('library-alpha', 'library-bravo'):
        site = Tenant(id=code, code=code, name=code, kind='native', status='active')
        session.add(site)
        session.flush()
        seed_tenant_defaults(session, site)
        provision_site(session, site)
        session.add(TenantDomain(tenant_id=site.id, hostname=code + '.example.test', kind='site',
                                status='active', verified_at=datetime.now(timezone.utc)))
        session.commit()
        sites.append(site)
    owners = [make.admin(role='admin', tenant_id=site.id) for site in sites]
    return dict(db=session, make=make, client=TestClient(app), sites=sites, owners=owners,
                pheaders=bearer(make.platform_admin()), headers=[bearer(person) for person in owners])


def ok(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


def library_title(env, *, ready=True):
    client, headers = env['client'], env['pheaders']
    title = ok(client.post(PLATFORM, json=dict(title='Central Drama', original_language='en', rights_note='Company owned'), headers=headers), 201)
    ep = ok(client.post(f"{PLATFORM}/{title['id']}/episodes", json=dict(episode_number=1, title='The Arrival'), headers=headers), 201)
    upload = ok(client.post(f"{PLATFORM}/{title['id']}/episodes/{ep['id']}/upload-session",
                     json=dict(filename='episode.mp4', mime='video/mp4', size_bytes=4, width=720, height=1280, duration_seconds=60), headers=headers))
    if ready:
        response = client.patch(upload['upload_url'], content=b'abcd', headers=dict(headers, **{'Upload-Offset': '0'}))
        assert response.status_code == 204, response.text
    title = ok(client.get(f"{PLATFORM}/{title['id']}", headers=headers))
    return title, ep, upload


def approved(env):
    title, ep, upload = library_title(env)
    title = ok(env['client'].post(f"{PLATFORM}/{title['id']}/approve", json=dict(version=title['version']), headers=env['pheaders']))
    return title, ep, upload


def grant(env, title, site_index=0, **values):
    return ok(env['client'].post(f"{PLATFORM}/{title['id']}/grants",
              json=dict(tenant_id=env['sites'][site_index].id, **values), headers=env['pheaders']), 201)


def imported(env, title, row, site_index=0):
    return ok(env['client'].post(f"{TENANT}/{title['id']}/import", json=dict(grant_id=row['id']), headers=env['headers'][site_index]))


def test_private_content_is_not_shared_and_library_is_platform_only(library_env):
    env = library_env
    private = env['make'].drama(title='Existing JJTV Private')
    title, _, _ = library_title(env)
    for headers in env['headers']:
        assert ok(env['client'].get(TENANT, headers=headers))['items'] == []
        assert env['client'].get(f"{TENANT}/{title['id']}", headers=headers).status_code == 404
        assert env['client'].get(PLATFORM, headers=headers).status_code == 403
        assert env['client'].post(PLATFORM, json={'title': 'Escape'}, headers=headers).status_code == 403
    env['db'].expire_all()
    assert env['db'].get(Drama, private.id).library_title_id is None
    assert env['db'].get(Drama, private.id).tenant_id == 'jjtv'


def test_upload_must_finish_before_approval_and_client_cannot_claim_ready(library_env):
    env = library_env
    title, _, upload = library_title(env, ready=False)
    response = env['client'].post(f"{PLATFORM}/{title['id']}/approve", json=dict(version=title['version']), headers=env['pheaders'])
    assert response.status_code == 409 and response.json()['detail']['code'] == 'library_media_not_ready'
    response = ok(env['client'].post(f"{PLATFORM}/assets/{upload['asset_id']}/sync-status", json={'status': 'ready'}, headers=env['pheaders']))
    assert response['status'] == 'created'
    wrong = env['client'].patch(upload['upload_url'], content=b'abcd', headers=dict(env['pheaders'], **{'Upload-Offset': '2'}))
    assert wrong.status_code == 409
    assert env['client'].patch(upload['upload_url'], content=b'ab', headers=dict(env['pheaders'], **{'Upload-Offset': '0'})).status_code == 204
    state = ok(env['client'].get(f"{PLATFORM}/{title['id']}", headers=env['pheaders']))
    assert state['episodes'][0]['asset']['status'] == 'transcoding'
    assert env['client'].patch(upload['upload_url'], content=b'cd', headers=dict(env['pheaders'], **{'Upload-Offset': '2'})).status_code == 204
    result = ok(env['client'].post(f"{PLATFORM}/{title['id']}/approve", json=dict(version=title['version']), headers=env['pheaders']))
    assert result['status'] == 'approved'


def test_real_reconciliation_uses_provider_state_not_browser_status(library_env, monkeypatch):
    env = library_env
    title, _, upload = library_title(env, ready=False)
    monkeypatch.setenv('BUNNY_MODE', 'real')
    monkeypatch.setattr(bunny, 'stream_video', lambda video_id: dict(guid=video_id, status=4, length=72, width=1080, height=1920))
    value = ok(env['client'].post(f"{PLATFORM}/assets/{upload['asset_id']}/sync-status", headers=env['pheaders']))
    assert value['status'] == 'ready' and value['duration_seconds'] == 72
    assert value['width'] == 1080
    result = ok(env['client'].post(f"{PLATFORM}/{title['id']}/approve", json=dict(version=title['version']), headers=env['pheaders']))
    assert result['approved_version'] == title['version']


def test_two_sites_import_once_share_asset_and_keep_independent_metadata(library_env):
    env = library_env
    title, _, upload = approved(env)
    first_grant, second_grant = grant(env, title), grant(env, title, 1)
    first, second = imported(env, title, first_grant), imported(env, title, second_grant, 1)
    repeated = imported(env, title, first_grant)
    assert first['created'] is True and repeated == dict(first, created=False)
    assert first['drama_id'] != second['drama_id']
    db = env['db']
    db.expire_all()
    dramas = [db.get(Drama, value['drama_id']) for value in (first, second)]
    assert [row.tenant_id for row in dramas] == [site.id for site in env['sites']]
    assert all(row.status == 'draft' for row in dramas)
    episodes = [db.scalar(select(Episode).where(Episode.drama_id == row.id)) for row in dramas]
    assert episodes[0].id != episodes[1].id
    assert all(row.media_asset_id == upload['asset_id'] and row.bunny_video_id == upload['video_id'] for row in episodes)
    assert db.scalar(select(func.count()).select_from(MediaAsset)) == 1
    assert db.scalar(select(func.count()).select_from(MediaUpload)) == 0
    changed = env['client'].patch('/admin/v1/dramas/' + str(first['drama_id']), json={'title': 'Alpha Local Name'}, headers=env['headers'][0])
    assert changed.status_code == 200, changed.text
    db.expire_all()
    assert db.get(Drama, second['drama_id']).title == 'Central Drama'
    assert db.get(LibraryTitle, title['id']).title == 'Central Drama'
    assert env['client'].get('/admin/v1/dramas/' + str(first['drama_id']), headers=env['headers'][1]).status_code == 404


def test_import_cannot_use_other_site_grant_and_permissions_are_explicit(library_env):
    env = library_env
    title, _, _ = approved(env)
    row = grant(env, title)
    response = env['client'].post(f"{TENANT}/{title['id']}/import", json=dict(grant_id=row['id']), headers=env['headers'][1])
    assert response.status_code == 404
    finance = env['make'].admin(role='finance', tenant_id=env['sites'][0].id)
    denied = bearer(finance)
    # The existing finance preset intentionally includes dramas.view, while
    # importing requires dramas.edit independently of that read capability.
    assert env['client'].get(TENANT, headers=denied).status_code == 200
    assert env['client'].post(f"{TENANT}/{title['id']}/import", json=dict(grant_id=row['id']), headers=denied).status_code == 403
    assert env['client'].post(f"{TENANT}/{title['id']}/import", json=dict(grant_id=row['id'], tenant_id=env['sites'][1].id), headers=env['headers'][0]).status_code == 422
    from app.modules.tenancy.models import TenantModule
    module = env['db'].get(TenantModule, (env['sites'][0].id, 'dramas'))
    module.enabled = False
    env['db'].commit()
    assert env['client'].get(TENANT, headers=env['headers'][0]).status_code == 403


def test_later_master_edits_leave_imported_edition_and_local_ids_unchanged(library_env):
    env = library_env
    title, _, _ = approved(env)
    first_grant = grant(env, title)
    first = imported(env, title, first_grant)
    old_version = title['version']
    title = ok(env['client'].patch(f"{PLATFORM}/{title['id']}", json=dict(version=title['version'], title='New Master Title'), headers=env['pheaders']))
    assert title['status'] == 'draft'
    # Previously approved revision remains available while the new draft is edited.
    visible = ok(env['client'].get(TENANT, headers=env['headers'][0]))['items'][0]
    assert visible['title'] == 'Central Drama' and visible['version'] == old_version
    title = ok(env['client'].post(f"{PLATFORM}/{title['id']}/approve", json=dict(version=title['version']), headers=env['pheaders']))
    second = imported(env, title, grant(env, title, 1), 1)
    env['db'].expire_all()
    assert env['db'].get(Drama, first['drama_id']).title == 'Central Drama'
    assert env['db'].get(Drama, first['drama_id']).library_version == old_version
    assert env['db'].get(Drama, second['drama_id']).title == 'New Master Title'
    assert env['db'].scalar(select(func.count()).select_from(LibraryRevision)) == 2


def test_new_edition_requires_explicit_adoption_preserves_paid_old_edition(library_env):
    from app.modules.account.models import User
    from app.modules.wallet.models import Entitlement, WalletAccount
    from app.modules.playback.service import authorize
    env = library_env
    title, _, upload = approved(env)
    old_grant = grant(env, title)
    first = imported(env, title, old_grant)
    db = env['db']
    old_drama = db.get(Drama, first['drama_id'])
    old_episode = db.scalar(select(Episode).where(Episode.drama_id == old_drama.id))
    old_drama.status = 'in_review'
    db.commit()
    old_drama.status = 'published'
    old_drama.coin_price_per_episode = 23
    old_episode.status = 'published'
    user = User(tenant_id=old_drama.tenant_id, email='edition-owner@example.test', display_name='Existing buyer')
    db.add(user)
    db.flush()
    db.add(WalletAccount(tenant_id=old_drama.tenant_id, user_id=user.id, balance=77))
    entitlement = Entitlement(tenant_id=old_drama.tenant_id, user_id=user.id, episode_id=old_episode.id)
    db.add(entitlement)
    db.commit()
    old_ids = old_drama.id, old_episode.id, entitlement.id
    old_version = title['version']

    title = ok(env['client'].patch(f"{PLATFORM}/{title['id']}", json=dict(version=title['version'], title='Revised Master'), headers=env['pheaders']))
    title = ok(env['client'].post(f"{PLATFORM}/{title['id']}/approve", json=dict(version=title['version']), headers=env['pheaders']))
    new_grant = grant(env, title)
    item = ok(env['client'].get(TENANT, headers=env['headers'][0]))['items'][0]
    assert item['imported_drama_id'] == old_drama.id
    assert item['imported_version'] == old_version and item['update_available'] is True
    # Ordinary import must remain non-destructive even with the newer grant.
    unchanged = imported(env, title, new_grant)
    assert unchanged == dict(first, created=False)
    body = dict(grant_id=new_grant['id'], adopt_new_version=True)
    adopted = ok(env['client'].post(f"{TENANT}/{title['id']}/import", json=body, headers=env['headers'][0]))
    repeated = ok(env['client'].post(f"{TENANT}/{title['id']}/import", json=body, headers=env['headers'][0]))
    assert adopted['created'] is True and adopted['drama_id'] != old_drama.id
    assert repeated == dict(adopted, created=False)
    db.expire_all()
    old_drama, old_episode = db.get(Drama, old_ids[0]), db.get(Episode, old_ids[1])
    assert old_drama.status == 'published' and old_drama.library_version == old_version
    assert old_episode.status == 'published'
    assert db.get(Entitlement, old_ids[2]).episode_id == old_episode.id
    assert db.get(WalletAccount, user.id).balance == 77
    new_drama = db.get(Drama, adopted['drama_id'])
    new_episode = db.scalar(select(Episode).where(Episode.drama_id == new_drama.id))
    assert new_drama.status == 'draft' and new_drama.title == 'Revised Master · v' + str(title['version'])
    assert new_drama.library_version == title['version']
    assert new_episode.status == 'in_review' and new_episode.id != old_episode.id
    assert new_episode.media_asset_id == old_episode.media_asset_id == upload['asset_id']
    assert db.scalar(select(func.count()).select_from(Entitlement).where(Entitlement.episode_id == new_episode.id)) == 0
    db.info['content_tenant_id'] = old_drama.tenant_id
    assert authorize(db, drama_id=old_drama.id, episode_id=old_episode.id, user_id=user.id)['allowed'] is True
    assert authorize(db, drama_id=new_drama.id, episode_id=new_episode.id, user_id=user.id)['allowed'] is False
    final = ok(env['client'].get(TENANT, headers=env['headers'][0]))['items'][0]
    assert final['imported_drama_id'] == new_drama.id and final['imported_version'] == title['version']
    assert final['update_available'] is False


def test_sales_end_does_not_cancel_existing_play_and_region_cannot_be_omitted(library_env):
    env = library_env
    title, _, _ = approved(env)
    now = datetime.now(timezone.utc)
    row = grant(env, title, countries=['US'], sales_until=(now + timedelta(hours=1)).isoformat(), play_until=(now + timedelta(days=1)).isoformat())
    local = imported(env, title, row)
    db = env['db']
    db.expire_all()
    drama = db.get(Drama, local['drama_id'])
    db.info['content_tenant_id'] = drama.tenant_id
    db.info['viewer_country'] = 'US'
    assert_distribution_allowed(db, drama, purpose='play', now=now + timedelta(hours=2))
    with pytest.raises(HTTPException) as ended:
        assert_distribution_allowed(db, drama, purpose='sale', now=now + timedelta(hours=2))
    assert ended.value.detail['code'] == 'distribution_sales_ended'
    db.info['viewer_country'] = None
    with pytest.raises(HTTPException) as unknown:
        assert_distribution_allowed(db, drama, purpose='play', country='US')
    assert unknown.value.detail['code'] == 'distribution_region_blocked'
    db.info['viewer_country'] = 'IN'
    with pytest.raises(HTTPException):
        assert_distribution_allowed(db, drama, purpose='play', country='US')


def test_revocation_blocks_manual_bulk_and_scheduled_publish(library_env):
    env = library_env
    title, _, _ = approved(env)
    row = grant(env, title)
    local = imported(env, title, row)
    db = env['db']
    ep = db.scalar(select(Episode).where(Episode.drama_id == local['drama_id']))
    ok(env['client'].patch(f"{PLATFORM}/{title['id']}/grants/{row['id']}", json=dict(version=row['version'], status='revoked'), headers=env['pheaders']))
    assert env['client'].get('/admin/v1/episodes/' + str(ep.id) + '/preview-url', headers=env['headers'][0]).status_code == 403
    assert env['client'].post('/admin/v1/episodes/' + str(ep.id) + '/publish', headers=env['headers'][0]).status_code == 403
    assert env['client'].post('/admin/v1/episodes/bulk-status', json=dict(episode_ids=[ep.id], action='publish'), headers=env['headers'][0]).status_code == 403
    from app.modules.content.publish_service import apply_due_schedules
    drama = db.get(Drama, local['drama_id'])
    drama.status = 'in_review'
    drama.scheduled_publish_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert apply_due_schedules(db) == 0
    assert drama.status == 'in_review'


def test_playback_expiry_caps_token_and_stops_new_authorization(library_env):
    env = library_env
    title, _, _ = approved(env)
    until = datetime.now(timezone.utc) + timedelta(seconds=45)
    row = grant(env, title, play_until=until.isoformat())
    local = imported(env, title, row)
    db = env['db']
    drama = db.get(Drama, local['drama_id'])
    ep = db.scalar(select(Episode).where(Episode.drama_id == drama.id))
    drama.status = 'in_review'
    db.commit()
    drama.status = 'published'
    drama.free_episode_count = 1
    ep.status = 'published'
    db.commit()
    db.info['content_tenant_id'] = drama.tenant_id
    from app.modules.playback.service import authorize
    result = authorize(db, drama_id=drama.id, episode_id=ep.id, user_id=None)
    assert result['allowed'] is True
    assert time.time() < result['expires_at'] <= until.timestamp()
    grant_model = db.get(DistributionGrant, row['id'])
    grant_model.play_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    denied = authorize(db, drama_id=drama.id, episode_id=ep.id, user_id=None)
    assert denied['allowed'] is False and denied['reason'] == 'distribution_expired'


def test_site_cannot_replace_shared_media_and_site_delete_preserves_master(library_env):
    env = library_env
    title, _, upload = approved(env)
    first = imported(env, title, grant(env, title))
    second = imported(env, title, grant(env, title, 1), 1)
    db = env['db']
    episode = db.scalar(select(Episode).where(Episode.drama_id == first['drama_id']))
    response = env['client'].post('/admin/v1/episodes/' + str(episode.id) + '/upload-session',
         json=dict(filename='replace.mp4', mime='video/mp4', size_bytes=4, width=720, height=1280), headers=env['headers'][0])
    assert response.status_code == 409 and response.json()['detail']['code'] == 'shared_library_media'
    response = env['client'].post('/admin/v1/dramas/' + str(first['drama_id']) + '/delete', json={'confirm': True}, headers=env['headers'][0])
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(MediaAsset, upload['asset_id']).status == 'ready'
    assert db.get(Drama, second['drama_id']) is not None
    assert db.get(LibraryTitle, title['id']).status == 'approved'


def test_archive_stops_distribution_without_deleting_uploaded_assets(library_env):
    env = library_env
    title, _, upload = approved(env)
    row = grant(env, title)
    local = imported(env, title, row)
    response = env['client'].delete(f"{PLATFORM}/{title['id']}", params={'version': title['version']}, headers=env['pheaders'])
    assert response.status_code == 200, response.text
    assert ok(env['client'].get(TENANT, headers=env['headers'][0]))['items'] == []
    env['db'].expire_all()
    with pytest.raises(HTTPException):
        assert_distribution_allowed(env['db'], env['db'].get(Drama, local['drama_id']), purpose='play')
    assert env['db'].get(MediaAsset, upload['asset_id']).status == 'ready'


def test_public_details_preview_catalog_feed_and_playback_disclose_dates(library_env, monkeypatch):
    env = library_env
    title, _, _ = approved(env)
    now = datetime.now(timezone.utc)
    sales, watches = now + timedelta(days=2), now + timedelta(days=10)
    local = imported(env, title, grant(env, title, sales_until=sales.isoformat(), play_until=watches.isoformat()))
    db = env['db']
    drama = db.get(Drama, local['drama_id'])
    episode = db.scalar(select(Episode).where(Episode.drama_id == drama.id))
    drama.status = 'in_review'
    db.commit()
    drama.status = 'published'
    drama.free_episode_count = 1
    episode.status = 'published'
    db.commit()
    public = {'Host': env['sites'][0].id + '.example.test'}
    client = env['client']
    detail = ok(client.get('/v1/dramas/' + str(drama.id), headers=public))
    preview = ok(client.get('/v1/preview/dramas/' + str(drama.id), headers=public))['drama']
    play = ok(client.get('/v1/dramas/' + str(drama.id) + '/play', headers=public))['drama']
    catalog = ok(client.get('/v1/catalog', headers=public))['items'][0]
    playback = ok(client.post('/v1/playback', json=dict(drama_id=drama.id, episode_id=episode.id), headers=public))
    assert playback['allowed'] is True
    feed = ok(client.get('/v1/feed', headers=dict(public, **{'X-Device-Id':'public-terms-probe'})))['items'][0]['drama']
    for payload in (detail, preview, play, catalog, playback, feed):
        assert datetime.fromisoformat(payload['watch_until']) == watches
        assert datetime.fromisoformat(payload['sales_until']) == sales
        assert not {'grant_id','distribution_grant_id','library_title_id','rights_note'} & set(payload)
    # The locked response carries the same dates before a coin purchase.
    drama.free_episode_count = 0
    drama.coin_price_per_episode = 23
    db.commit()
    locked = ok(client.post('/v1/playback', json=dict(drama_id=drama.id, episode_id=episode.id), headers=public))
    assert locked['allowed'] is False
    assert datetime.fromisoformat(locked['watch_until']) == watches
    private = env['make'].drama(title='Private without library term', status='published')
    monkeypatch.setenv('SITE_LEGACY_HOSTS', 'legacy.example.test')
    private_out = ok(client.get('/v1/dramas/' + str(private.id), headers={'Host':'legacy.example.test'}))
    assert private_out['watch_until'] is None and private_out['sales_until'] is None


def test_coin_purchase_audits_disclosed_dates_once_without_rewriting_membership(library_env):
    from app.modules.account.models import User
    from app.modules.common.models import AuditLog
    from app.modules.wallet.models import WalletAccount
    from app.modules.wallet.service import unlock_episode
    env = library_env
    title, _, _ = approved(env)
    now = datetime.now(timezone.utc)
    sales, watches = now + timedelta(days=2), now + timedelta(days=10)
    local = imported(env, title, grant(env, title, sales_until=sales.isoformat(), play_until=watches.isoformat()))
    db = env['db']
    drama = db.get(Drama, local['drama_id'])
    episode = db.scalar(select(Episode).where(Episode.drama_id == drama.id))
    drama.status = 'in_review'
    db.commit()
    drama.status = 'published'
    drama.coin_price_per_episode = 23
    episode.status = 'published'
    buyer = User(tenant_id=drama.tenant_id, email='dated-purchase@example.test', display_name='Buyer')
    db.add(buyer)
    db.flush()
    db.add(WalletAccount(tenant_id=drama.tenant_id, user_id=buyer.id, balance=100))
    db.commit()
    db.info['content_tenant_id'] = drama.tenant_id
    result = unlock_episode(db, user_id=buyer.id, episode_id=episode.id, idempotency_key='dated-unlock')
    assert result.status == 'unlocked' and result.balance_after == 77
    assert unlock_episode(db, user_id=buyer.id, episode_id=episode.id, idempotency_key='dated-unlock').replayed is True
    rows = list(db.scalars(select(AuditLog).where(AuditLog.action == 'episode_coin_unlock', AuditLog.target_id == episode.id)))
    assert len(rows) == 1
    assert rows[0].tenant_id == drama.tenant_id and rows[0].actor_id == buyer.id
    assert datetime.fromisoformat(rows[0].payload['watch_until']) == watches
    assert datetime.fromisoformat(rows[0].payload['sales_until']) == sales
    assert rows[0].payload['library_version'] == drama.library_version
    assert rows[0].payload['coin_price'] == 23


def subtitle_revision(env, title, episode, tracks):
    changed = ok(env['client'].patch(f"{PLATFORM}/{title['id']}/episodes/{episode['id']}",
                 json=dict(version=title['version'], subtitles=tracks), headers=env['pheaders']))
    return ok(env['client'].post(f"{PLATFORM}/{title['id']}/approve", json=dict(version=changed['version']), headers=env['pheaders']))


def test_central_subtitles_freeze_and_distribute_to_two_sites_without_upload(library_env, monkeypatch):
    from app.modules.content.models import EpisodeSubtitle
    from app.modules.playback.service import authorize
    env = library_env
    title, central_episode, _ = approved(env)
    tracks = [dict(language='EN', label='English', url='https://captions.example.test/master-en.vtt'),
              dict(language='hi', url='/subs/master-hi.vtt')]
    title = subtitle_revision(env, title, central_episode, tracks)
    normalized = [dict(language='en', label='English', url='https://captions.example.test/master-en.vtt'),
                  dict(language='hi', label='hi', url='/subs/master-hi.vtt')]
    assert title['episodes'][0]['subtitles'] == normalized
    def no_upload(*args, **kwargs):
        raise AssertionError('Distribution must reference existing subtitles without uploading')
    monkeypatch.setattr(bunny, 'storage_upload', no_upload)
    local = [imported(env, title, grant(env, title, index), index) for index in (0, 1)]
    db = env['db']
    episode_ids = []
    for item in local:
        ep = db.scalar(select(Episode).where(Episode.drama_id == item['drama_id']))
        episode_ids.append(ep.id)
        rows = list(db.scalars(select(EpisodeSubtitle).where(EpisodeSubtitle.episode_id == ep.id).order_by(EpisodeSubtitle.language)))
        assert [(row.language, row.label, row.url, row.is_default) for row in rows] == [
            ('en', 'English', normalized[0]['url'], True), ('hi', 'hi', normalized[1]['url'], False)]
        assert ep.subtitle_url == normalized[0]['url']
    assert episode_ids[0] != episode_ids[1]
    tenant_detail = ok(env['client'].get(f"{TENANT}/{title['id']}", headers=env['headers'][0]))
    assert tenant_detail['episodes'][0]['subtitles'] == normalized
    drama = db.get(Drama, local[0]['drama_id'])
    ep = db.get(Episode, episode_ids[0])
    drama.status = 'in_review'
    db.commit()
    drama.status = 'published'
    drama.free_episode_count = 1
    ep.status = 'published'
    db.commit()
    db.info['content_tenant_id'] = drama.tenant_id
    playback = authorize(db, drama_id=drama.id, episode_id=ep.id, user_id=None)
    assert playback['allowed'] is True
    assert [row['url'] for row in playback['subtitles']] == [track['url'] for track in normalized]
    assert playback['subtitle']['url'] == normalized[0]['url']


def test_new_subtitle_revision_does_not_overwrite_old_site_edits(library_env):
    from app.modules.content.models import EpisodeSubtitle
    env = library_env
    title, central_episode, _ = approved(env)
    original = [dict(language='en', label='Original', url='https://captions.example.test/old.vtt')]
    title = subtitle_revision(env, title, central_episode, original)
    first, second = imported(env, title, grant(env, title)), imported(env, title, grant(env, title, 1), 1)
    db = env['db']
    old_episode = db.scalar(select(Episode).where(Episode.drama_id == first['drama_id']))
    second_episode = db.scalar(select(Episode).where(Episode.drama_id == second['drama_id']))
    old_track = db.get(EpisodeSubtitle, (old_episode.id, 'en'))
    old_track.url = '/subs/alpha-independent-edit.vtt'
    old_episode.subtitle_url = old_track.url
    db.commit()
    title = subtitle_revision(env, title, central_episode,
                              [dict(language='en', label='Corrected', url='https://captions.example.test/new.vtt')])
    row = grant(env, title)
    unchanged = imported(env, title, row)
    assert unchanged == dict(first, created=False)
    adopted = ok(env['client'].post(f"{TENANT}/{title['id']}/import",
                 json=dict(grant_id=row['id'], adopt_new_version=True), headers=env['headers'][0]))
    db.expire_all()
    assert db.get(EpisodeSubtitle, (old_episode.id, 'en')).url == '/subs/alpha-independent-edit.vtt'
    assert db.get(EpisodeSubtitle, (second_episode.id, 'en')).url == original[0]['url']
    new_episode = db.scalar(select(Episode).where(Episode.drama_id == adopted['drama_id']))
    assert new_episode.id != old_episode.id
    assert db.get(EpisodeSubtitle, (new_episode.id, 'en')).url == 'https://captions.example.test/new.vtt'
    assert new_episode.status == 'in_review'


def test_central_subtitle_create_and_patch_reject_unsafe_or_duplicate_tracks(library_env):
    env = library_env
    title, ep, _ = approved(env)
    invalid = [
        [dict(language='en', url='http://captions.example.test/insecure.vtt')],
        [dict(language='en', url='//captions.example.test/protocol-relative.vtt')],
        [dict(language='en', url='javascript:alert(1)')],
        [dict(language='en', url='https://user:password@captions.example.test/private.vtt')],
        [dict(language='not a language', url='/subs/en.vtt')],
        [dict(language='en', url='/subs/a.vtt'), dict(language='EN', url='/subs/b.vtt')],
    ]
    path = f"{PLATFORM}/{title['id']}/episodes/{ep['id']}"
    for tracks in invalid:
        response = env['client'].patch(path, json=dict(version=title['version'], subtitles=tracks), headers=env['pheaders'])
        assert response.status_code == 422, response.text
    created = ok(env['client'].post(f"{PLATFORM}/{title['id']}/episodes",
            json=dict(episode_number=2, subtitles=[dict(language='zh-Hant', url='/subs/zh-hant.vtt')]), headers=env['pheaders']), 201)
    assert created['subtitles'] == [dict(language='zh-hant', label='zh-hant', url='/subs/zh-hant.vtt')]


def test_grant_language_filters_import_preview_and_later_local_subtitles(library_env):
    from app.modules.content.models import EpisodeSubtitle
    from app.modules.playback.service import authorize
    env = library_env
    title, central_episode, _ = approved(env)
    # The first master subtitle is Hindi, while the video itself is English.
    title = subtitle_revision(env, title, central_episode,
        [dict(language='hi', url='https://captions.example.test/hi.vtt'),
         dict(language='en', url='https://captions.example.test/en.vtt')])
    restricted = grant(env, title, languages=['en'])
    unrestricted = grant(env, title, 1)
    first, second = imported(env, title, restricted), imported(env, title, unrestricted, 1)
    db = env['db']
    alpha_episode = db.scalar(select(Episode).where(Episode.drama_id == first['drama_id']))
    alpha_tracks = list(db.scalars(select(EpisodeSubtitle).where(EpisodeSubtitle.episode_id == alpha_episode.id)))
    assert len(alpha_tracks) == 1 and alpha_tracks[0].language == 'en' and alpha_tracks[0].is_default
    assert alpha_episode.subtitle_url == 'https://captions.example.test/en.vtt'
    detail = ok(env['client'].get(f"{TENANT}/{title['id']}", headers=env['headers'][0]))
    assert [track['language'] for track in detail['episodes'][0]['subtitles']] == ['en']
    # Narrowing a grant also filters rows already imported under the old grant,
    # including independent site uploads, without rejecting the permitted video.
    row = ok(env['client'].patch(f"{PLATFORM}/{title['id']}/grants/{unrestricted['id']}",
             json=dict(version=unrestricted['version'], languages=['en']), headers=env['pheaders']))
    assert row['languages'] == ['en']
    drama = db.get(Drama, second['drama_id'])
    episode = db.scalar(select(Episode).where(Episode.drama_id == drama.id))
    drama.status = 'in_review'
    db.commit()
    drama.status = 'published'
    drama.free_episode_count = 1
    episode.status = 'published'
    db.add(EpisodeSubtitle(episode_id=episode.id, language='zh', label='Local Chinese',
                          url='/subs/local-zh.vtt', is_default=False))
    db.commit()
    db.info['content_tenant_id'] = drama.tenant_id
    playback = authorize(db, drama_id=drama.id, episode_id=episode.id, user_id=None)
    assert playback['allowed'] is True
    assert playback['subtitles'] == [dict(language='en', label='en', url='https://captions.example.test/en.vtt', is_default=True)]
    assert playback['subtitle']['url'] == 'https://captions.example.test/en.vtt'


@pytest.mark.parametrize('partial', [False, True])
def test_abandoned_upload_is_retryable_and_late_writes_cannot_revive_it(library_env, monkeypatch, partial):
    from app.modules.common.models import AuditLog
    env = library_env
    title, episode, upload = library_title(env, ready=False)
    client, headers = env['client'], env['pheaders']
    if partial:
        assert client.patch(upload['upload_url'], content=b'ab', headers=dict(headers, **{'Upload-Offset': '0'})).status_code == 204
    abandoned = ok(client.post(f"{PLATFORM}/assets/{upload['asset_id']}/abandon", json={}, headers=headers))
    assert abandoned['status'] == 'failed' and abandoned['abandoned'] is True
    again = ok(client.post(f"{PLATFORM}/assets/{upload['asset_id']}/abandon", json={}, headers=headers))
    assert again['already_abandoned'] is True
    replacement = ok(client.post(f"{PLATFORM}/{title['id']}/episodes/{episode['id']}/upload-session",
        json=dict(filename='retry.mp4', mime='video/mp4', size_bytes=4, width=720, height=1280), headers=headers))
    assert replacement['asset_id'] != upload['asset_id'] and replacement['video_id'] != upload['video_id']
    assert client.patch(upload['upload_url'], content=b'cd' if partial else b'abcd',
        headers=dict(headers, **{'Upload-Offset': '2' if partial else '0'})).status_code == 409
    assert client.post(upload['upload_url'], headers=headers).status_code == 409
    # Old provider status reports must not resurrect a locally abandoned row.
    monkeypatch.setenv('BUNNY_MODE', 'real')
    def forbidden_provider_call(*args, **kwargs):
        raise AssertionError('terminal uploads must not be reconciled or deleted')
    monkeypatch.setattr(bunny, 'stream_video', forbidden_provider_call)
    monkeypatch.setattr(bunny, 'delete_stream_video', forbidden_provider_call)
    old = ok(client.post(f"{PLATFORM}/assets/{upload['asset_id']}/sync-status", headers=headers))
    assert old['status'] == 'failed'
    monkeypatch.setenv('BUNNY_MODE', 'fake')
    assert client.patch(replacement['upload_url'], content=b'wxyz', headers=dict(headers, **{'Upload-Offset': '0'})).status_code == 204
    result = ok(client.get(f"{PLATFORM}/{title['id']}", headers=headers))
    assert result['episodes'][0]['media_asset_id'] == replacement['asset_id']
    assert result['episodes'][0]['asset']['status'] == 'ready'
    approved_result = ok(client.post(f"{PLATFORM}/{title['id']}/approve", json={'version': result['version']}, headers=headers))
    assert approved_result['status'] == 'approved'
    env['db'].expire_all()
    assert env['db'].get(MediaAsset, upload['asset_id']).status == 'failed'
    assert env['db'].scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == 'library_upload_abandoned')) == 1


def test_ready_asset_cannot_be_abandoned_even_before_approval(library_env):
    env = library_env
    _, _, upload = library_title(env)
    response = env['client'].post(f"{PLATFORM}/assets/{upload['asset_id']}/abandon", json={}, headers=env['pheaders'])
    assert response.status_code == 409 and response.json()['detail']['code'] == 'library_upload_not_cancellable'
    env['db'].expire_all()
    assert env['db'].get(MediaAsset, upload['asset_id']).status == 'ready'


def test_abandon_checks_real_provider_completion_and_fails_closed_on_outage(library_env, monkeypatch):
    env = library_env
    _, _, upload = library_title(env, ready=False)
    monkeypatch.setenv('BUNNY_MODE', 'real')
    def unavailable(*args, **kwargs):
        raise bunny.BunnyError('temporary provider outage')
    monkeypatch.setattr(bunny, 'stream_video', unavailable)
    response = env['client'].post(f"{PLATFORM}/assets/{upload['asset_id']}/abandon", json={}, headers=env['pheaders'])
    assert response.status_code == 502
    env['db'].expire_all()
    assert env['db'].get(MediaAsset, upload['asset_id']).status == 'created'
    monkeypatch.setattr(bunny, 'stream_video', lambda video_id: dict(guid=video_id, status=4, length=61, width=720, height=1280))
    response = env['client'].post(f"{PLATFORM}/assets/{upload['asset_id']}/abandon", json={}, headers=env['pheaders'])
    assert response.status_code == 409 and response.json()['detail']['code'] == 'library_upload_not_cancellable'
    env['db'].expire_all()
    assert env['db'].get(MediaAsset, upload['asset_id']).status == 'ready'


def test_live_incomplete_upload_can_be_abandoned_without_deleting_provider_media(library_env, monkeypatch):
    env = library_env
    _, _, upload = library_title(env, ready=False)
    monkeypatch.setenv('BUNNY_MODE', 'real')
    monkeypatch.setattr(bunny, 'stream_video', lambda video_id: dict(guid=video_id, status=3))
    def forbidden_delete(*args, **kwargs):
        raise AssertionError('abandon must not delete provider resources')
    monkeypatch.setattr(bunny, 'delete_stream_video', forbidden_delete)
    result = ok(env['client'].post(f"{PLATFORM}/assets/{upload['asset_id']}/abandon", json={}, headers=env['pheaders']))
    assert result['status'] == 'failed'


def test_approved_snapshot_asset_cannot_be_abandoned_after_state_regression(library_env):
    env = library_env
    _, _, upload = approved(env)
    asset = env['db'].get(MediaAsset, upload['asset_id'])
    asset.status = 'transcoding'
    env['db'].commit()
    response = env['client'].post(f"{PLATFORM}/assets/{upload['asset_id']}/abandon", json={}, headers=env['pheaders'])
    assert response.status_code == 409 and response.json()['detail']['code'] == 'library_media_approved'
    env['db'].expire_all()
    assert env['db'].get(MediaAsset, upload['asset_id']).status == 'transcoding'


def test_other_site_media_reference_blocks_abandon_without_a_snapshot(library_env):
    env = library_env
    _, episode, upload = library_title(env, ready=False)
    site = env['sites'][1]
    local = Drama(tenant_id=site.id, title='Other site reference', status='draft')
    env['db'].add(local)
    env['db'].flush()
    env['db'].add(Episode(tenant_id=site.id, drama_id=local.id, episode_number=1,
        library_episode_id=episode['id'], media_asset_id=upload['asset_id'], bunny_video_id=upload['video_id'], status='in_review'))
    env['db'].commit()
    response = env['client'].post(f"{PLATFORM}/assets/{upload['asset_id']}/abandon", json={}, headers=env['pheaders'])
    assert response.status_code == 409 and response.json()['detail']['code'] == 'library_media_in_use'
    env['db'].expire_all()
    assert env['db'].get(MediaAsset, upload['asset_id']).status == 'created'


def test_upload_recovery_requires_platform_admin_and_rejects_client_status(library_env):
    env = library_env
    _, _, upload = library_title(env, ready=False)
    endpoint = f"{PLATFORM}/assets/{upload['asset_id']}/abandon"
    assert env['client'].post(endpoint, json={}).status_code == 401
    for headers in env['headers']:
        assert env['client'].post(endpoint, json={}, headers=headers).status_code == 403
    response = env['client'].post(endpoint, json={'status': 'ready'}, headers=env['pheaders'])
    assert response.status_code == 422
    env['db'].expire_all()
    assert env['db'].get(MediaAsset, upload['asset_id']).status == 'created'


def test_concurrent_retry_after_abandon_creates_only_one_new_upload(library_env):
    from concurrent.futures import ThreadPoolExecutor
    env = library_env
    title, episode, upload = library_title(env, ready=False)
    ok(env['client'].post(f"{PLATFORM}/assets/{upload['asset_id']}/abandon", json={}, headers=env['pheaders']))
    endpoint = f"{PLATFORM}/{title['id']}/episodes/{episode['id']}/upload-session"
    def retry():
        return env['client'].post(endpoint, json=dict(filename='retry.mp4', mime='video/mp4', size_bytes=4, width=720, height=1280), headers=env['pheaders'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: retry(), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    env['db'].expire_all()
    assert env['db'].scalar(select(func.count()).select_from(MediaAsset)) == 2



def test_sync_refreshes_a_stale_created_object_after_abandon(library_env, monkeypatch):
    from app.modules.library import service
    env = library_env
    _, _, upload = library_title(env, ready=False)
    stale = env['db'].get(MediaAsset, upload['asset_id'])
    assert stale.status == 'created'
    ok(env['client'].post(f"{PLATFORM}/assets/{upload['asset_id']}/abandon", json={}, headers=env['pheaders']))
    assert stale.status == 'created'  # A genuinely stale SQLAlchemy identity map.
    monkeypatch.setenv('BUNNY_MODE', 'real')
    def forbidden_remote(*args, **kwargs):
        raise AssertionError('refresh must observe terminal state before provider lookup')
    monkeypatch.setattr(bunny, 'stream_video', forbidden_remote)
    result = service.sync_asset(env['db'], stale)
    assert result['status'] == 'failed' and stale.status == 'failed'
    env['db'].rollback()


def test_sync_provider_lookup_and_abandon_are_serialized(library_env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    env = library_env
    _, _, upload = library_title(env, ready=False)
    lookup_started, finish_lookup = Event(), Event()
    monkeypatch.setenv('BUNNY_MODE', 'real')
    def remote(video_id):
        lookup_started.set()
        assert finish_lookup.wait(10), 'provider fixture was not released'
        return dict(guid=video_id, status=4, length=60, width=720, height=1280)
    monkeypatch.setattr(bunny, 'stream_video', remote)
    with ThreadPoolExecutor(max_workers=2) as pool:
        sync = pool.submit(env['client'].post, f"{PLATFORM}/assets/{upload['asset_id']}/sync-status", headers=env['pheaders'])
        assert lookup_started.wait(10)
        abandon = pool.submit(env['client'].post, f"{PLATFORM}/assets/{upload['asset_id']}/abandon", json={}, headers=env['pheaders'])
        finish_lookup.set()
        assert sync.result(timeout=15).status_code == 200
        cancelled = abandon.result(timeout=15)
    assert cancelled.status_code == 409 and cancelled.json()['detail']['code'] == 'library_upload_not_cancellable'
    env['db'].expire_all()
    assert env['db'].get(MediaAsset, upload['asset_id']).status == 'ready'



def test_large_country_grants_import_to_two_sites_and_keep_region_intersection(library_env):
    env = library_env
    countries = 'US CA MX BR AR CL CO PE UY PY BO EC CR PA GT HN SV NI DO CU JM HT TT BS BB BZ GB IE FR DE ES PT IT NL BE LU CH AT DK SE NO FI IS PL CZ SK HU RO BG HR SI RS BA ME AL MK GR TR CY MT EE LV LT UA MD GE AM AZ KZ UZ IN PK BD LK NP BT CN JP KR MN SG MY TH VN ID PH AU NZ'.split()
    assert len(set(countries)) >= 68 and len(','.join(countries)) > 200
    title, _, upload = approved(env)
    locals = [imported(env, title, grant(env, title, index, countries=countries), index) for index in range(2)]
    assert locals[0]['drama_id'] != locals[1]['drama_id']
    db = env['db']
    db.expire_all()
    for index, result in enumerate(locals):
        db.info['content_tenant_id'] = env['sites'][index].id
        drama = db.get(Drama, result['drama_id'])
        assert drama.country_allowlist == ''
        assert db.scalar(select(Episode.media_asset_id).where(Episode.drama_id == drama.id)) == upload['asset_id']
        db.info['content_tenant_id'] = env['sites'][index].id
        for purpose in ('play', 'sale'):
            db.info['viewer_country'] = 'US'
            assert_distribution_allowed(db, drama, purpose=purpose)
            for denied in ('ZA', None):
                db.info['viewer_country'] = denied
                with pytest.raises(HTTPException) as blocked:
                    assert_distribution_allowed(db, drama, purpose=purpose)
                assert blocked.value.detail['code'] == 'distribution_region_blocked'
        # Site-local geography remains an additional AND restriction, never a
        # replacement capable of expanding the central grant's countries.
        drama.country_allowlist = 'US,ZA'
        db.commit()
        db.info['viewer_country'] = 'US'
        assert_distribution_allowed(db, drama)
        db.info['viewer_country'] = 'CA'
        with pytest.raises(HTTPException) as local_block:
            assert_distribution_allowed(db, drama)
        assert local_block.value.detail['code'] == 'region_blocked'
        db.info['viewer_country'] = 'ZA'
        with pytest.raises(HTTPException) as central_block:
            assert_distribution_allowed(db, drama)
        assert central_block.value.detail['code'] == 'distribution_region_blocked'



def test_abandon_new_transfer_keeps_an_approved_imported_asset_unchanged(library_env):
    env = library_env
    title, episode, original = approved(env)
    local = imported(env, title, grant(env, title))
    replacement = ok(env['client'].post(f"{PLATFORM}/{title['id']}/episodes/{episode['id']}/upload-session",
        json=dict(filename='new-draft.mp4', mime='video/mp4', size_bytes=4, width=720, height=1280), headers=env['pheaders']))
    ok(env['client'].post(f"{PLATFORM}/assets/{replacement['asset_id']}/abandon", json={}, headers=env['pheaders']))
    env['db'].expire_all()
    assert env['db'].get(MediaAsset, original['asset_id']).status == 'ready'
    assert env['db'].get(MediaAsset, replacement['asset_id']).status == 'failed'
    site_episode = env['db'].scalar(select(Episode).where(Episode.drama_id == local['drama_id']))
    assert site_episode.media_asset_id == original['asset_id']
    snapshot = env['db'].scalar(select(LibraryRevision).where(LibraryRevision.library_title_id == title['id'], LibraryRevision.version == title['version']))
    assert snapshot.snapshot['episodes'][0]['media_asset_id'] == original['asset_id']


def set_library_gate(env, source, enabled):
    from app.modules.sites.models import SiteSettings
    from app.modules.tenancy.models import TenantModule
    db, site = env['db'], env['sites'][0]
    db.expire_all()
    if source == 'feature':
        settings = db.get(SiteSettings, site.id)
        settings.features = dict(settings.features or {}, library=enabled)
    else:
        db.get(TenantModule, (site.id, 'dramas')).enabled = enabled
    db.commit()


@pytest.mark.parametrize('source', ['feature', 'module'])
@pytest.mark.parametrize('adopt', [False, True])
def test_new_import_respects_effective_library_gate_without_affecting_other_site(library_env, source, adopt):
    env = library_env
    title, _, _ = approved(env)
    set_library_gate(env, source, False)
    # Platform preallocation remains available while site imports are disabled.
    first_grant, second_grant = grant(env, title), grant(env, title, 1)
    body = dict(grant_id=first_grant['id'], adopt_new_version=adopt)
    denied = env['client'].post(f"{TENANT}/{title['id']}/import", json=body, headers=env['headers'][0])
    assert denied.status_code == 403, denied.text
    if source == 'feature':
        assert denied.json()['detail']['code'] == 'feature_disabled'
    db = env['db']
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Drama)) == 0
    assert db.scalar(select(func.count()).select_from(Episode)) == 0
    # Service callers must use the same effective gate as the HTTP entry point.
    from app.modules.library.service import import_title
    with pytest.raises(HTTPException) as direct:
        import_title(db, tenant=env['sites'][0], title_id=title['id'], grant_id=first_grant['id'],
                     actor=env['owners'][0], overrides={}, adopt_new_version=adopt)
    assert direct.value.status_code == 403
    assert direct.value.detail['code'] == 'feature_disabled'
    db.rollback()
    other = imported(env, title, second_grant, 1)
    assert other['created'] is True
    set_library_gate(env, source, True)
    enabled = ok(env['client'].post(f"{TENANT}/{title['id']}/import", json=body, headers=env['headers'][0]))
    replay = ok(env['client'].post(f"{TENANT}/{title['id']}/import", json=body, headers=env['headers'][0]))
    assert enabled['created'] is True and enabled['drama_id'] != other['drama_id']
    assert replay == dict(enabled, created=False)


@pytest.mark.parametrize('source', ['feature', 'module'])
def test_library_gate_blocks_reimport_and_adoption_but_preserves_published_edition(library_env, source):
    from app.modules.playback.service import authorize
    from app.modules.common.models import AuditLog
    env = library_env
    title, _, _ = approved(env)
    first_grant = grant(env, title)
    first = imported(env, title, first_grant)
    db = env['db']
    drama = db.get(Drama, first['drama_id'])
    episode = db.scalar(select(Episode).where(Episode.drama_id == drama.id))
    drama.status = 'in_review'
    db.commit()
    drama.status, drama.free_episode_count, episode.status = 'published', 1, 'published'
    db.commit()
    old_ids = drama.id, episode.id, drama.library_version
    title = ok(env['client'].patch(f"{PLATFORM}/{title['id']}",
               json=dict(version=title['version'], title='New Edition'), headers=env['pheaders']))
    title = ok(env['client'].post(f"{PLATFORM}/{title['id']}/approve",
               json=dict(version=title['version']), headers=env['pheaders']))
    new_grant = grant(env, title)
    set_library_gate(env, source, False)
    before_audits = db.scalar(select(func.count()).select_from(AuditLog))
    # A disabled import action consistently returns 403, including no-op replays.
    for row, adopt in ((first_grant, False), (new_grant, False), (new_grant, True)):
        denied = env['client'].post(f"{TENANT}/{title['id']}/import",
                 json=dict(grant_id=row['id'], adopt_new_version=adopt), headers=env['headers'][0])
        assert denied.status_code == 403, denied.text
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Drama)) == 1
    assert db.scalar(select(func.count()).select_from(Episode)) == 1
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before_audits
    drama, episode = db.get(Drama, old_ids[0]), db.get(Episode, old_ids[1])
    assert (drama.status, episode.status, drama.library_version) == ('published', 'published', old_ids[2])
    db.info['content_tenant_id'] = drama.tenant_id
    assert authorize(db, drama_id=drama.id, episode_id=episode.id, user_id=None)['allowed'] is True
    db.info.pop('content_tenant_id', None)
    db.rollback()
    set_library_gate(env, source, True)
    assert imported(env, title, first_grant) == dict(first, created=False)
    adopted = ok(env['client'].post(f"{TENANT}/{title['id']}/import",
                 json=dict(grant_id=new_grant['id'], adopt_new_version=True), headers=env['headers'][0]))
    assert adopted['created'] is True and adopted['drama_id'] != old_ids[0]
