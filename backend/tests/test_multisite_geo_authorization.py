"""Public region authorization uses trusted proxy evidence, never client input."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from starlette.requests import Request

from app.modules.account.models import User
from app.modules.account import service as accounts
from app.modules.content.models import Drama, Episode
from app.modules.library.models import DistributionGrant
from app.modules.library.service import assert_distribution_allowed
from app.modules.sites.service import resolve_public_site
from app.modules.wallet.models import CoinLedgerEntry, Entitlement, WalletAccount
from tests.test_company_content_library import approved, grant, imported, library_env, ok


@pytest.fixture(params=['library', 'legacy'])
def regional(request, library_env, monkeypatch):
    env = library_env
    monkeypatch.setenv('SITE_TRUSTED_PROXIES', '')
    db, client = env['db'], env['client']
    if request.param == 'library':
        title, _, _ = approved(env)
        local = imported(env, title, grant(env, title, countries=['US']))
        drama = db.get(Drama, local['drama_id'])
        episode = db.scalar(select(Episode).where(Episode.drama_id == drama.id))
    else:
        drama = Drama(tenant_id=env['sites'][0].id, title='Legacy regional private title',
                      country_allowlist='US', status='draft')
        db.add(drama)
        db.flush()
        episode = Episode(tenant_id=drama.tenant_id, drama_id=drama.id, episode_number=1,
                          status='in_review', bunny_video_id='private-regional-video')
        db.add(episode)
    drama.free_episode_count = 1
    drama.coin_price_per_episode = 23
    db.commit()
    # Administrative publishing must not require the operator's viewer IP.
    ok(client.post(f'/admin/v1/dramas/{drama.id}/status', json={'to_status':'in_review'}, headers=env['headers'][0]))
    ok(client.post(f'/admin/v1/episodes/{episode.id}/publish', headers=env['headers'][0]))
    ok(client.post(f'/admin/v1/dramas/{drama.id}/status', json={'to_status':'published'}, headers=env['headers'][0]))
    db.expire_all()
    env.update(drama=db.get(Drama, drama.id), episode=db.get(Episode, episode.id),
               host={'Host':env['sites'][0].id + '.example.test'}, kind=request.param)
    return env


def playback(env, *, country=None, proxy_country=None):
    body = dict(drama_id=env['drama'].id, episode_id=env['episode'].id)
    if country is not None:
        body['country'] = country
    headers = dict(env['host'])
    if proxy_country is not None:
        headers['X-Site-Country'] = proxy_country
    return ok(env['client'].post('/v1/playback', json=body, headers=headers))


def test_untrusted_body_or_country_header_cannot_authorize_regional_playback(regional):
    for result in (playback(regional), playback(regional, country='US'),
                   playback(regional, country='US', proxy_country='US')):
        assert result['allowed'] is False
        assert result['reason'] in {'region_blocked','distribution_region_blocked'}
        assert 'url' not in result and 'subtitles' not in result


def test_trusted_proxy_country_wins_over_conflicting_body_and_missing_is_denied(regional, monkeypatch):
    monkeypatch.setenv('SITE_TRUSTED_PROXIES', 'testclient')
    assert playback(regional, country='US', proxy_country='IN')['allowed'] is False
    assert playback(regional, country='IN', proxy_country='US')['allowed'] is True
    assert playback(regional, country='US')['allowed'] is False
    assert playback(regional, country='US', proxy_country='USA')['allowed'] is False


def test_regional_coin_purchase_requires_trusted_country_and_never_debits_on_denial(regional, monkeypatch):
    env, db = regional, regional['db']
    env['drama'].free_episode_count = 0
    buyer = User(tenant_id=env['drama'].tenant_id, email='geo-buyer@example.test', display_name='Regional buyer')
    db.add(buyer)
    db.flush()
    db.add(WalletAccount(tenant_id=buyer.tenant_id, user_id=buyer.id, balance=100))
    db.commit()
    token = accounts._issue_session(buyer)
    headers = dict(env['host'], Authorization='Bearer ' + token, **{'Idempotency-Key':'geo-purchase'})
    body = dict(episode_id=env['episode'].id, country='US')
    denied = env['client'].post('/v1/unlock', json=body, headers=dict(headers, **{'X-Site-Country':'US'}))
    assert denied.status_code == 403, denied.text
    db.expire_all()
    assert db.get(WalletAccount, buyer.id).balance == 100
    assert db.scalar(select(func.count()).select_from(Entitlement).where(Entitlement.user_id == buyer.id)) == 0
    assert db.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.user_id == buyer.id)) == 0
    monkeypatch.setenv('SITE_TRUSTED_PROXIES', 'testclient')
    wrong = env['client'].post('/v1/unlock', json=body, headers=dict(headers, **{'X-Site-Country':'IN'}))
    assert wrong.status_code == 403, wrong.text
    allowed = ok(env['client'].post('/v1/unlock', json=dict(body, country='IN'), headers=dict(headers, **{'X-Site-Country':'US'})))
    assert allowed['status'] == 'unlocked' and allowed['balance_after'] == 77


def test_country_argument_is_not_a_service_authorization_fallback(regional):
    db, drama = regional['db'], regional['drama']
    db.info['content_tenant_id'] = drama.tenant_id
    db.info.pop('viewer_country', None)
    with pytest.raises(HTTPException) as denied:
        assert_distribution_allowed(db, drama, purpose='play', country='US', episode=regional['episode'])
    assert denied.value.status_code == 403


def test_unrestricted_content_still_plays_without_geography(regional):
    db, drama = regional['db'], regional['drama']
    drama.country_allowlist = ''
    if drama.distribution_grant_id is not None:
        db.get(DistributionGrant, drama.distribution_grant_id).countries = []
    db.commit()
    assert playback(regional)['allowed'] is True


def test_admin_publication_schedule_does_not_require_viewer_country(regional):
    env = regional
    path = '/admin/v1/dramas/' + str(env['drama'].id)
    ok(env['client'].post(path + '/status', json={'to_status':'unpublished'}, headers=env['headers'][0]))
    ok(env['client'].post(path + '/schedule', json={'at':(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}, headers=env['headers'][0]))
    ok(env['client'].post('/admin/v1/schedules/apply', headers=env['headers'][0]))
    env['db'].expire_all()
    assert env['db'].get(Drama, env['drama'].id).status == 'published'


def test_resolver_clears_previous_trusted_country_on_untrusted_request(library_env, monkeypatch):
    env, db = library_env, library_env['db']
    monkeypatch.setenv('SITE_TRUSTED_PROXIES', '10.20.30.40')
    def request(client):
        return Request(dict(type='http', method='GET', path='/v1/site/bootstrap', scheme='https',
            server=('site.example.test',443), client=(client,1234), query_string=b'',
            headers=[(b'host',(env['sites'][0].id + '.example.test').encode()), (b'x-site-country',b'US')]))
    resolve_public_site(db, request('10.20.30.40'))
    assert db.info['viewer_country'] == 'US'
    resolve_public_site(db, request('untrusted-client'))
    assert db.info['viewer_country'] is None and db.info['trusted_country'] is None
