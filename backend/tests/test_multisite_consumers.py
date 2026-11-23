"""Real PostgreSQL ownership and protocol checks for independent consumer sites."""
from datetime import datetime,timezone,timedelta
import hashlib,hmac,json,time
import pytest
from fastapi import HTTPException
from sqlalchemy import select,insert,func
from sqlalchemy.exc import IntegrityError
from app.modules.account.models import User,ThirdPartyIdentity,VerificationCode
from app.modules.account import service as accounts
from app.modules.billing.models import Order
from app.modules.billing.commerce_models import SiteMerchantBinding,StripeWebhookReceipt
from app.modules.billing import site_merchants
from app.modules.content.models import Drama,Episode
from app.modules.playback.models import PlaybackEvent
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.consumer_scope import site_context,require_owned
from app.modules.wallet.models import WalletAccount,Entitlement,CoinLedgerEntry
from app.modules.wallet.service import unlock_episode,EpisodeNotFoundError


def two_sites(db):
    db.add(Tenant(id='site-b',code='site-b',name='Country B',kind='native',status='active'));db.flush()
    users=[]
    for site in ('jjtv','site-b'):
        with site_context(db,site):
            user=User(email='same@example.com',display_name=site);db.add(user);db.flush()
            db.add(WalletAccount(user_id=user.id,balance=100))
            db.add(ThirdPartyIdentity(user_id=user.id,provider='google',provider_user_id='same-provider-subject'))
            db.flush();users.append(user)
    db.commit();return users


def test_same_email_and_provider_subject_are_independent_accounts(session):
    a,b=two_sites(session)
    assert a.id!=b.id
    for site,expected in (('jjtv',a),('site-b',b)):
        with site_context(session,site):
            assert session.scalar(select(User).where(User.email=='same@example.com')).id==expected.id
            assert session.scalar(select(ThirdPartyIdentity)).user_id==expected.id


def test_new_and_legacy_tokens_never_authenticate_in_another_site(session):
    a,b=two_sites(session)
    token=accounts._issue_session(a)
    legacy='legacy-multisite-credential'
    accounts.get_redis().set(accounts.USER_SESSION_PREFIX+legacy,str(a.id))
    with site_context(session,'site-b'):
        assert accounts.resolve_user(session,token) is None
        assert accounts.resolve_user(session,legacy) is None
        assert accounts.resolve_user(session,accounts._issue_session(b)).id==b.id
        # Already-loaded objects must not bypass ownership through the identity map.
        with pytest.raises(HTTPException):require_owned(session,User,a.id)
    with site_context(session,'jjtv'):
        assert accounts.resolve_user(session,token).id==a.id
        assert accounts.resolve_user(session,legacy).id==a.id


def test_otp_consumption_and_rate_limits_are_site_bound(session,monkeypatch):
    two_sites(session)
    monkeypatch.setattr(accounts,'send_verification_code',lambda **kw:'mail-id')
    with site_context(session,'jjtv'):
        code=accounts.issue_code(session,email='same@example.com',purpose='login')
    with site_context(session,'site-b'):
        with pytest.raises(accounts.AccountError):accounts._consume_code(session,email='same@example.com',purpose='login',code=code)
        session.rollback()
        accounts.issue_code(session,email='same@example.com',purpose='login')
        assert session.scalar(select(func.count()).select_from(VerificationCode))==1
    redis=accounts.get_redis()
    assert int(redis.get('code_rate:jjtv:login:same@example.com'))==1
    assert int(redis.get('code_rate:site-b:login:same@example.com'))==1


def test_database_rejects_cross_site_entitlement_and_wallet_owner(session):
    a,b=two_sites(session)
    with site_context(session,'site-b'):
        drama=Drama(title='Country B title',status='published',coin_price_per_episode=20,free_episode_count=0);session.add(drama);session.flush()
        ep=Episode(drama_id=drama.id,episode_number=1,status='published',bunny_video_id='b-video');session.add(ep);session.commit()
    with pytest.raises(IntegrityError):
        session.execute(insert(Entitlement).values(tenant_id='jjtv',user_id=a.id,episode_id=ep.id,entitlement_type='coin'))
        session.commit()
    session.rollback()
    with site_context(session,'jjtv'):
        with pytest.raises(EpisodeNotFoundError):unlock_episode(session,user_id=a.id,episode_id=ep.id,idempotency_key='foreign-episode')
    session.rollback()
    assert session.get(WalletAccount,a.id).balance==100


def test_statistics_do_not_mix_viewer_events_or_snapshot_days(session):
    a,b=two_sites(session)
    from app.modules.analytics import service as stats
    from app.modules.analytics.models import DailyMetric
    for site,user,count in (('jjtv',a,1),('site-b',b,3)):
        with site_context(session,site):
            drama=Drama(title='Events',status='published');session.add(drama);session.flush()
            ep=Episode(drama_id=drama.id,episode_number=1,status='published',bunny_video_id='video');session.add(ep);session.flush()
            session.add_all([PlaybackEvent(user_id=user.id,episode_id=ep.id,kind='start',position_seconds=0) for _ in range(count)])
            session.commit()
            result=stats.aggregate_day(session,stats.business_day(datetime.now(timezone.utc)))
            assert result.plays==count and result.new_users==1
    assert session.scalar(select(func.count()).select_from(DailyMetric))==2


def test_merchant_reuse_requires_explicit_binding(session,monkeypatch):
    two_sites(session)
    monkeypatch.setenv('STRIPE_SECRET_KEY','sk_test_isolation')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','whsec_isolation')
    with site_context(session,'site-b'):
        with pytest.raises(Exception) as exc:site_merchants.bind_merchant(session,'env',False,None)
        assert exc.value.status_code==409
        session.rollback()
        result=site_merchants.bind_merchant(session,'env',True,None)
        assert result['explicit_reuse'] and result['account_code']=='env'
        with pytest.raises(Exception) as exc:site_merchants.purchase_account(session,'not-bound')
        assert exc.value.status_code==403


def test_signed_webhook_uses_stored_order_site_and_dedupes_merchant_event(session,monkeypatch):
    a,b=two_sites(session)
    monkeypatch.setenv('STRIPE_SECRET_KEY','sk_test_isolation')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','whsec_isolation')
    with site_context(session,'site-b'):
        order=Order(order_no='site-b-order',tenant_id='site-b',user_id=b.id,product_type='coin_pack',product_snapshot=dict(product_type='coin_pack',code='pack',version_id=1,price_cents=500,currency='USD',base_coins=25,bonus_coins=5,billing_mode='one_time'),account_code='env',amount_cents=500,currency='USD',billing_mode='one_time',status='created',checkout_key='site-b-checkout',external_txn_id='cs_site_b')
        session.add(order);session.commit()
    payload={'id':'evt_site_b_1','type':'checkout.session.completed','data':{'object':dict(id='cs_site_b',mode='payment',payment_status='paid',amount_total=500,currency='usd',payment_intent='pi_site_b',metadata={'commerce_order_no':'site-b-order','tenant_id':'jjtv'})}}
    body=json.dumps(payload).encode();stamp=str(int(time.time()))
    signature='t='+stamp+',v1='+hmac.new(b'whsec_isolation',stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    with site_context(session,'jjtv'):
        first=site_merchants.receive(session,'env',body,signature,payload)
        second=site_merchants.receive(session,'env',body,signature,payload)
        assert second['replayed'] is True
        assert session.get(WalletAccount,a.id).balance==100
    with site_context(session,'site-b'):
        assert session.get(WalletAccount,b.id).balance==130
        assert session.scalar(select(func.count()).select_from(CoinLedgerEntry))==1
    assert session.get(StripeWebhookReceipt,('env','evt_site_b_1')).tenant_id=='site-b'


def test_provisioning_after_legacy_explicit_singleton_id_keeps_ids(session):
    from app.modules.billing.models import BillingSettings,Plan
    from app.modules.billing.service import provision_site_billing
    session.add(BillingSettings(id=1,tenant_id='jjtv',billing_mode='one_time'));session.commit()
    session.add(Tenant(id='site-c',code='site-c',name='C',kind='native',status='active'));session.flush()
    session.add(Tenant(id='site-d',code='site-d',name='D',kind='native',status='active'));session.flush()
    provision_site_billing(session,'site-c');provision_site_billing(session,'site-d');session.commit()
    assert session.get(BillingSettings,1).tenant_id=='jjtv'
    with site_context(session,'site-c'):
        assert session.scalar(select(BillingSettings)).id!=1
        assert session.scalar(select(func.count()).select_from(Plan))==3
    with site_context(session,'site-d'):
        assert session.scalar(select(BillingSettings)).id not in (1,2)


def test_site_timezone_controls_report_buckets_and_reward_calendar(session):
    from datetime import date
    from app.modules.sites.models import SiteSettings
    from app.modules.analytics import service as stats
    a,b=two_sites(session)
    session.add(SiteSettings(tenant_id='site-b',timezone='Pacific/Kiritimati',currency='USD'));session.commit()
    stamp=datetime(2026,11,15,4,30,tzinfo=timezone.utc)
    with site_context(session,'site-b'):
        assert accounts.la_today(stamp,db=session)==date(2026,11,15)
        drama=Drama(title='Pacific test',status='published');session.add(drama);session.flush()
        ep=Episode(drama_id=drama.id,episode_number=1,status='published',bunny_video_id='clock');session.add(ep);session.flush()
        session.add(PlaybackEvent(user_id=b.id,episode_id=ep.id,kind='start',position_seconds=0,created_at=stamp));session.commit()
        report=stats.dashboard(session,date(2026,11,15),days=1)
        assert report['timezone']=='Pacific/Kiritimati' and report['plays']==1
    with site_context(session,'jjtv'):
        assert accounts.la_today(stamp,db=session)==date(2026,11,14)


def test_pack_currency_requires_explicit_site_price_and_checkout_cannot_convert(session):
    from app.modules.sites.models import SiteSettings
    from app.modules.billing import commerce
    from app.modules.billing.service import BillingError
    from app.modules.billing.commerce_models import CoinPackVersion
    _,b=two_sites(session)
    from app.modules.tenancy.permissions import seed_tenant_defaults
    seed_tenant_defaults(session,session.get(Tenant,'site-b'))
    session.add(SiteSettings(tenant_id='site-b',timezone='UTC',currency='EUR',web_url='https://b.example',h5_url='https://m.b.example'));session.commit()
    with site_context(session,'site-b'):
        with pytest.raises(BillingError) as exc:commerce.save_pack(session,'start',dict(name_en='Start',price_cents=500,currency='USD',base_coins=25),None)
        assert exc.value.status_code==409
        session.rollback()
        product=commerce.save_pack(session,'start',dict(name_en='Start',price_cents=500,currency='EUR',base_coins=25),None)
        assert product['currency']=='EUR'
        session.get(SiteSettings,'site-b').currency='GBP';session.commit()
        with pytest.raises(BillingError) as exc:commerce.checkout(session,user_id=b.id,email=b.email,product_type='coin_pack',code='start',idempotency_key='currency-must-not-convert')
        assert exc.value.status_code==409
        session.rollback()
        assert session.scalar(select(CoinPackVersion.currency))=='EUR'
        assert session.scalar(select(func.count()).select_from(Order))==0


def test_unscoped_internal_writers_infer_one_consistent_parent_site(session):
    _,b=two_sites(session)
    drama=Drama(tenant_id='site-b',title='Legacy internal writer',status='published');session.add(drama);session.flush()
    ep=Episode(drama_id=drama.id,episode_number=1,status='published',bunny_video_id='legacy-internal');session.add(ep);session.flush()
    event=PlaybackEvent(user_id=b.id,episode_id=ep.id,kind='start',position_seconds=0);session.add(event);session.commit()
    assert ep.tenant_id==event.tenant_id=='site-b'


def test_finance_filters_and_summary_use_site_day_and_currency(session):
    from datetime import date
    from app.modules.sites.models import SiteSettings
    from app.modules.billing.commerce import admin_orders
    _,b=two_sites(session)
    session.add(SiteSettings(tenant_id='site-b',timezone='Asia/Kolkata',currency='INR'));session.commit()
    with site_context(session,'site-b'):
        session.add(Order(order_no='india-day',user_id=b.id,product_type='coin_pack',product_snapshot={},account_code='env',amount_cents=500,currency='INR',status='paid',created_at=datetime(2026,11,14,20,0,tzinfo=timezone.utc)))
        session.commit()
        result=admin_orders(session,date_from=date(2026,11,15),date_to=date(2026,11,15))
        assert result['total']==1 and result['summary']['currency']=='INR'
        assert result['summary']['net_cents']==500


def test_coin_statistics_use_site_midnight_and_exclude_other_sites(session,make):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.modules.sites.models import SiteSettings
    from app.modules.tenancy.permissions import seed_tenant_defaults
    a,b=two_sites(session)
    site=session.get(Tenant,'site-b');seed_tenant_defaults(session,site)
    session.add(SiteSettings(tenant_id=site.id,timezone='Asia/Kolkata',currency='INR'));session.commit()
    stamp=datetime(2026,11,14,20,0,tzinfo=timezone.utc)
    for tenant,user,amount in (('jjtv',a,99),('site-b',b,5)):
        with site_context(session,tenant):
            session.get(WalletAccount,user.id).balance+=amount
            session.add(CoinLedgerEntry(user_id=user.id,amount=amount,balance_after=100+amount,biz_type='adjustment',biz_key='clock:'+tenant,created_at=stamp));session.commit()
    owner=make.admin(role='admin',tenant_id=site.id)
    client=TestClient(app)
    login=client.post('/tenant/v1/auth/login',json={'tenant_code':site.code,'username':owner.username,'password':'pass-1234'})
    assert login.status_code==200,login.text
    result=client.get('/admin/v1/coins/stats',headers={'Authorization':'Bearer '+login.json()['token']})
    assert result.status_code==200,result.text
    # 20:00 UTC is 01:30 next day in India, while Los Angeles is still Nov 14.
    assert result.json()['items']==[{'day':'2026-11-15','amount':5,'count':1}]
