"""Public comment writes obey site capabilities before moderation or persistence."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.account import service as accounts
from app.modules.common.models import AuditLog
from app.modules.sites.models import SiteSettings
from app.modules.social import moderation
from app.modules.social.models import Comment, InboxMessage
from app.modules.tenancy.consumer_scope import site_context
from app.modules.tenancy.models import Tenant, TenantDomain, TenantModule
from app.modules.tenancy.permissions import seed_tenant_defaults

OTHER = 'comment-site'


@pytest.fixture
def threads(session, make, monkeypatch):
    site = Tenant(id=OTHER, code=OTHER, name='Comment site', kind='native', status='active')
    session.add(site); seed_tenant_defaults(session, site); session.flush()
    session.add(TenantDomain(tenant_id=OTHER, hostname='comments.example', kind='site', status='active', verified_at=datetime.now(timezone.utc)))
    session.add_all([SiteSettings(tenant_id='jjtv'), SiteSettings(tenant_id=OTHER)])
    session.commit()
    items = {}; jobs = []
    monkeypatch.setattr(moderation, 'process_comment', lambda identifier: jobs.append(identifier))
    for owner in ['jjtv', OTHER]:
        with site_context(session, owner):
            user = make.user(email='commenter@example.com')
            drama = make.drama(title=owner, status='published')
            episode = make.episode(drama, status='published')
            host = 'https://testserver' if owner == 'jjtv' else 'https://comments.example'
            items[owner] = (user, episode, TestClient(app, base_url=host), {'Authorization': 'Bearer '+accounts._issue_session(user)})
    return items, jobs


def set_comments(session, owner, via):
    session.get(TenantModule, (owner, 'comments')).enabled = via != 'module'
    session.get(SiteSettings, owner).features = {'comments': via != 'feature'}
    session.commit()


def create(client, headers, episode, alias):
    body = {'body': '  Current episode comment  ', 'tenant_id': 'jjtv'}
    if alias:
        body.update(episode_id=episode.id, drama_id=episode.drama_id)
    return client.post('/v1/comments' if alias else f'/v1/episodes/{episode.id}/comments', json=body, headers=headers)


def no_comment_side_effects(session, jobs):
    session.expire_all()
    for model in [Comment, AuditLog, InboxMessage]:
        assert session.scalar(select(func.count()).select_from(model)) == 0
    assert jobs == []


@pytest.mark.parametrize('owner', ['jjtv', OTHER])
@pytest.mark.parametrize('via', ['module', 'feature'])
@pytest.mark.parametrize('alias', [False, True])
def test_disabled_comment_creation_denies_both_aliases_before_queue_or_writes(session, threads, monkeypatch, owner, via, alias):
    items, jobs = threads
    _user, episode, client, headers = items[owner]
    set_comments(session, owner, via)
    queue_reads = []
    monkeypatch.setattr(moderation, 'queue_state', lambda db: queue_reads.append(db.info.get('content_tenant_id')) or 'queued')
    response = create(client, headers, episode, alias)
    assert response.status_code == 403, response.text
    assert queue_reads == []
    no_comment_side_effects(session, jobs)


@pytest.mark.parametrize('alias', [False, True])
@pytest.mark.parametrize('automated', [False, True])
def test_enabled_legacy_comments_keep_pending_response_with_optional_moderation(session, threads, alias, automated):
    items, jobs = threads
    user, episode, client, headers = items['jjtv']
    with site_context(session, 'jjtv'):
        settings = moderation.get_settings(session)
        settings.enabled = automated
        moderation.set_api_key(settings, 'sk-comment-only-test')
        # Disabling the moderation module must not disable human-queue comments.
        session.get(TenantModule, ('jjtv', 'moderation')).enabled = automated
        session.commit()
    response = create(client, headers, episode, alias)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['status'] == 'pending' and payload['item']['mine'] and payload['item']['pending']
    assert payload['item']['body'] == 'Current episode comment'
    assert payload['moderation']['state'] == ('queued' if automated else 'disabled')
    session.expire_all()
    row = session.get(Comment, payload['id'])
    assert (row.tenant_id, row.user_id, row.episode_id, row.status) == ('jjtv', user.id, episode.id, 'pending')
    assert jobs == ([row.id] if automated else [])


@pytest.mark.parametrize('alias', [False, True])
def test_native_comment_creation_uses_its_site_and_cannot_target_foreign_content_or_token(session, threads, alias):
    items, jobs = threads
    user, episode, client, headers = items[OTHER]
    _other_user, foreign_episode, original_client, foreign_headers = items['jjtv']
    assert create(client, headers, foreign_episode, alias).status_code == 404
    assert create(client, foreign_headers, episode, alias).status_code == 401
    no_comment_side_effects(session, jobs)
    # Closing the original site's capability cannot disable an independent site.
    set_comments(session, 'jjtv', 'feature')
    created = create(client, headers, episode, alias)
    assert created.status_code == 200, created.text
    session.expire_all()
    row = session.get(Comment, created.json()['id'])
    assert (row.tenant_id, row.user_id, row.episode_id) == (OTHER, user.id, episode.id)
    assert create(original_client, foreign_headers, foreign_episode, alias).status_code == 403
    assert jobs == []


@pytest.mark.parametrize('via', ['module', 'feature'])
def test_disabled_new_comments_preserve_existing_thread_read_policy(session, threads, via):
    items, jobs = threads
    user, episode, client, headers = items[OTHER]
    with site_context(session, OTHER):
        session.add_all([
            Comment(user_id=user.id, drama_id=episode.drama_id, episode_id=episode.id, body='Published history', status='approved'),
            Comment(user_id=user.id, drama_id=episode.drama_id, episode_id=episode.id, body='My pending history', status='pending'),
        ]); session.commit()
    set_comments(session, OTHER, via)
    public = client.get(f'/v1/episodes/{episode.id}/comments')
    own = client.get(f'/v1/episodes/{episode.id}/comments', headers=headers)
    assert public.status_code == own.status_code == 200
    assert {item['body'] for item in public.json()['items']} == {'Published history'}
    assert {item['body'] for item in own.json()['items']} == {'Published history', 'My pending history'}
    assert jobs == []
