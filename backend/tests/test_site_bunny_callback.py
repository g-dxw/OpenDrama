"""Shared-API Bunny callbacks bind both tenant filters to the stored upload owner."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.common.models import AuditLog
from app.modules.content import upload_service
from app.modules.content.models import Episode, MediaUpload
from app.modules.tenancy.consumer_scope import site_context
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.permissions import seed_tenant_defaults


@pytest.fixture
def site_uploads(session, make):
    site = Tenant(id='upload-site', code='upload-site', name='Upload site', kind='native', status='active')
    session.add(site); seed_tenant_defaults(session, site); session.commit()
    items = {}
    for owner in ['jjtv', site.id]:
        with site_context(session, owner):
            drama = make.drama(title=owner)
            episode = make.episode(drama)
            result = upload_service.create_upload_session(session, episode=episode,
                filename='episode.mp4', mime='video/mp4', size_bytes=1000, width=720, height=1280)
            items[owner] = (episode.id, result['video_id'])
    return items


def callback(client, video_id, *, outcome='finished', signature=None, **claims):
    body = json.dumps(dict(video_id=video_id, status=outcome, **claims)).encode()
    return client.post('/v1/bunny/callback', content=body,
        headers={'X-Bunny-Signature': signature if signature is not None else upload_service.sign_callback(body)})


@pytest.mark.parametrize('owner', ['jjtv', 'upload-site'])
@pytest.mark.parametrize('outcome,status,upload_status', [('finished', 'in_review', 'completed'), ('failed', 'transcode_failed', 'failed')])
def test_signed_shared_api_callback_updates_only_stored_upload_owner(session, site_uploads, owner, outcome, status, upload_status):
    episode_id, video_id = site_uploads[owner]
    other = 'upload-site' if owner == 'jjtv' else 'jjtv'
    client = TestClient(app, base_url='https://api.example.com')
    # Host resolves JJTV. Signed body claims deliberately name the other site;
    # ownership must come from MediaUpload -> Episode, never these claims.
    response = callback(client, video_id, outcome=outcome, tenant_id=other, episode_id=site_uploads[other][0])
    assert response.status_code == 200, response.text
    assert response.json()['status'] == status and response.json()['replayed'] is False
    repeated = callback(client, video_id, outcome=outcome, tenant_id=other, episode_id=site_uploads[other][0])
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()['status'] == status
    if outcome == 'finished':
        assert repeated.json()['replayed'] is True
    session.expire_all()
    with site_context(session, owner):
        assert session.get(Episode, episode_id).status == status
        assert session.scalar(select(MediaUpload).where(MediaUpload.video_id == video_id)).status == upload_status
    with site_context(session, other):
        other_id, other_video = site_uploads[other]
        assert session.get(Episode, other_id).status == 'transcoding'
        assert session.scalar(select(MediaUpload).where(MediaUpload.video_id == other_video)).status == 'created'


def test_bad_signature_cannot_resolve_or_mutate_foreign_upload(session, site_uploads):
    episode_id, video_id = site_uploads['upload-site']
    response = callback(TestClient(app, base_url='https://api.example.com'), video_id, signature='invalid', tenant_id='upload-site')
    assert response.status_code == 401, response.text
    session.expire_all()
    with site_context(session, 'upload-site'):
        assert session.get(Episode, episode_id).status == 'transcoding'
        assert session.scalar(select(MediaUpload).where(MediaUpload.video_id == video_id)).status == 'created'
    rejection = session.scalar(select(AuditLog).where(AuditLog.action == 'bunny_callback_rejected'))
    assert rejection is not None and rejection.tenant_id == 'jjtv'


def test_callback_service_restores_callers_site_scope(session, site_uploads):
    episode_id, video_id = site_uploads['upload-site']
    body = json.dumps(dict(video_id=video_id, status='finished')).encode()
    with site_context(session, 'jjtv'):
        result = upload_service.apply_callback(session, body=body, signature=upload_service.sign_callback(body), video_id=video_id, outcome='finished')
        assert result['status'] == 'in_review'
        assert session.info['content_tenant_id'] == 'jjtv'
        assert not session.info.get('content_tenant_bypass') and not session.info.get('consumer_tenant_bypass')
        assert session.scalar(select(Episode).where(Episode.id == episode_id)) is None
