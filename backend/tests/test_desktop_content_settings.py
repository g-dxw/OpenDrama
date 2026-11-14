from types import SimpleNamespace
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.modules.content.models import Drama,Episode,EpisodeSubtitle
from app.modules.content.pricing import episode_access
from app.modules.ops.managed_settings import ManagedSetting,get_config,save_config,public_config,login_runtime
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.permissions import seed_tenant_defaults

@pytest.mark.parametrize('mode,member,pays,unlocked,number,expected',[
 ('public',False,False,False,1,True),('public',False,False,False,2,False),
 ('public',True,False,False,2,True),('public',True,True,False,2,False),
 ('public',True,True,True,2,True),('members',False,False,True,2,False),
 ('members',False,False,False,1,True),('members',True,False,False,2,True),
 ('members',True,True,False,2,False),('members',True,True,True,2,True)])
def test_policy_matrix(mode,member,pays,unlocked,number,expected):
 drama=SimpleNamespace(free_episode_count=1,non_member_free_episodes=None,coin_price_per_episode=10,access_mode=mode,member_pays_coins=pays)
 episode=SimpleNamespace(episode_number=number,coin_price=3)
 result=episode_access(drama,episode,member=member,unlocked=unlocked)
 assert result['allowed']==expected
 assert result['coin_price']==3

def login(client,person):
 r=client.post('/tenant/v1/auth/login',json=dict(tenant_code=person._test_tenant_code,username=person.username,password='pass-1234'))
 assert r.status_code==200,r.text
 return {'Authorization':'Bearer '+r.json()['token']}

def test_encrypted_credentials_blank_and_clear(session,monkeypatch):
 monkeypatch.setenv('SETTINGS_ENCRYPTION_KEY',Fernet.generate_key().decode())
 save_config(session,'test',dict(key='private-secret',enabled=True),secret_fields=['key']);session.commit()
 row=session.get(ManagedSetting,'test')
 assert 'private-secret' not in str(row.secrets)
 assert public_config(session,'test',secret_fields=['key'])['key_configured']
 assert 'key' not in public_config(session,'test',secret_fields=['key'])
 save_config(session,'test',dict(key=''),secret_fields=['key'])
 assert get_config(session,'test')['key']=='private-secret'
 save_config(session,'test',dict(clear_key=True),secret_fields=['key'])
 assert get_config(session,'test',{'key':'environment-secret'})['key']==''

def test_secrets_require_explicit_key(session,monkeypatch):
 monkeypatch.delenv('SETTINGS_ENCRYPTION_KEY',raising=False)
 from fastapi import HTTPException
 with pytest.raises(HTTPException) as exc:save_config(session,'test',dict(key='secret'),secret_fields=['key'])
 assert exc.value.status_code==503

def test_login_flags_and_audiences_are_runtime_local(session,make,monkeypatch):
 from app.modules.account.google import _audiences
 monkeypatch.setenv('SETTINGS_ENCRYPTION_KEY',Fernet.generate_key().decode())
 owner=make.admin(role='admin');client=TestClient(app);headers=login(client,owner)
 r=client.post('/admin/v1/login/settings',headers=headers,json=dict(email_enabled=False,google_enabled=True,google_client_ids=['pc.apps.googleusercontent.com']))
 assert r.status_code==200,r.text
 assert client.get('/v1/auth/providers').json()['google']['client_id']=='pc.apps.googleusercontent.com'
 assert client.post('/v1/auth/email/code',json=dict(email='test@example.com')).status_code==403
 with login_runtime(session,'google'):assert _audiences()==['pc.apps.googleusercontent.com']


def test_catalog_paginates_and_hides_other_tenants(session,make):
 other=Tenant(id='other',code='other',name='Other',kind='native',status='active');session.add(other);session.flush();seed_tenant_defaults(session,other);session.commit()
 for i in range(3):
  drama=make.drama(title='Public '+str(i),status='published');episode=make.episode(drama,status='published');episode.bunny_video_id='video';session.commit()
 hidden=Drama(tenant_id='other',title='Other show',status='published');session.add(hidden);session.flush();session.add(Episode(drama_id=hidden.id,episode_number=1,status='published',bunny_video_id='private'));session.commit()
 client=TestClient(app);r=client.get('/v1/catalog?limit=2&sort=title')
 assert r.status_code==200,r.text
 result=r.json();assert result['total']==3;assert len(result['items'])==2
 assert client.get('/v1/catalog?limit=2&offset=2').json()['items'][0]['title']=='Public 0'
 assert client.get('/v1/catalog?limit=101').status_code==422

def test_subtitle_languages_default_and_pricing_permissions(session,make,monkeypatch):
 from app.modules.common import bunny
 owner=make.admin(role='admin');finance=make.admin(role='finance');drama=make.drama();episode=make.episode(drama)
 client=TestClient(app);headers=login(client,owner)
 monkeypatch.setattr(bunny,'live',lambda:True);monkeypatch.setattr(bunny,'storage_upload',lambda *args,**kwargs:None)
 for language in ['en','zh-CN']:
  r=client.post('/admin/v1/episodes/'+str(episode.id)+'/subtitles',headers=headers,data=dict(language=language,label=language,is_default='true'),files={'file':('captions.vtt',b'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello')})
  assert r.status_code==200,r.text
 tracks=client.get('/admin/v1/episodes/'+str(episode.id)+'/subtitles',headers=headers).json()['items']
 assert len(tracks)==2;assert sum(t['is_default'] for t in tracks)==1
 assert next(t for t in tracks if t['is_default'])['language']=='zh-cn'
 r=client.delete('/admin/v1/episodes/'+str(episode.id)+'/subtitles/zh-cn',headers=headers)
 assert r.status_code==200,r.text
 tracks=client.get('/admin/v1/episodes/'+str(episode.id)+'/subtitles',headers=headers).json()['items'];assert tracks[0]['is_default']
 blocked=client.patch('/admin/v1/episodes/'+str(episode.id)+'/pricing',headers=login(client,finance),json={'coin_price':1})
 assert blocked.status_code==403
 assert client.patch('/admin/v1/episodes/'+str(episode.id)+'/pricing',headers=headers,json={'coin_price':3}).status_code==200

def test_native_tenant_cannot_access_jjtv_settings(session,make):
 other=Tenant(id='other',code='other',name='Other',kind='native',status='active');session.add(other);session.flush();seed_tenant_defaults(session,other);session.commit()
 staff=make.admin(role='admin',tenant_id='other');client=TestClient(app);headers=login(client,staff)
 for path in ['/admin/v1/login/settings','/admin/v1/stripe/accounts','/admin/v1/coins/records','/admin/v1/support/tickets','/admin/v1/attr/pixels']:
  assert client.get(path,headers=headers).status_code==403,path

def test_null_metadata_rejected_and_legacy_subtitle_updates_default(session,make,monkeypatch):
    from app.modules.common import bunny
    owner=make.admin(role='admin');drama=make.drama();episode=make.episode(drama)
    session.add(EpisodeSubtitle(episode_id=episode.id,language='en',label='English',url='subs/old.vtt',is_default=True));session.commit()
    client=TestClient(app);headers=login(client,owner)
    for field in ['access_mode','member_pays_coins','language']:
        r=client.patch('/admin/v1/dramas/'+str(drama.id),headers=headers,json={field:None});assert r.status_code==422,r.text
    monkeypatch.setattr(bunny,'live',lambda:True);uploads=[]
    monkeypatch.setattr(bunny,'storage_upload',lambda path,data,mime:uploads.append((path,data,mime)))
    r=client.post('/admin/v1/episodes/'+str(episode.id)+'/subtitle',headers=headers,files={'file':('old.srt',b'1\n00:00:00,000 --> 00:00:01,000\nHello')})
    assert r.status_code==200,r.text
    track=client.get('/admin/v1/episodes/'+str(episode.id)+'/subtitles',headers=headers).json()['items'][0]
    assert track['language']=='en' and track['url']==r.json()['subtitle_url'] and track['url']!='subs/old.vtt'
    assert uploads[0][1].startswith(b'WEBVTT')

def test_zero_episode_override_is_free_and_zero_base_preserves_member_access():
    drama=SimpleNamespace(free_episode_count=0,non_member_free_episodes=None,coin_price_per_episode=0,access_mode='public',member_pays_coins=True)
    episode=SimpleNamespace(episode_number=2,coin_price=None)
    assert not episode_access(drama,episode)['allowed']
    assert episode_access(drama,episode,member=True)['allowed']
    episode.coin_price=0
    assert episode_access(drama,episode)['free'] and episode_access(drama,episode)['allowed']

def test_history_pagination_and_effective_preview_count(session,make):
    from app.modules.account.service import _issue_session
    from app.modules.playback.models import PlaybackProgress
    user=make.user();drama=make.drama(title='History',status='published',free=0)
    drama.non_member_free_episodes=2
    for n in range(1,4):
        episode=make.episode(drama,number=n,status='published');episode.bunny_video_id='video';session.add(PlaybackProgress(user_id=user.id,episode_id=episode.id,position_seconds=n))
    session.commit();client=TestClient(app);headers={'Authorization':'Bearer '+_issue_session(user)}
    one=client.get('/v1/me/history?limit=2',headers=headers);assert one.status_code==200,one.text
    assert one.json()['total']==3 and len(one.json()['items'])==2
    two=client.get('/v1/me/history?limit=2&offset=2',headers=headers);assert len(two.json()['items'])==1
    assert not {x['episode_id'] for x in one.json()['items']}&{x['episode_id'] for x in two.json()['items']}
    play=client.get('/v1/dramas/'+str(drama.id)+'/play').json()
    assert play['drama']['free_episode_count']==2


def test_subtitle_manager_preserves_legacy_default_and_tenant_scope(session,make):
 owner=make.admin(role='admin');drama=make.drama();episode=make.episode(drama)
 episode.subtitle_url='legacy/subtitles/default.vtt';session.commit()
 other=Tenant(id='subtitle-other',code='subtitle-other',name='Other',kind='native',status='active')
 session.add(other);session.flush();seed_tenant_defaults(session,other);session.commit()
 foreign=Drama(tenant_id=other.id,title='Foreign subtitle show',status='draft');session.add(foreign);session.flush()
 foreign_episode=Episode(drama_id=foreign.id,episode_number=1,subtitle_url='private/foreign.vtt')
 session.add(foreign_episode);session.commit()
 client=TestClient(app);headers=login(client,owner)
 response=client.get('/admin/v1/episodes/'+str(episode.id)+'/subtitles',headers=headers)
 assert response.status_code==200,response.text
 assert response.json()=={'items':[],'legacy_default_url':'legacy/subtitles/default.vtt'}
 assert client.get('/admin/v1/episodes/'+str(foreign_episode.id)+'/subtitles',headers=headers).status_code==404


def test_public_plan_cards_expose_entitlement_days_and_only_recurring_periods(session):
 from app.modules.billing.models import Plan,PlanVersion
 from app.modules.billing import service as billing
 for code in ['weekly','monthly','yearly']:
  plan=Plan(code=code,name_en=code,status='active');session.add(plan);session.flush()
  session.add(PlanVersion(plan_id=plan.id,version=1,price_cents=999))
 session.commit();billing.set_billing_mode(session,'subscription',1)
 client=TestClient(app)
 recurring=client.get('/v1/plans').json()['items']
 assert [(p['code'],p['duration_days'],p['recurring_interval']) for p in recurring]==[
  ('weekly',None,'week'),('monthly',None,'month'),('yearly',None,'year')]
 billing.set_billing_mode(session,'one_time',1)
 one_time=client.get('/v1/plans').json()['items']
 assert [p['duration_days'] for p in one_time]==[7,30,365]
 assert all(p['recurring_interval'] is None for p in one_time)


def test_admin_plan_cards_expose_the_same_real_cadence_as_public_plans(session, make):
 from app.modules.billing.models import Plan, PlanVersion
 from app.modules.billing import service as billing
 owner=make.admin(role='admin')
 for code in ['weekly','monthly','yearly']:
  plan=Plan(code=code,name_en=code,status='active');session.add(plan);session.flush()
  session.add(PlanVersion(plan_id=plan.id,version=1,price_cents=999))
 session.commit()
 client=TestClient(app);headers=login(client,owner)
 for mode in ['subscription','one_time']:
  billing.set_billing_mode(session,mode,owner.id)
  public=client.get('/v1/plans').json()['items']
  response=client.get('/admin/v1/commerce/plans',headers=headers)
  assert response.status_code==200,response.text
  actual=response.json()
  assert actual['billing_mode']==mode
  fields=lambda p:(p['code'],p['duration_days'],p['recurring_interval'],p['billing_mode'])
  assert list(map(fields,actual['items']))==list(map(fields,public))
