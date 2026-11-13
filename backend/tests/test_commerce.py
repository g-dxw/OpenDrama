"""Commercial money facts, source consumption and historical compatibility.
All Stripe requests are mocked. These tests exercise real PostgreSQL constraints.
"""
import json
import time
import hashlib
import hmac
from datetime import datetime,timedelta,timezone
import pytest
from sqlalchemy import select,func
from app.modules.billing import commerce as c
from app.modules.billing import service as billing
from app.modules.billing.models import Order,Plan,PlanVersion,Subscription
from app.modules.billing.commerce_models import CoinPackVersion,CoinSource,CoinAllocation,MembershipGrant,CommerceRefund
from app.modules.billing.service import BillingError
from app.modules.wallet.models import WalletAccount,CoinLedgerEntry
from app.modules.wallet import service as wallet

@pytest.fixture
def commercial(session,make,monkeypatch):
    monkeypatch.setenv('STRIPE_SECRET_KEY','sk_test_unit')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET','whsec_unit')
    from cryptography.fernet import Fernet
    monkeypatch.setenv('SETTINGS_ENCRYPTION_KEY',Fernet.generate_key().decode())
    actor=make.admin(role='admin');user=make.user(coins=0)
    calls=[]
    def remote(db,code,path,data=None,method='POST',idempotency_key=None):
        calls.append((code,path,data,method,idempotency_key))
        if path=='/prices':return {'id':'price_'+str(len(calls))}
        if path=='/checkout/sessions':return {'id':'cs_'+idempotency_key[-12:],'url':'https://checkout.stripe.com/test/'+idempotency_key[-12:]}
        if path=='/refunds':return {'id':'re_'+hashlib.sha256(idempotency_key.encode()).hexdigest()[:12],'status':'succeeded'}
        if path=='/account':return {'id':'acct_unit','country':'US'}
        return {}
    monkeypatch.setattr(c,'stripe_request',remote)
    c.save_pack(session,'starter',dict(name_en='Starter',price_cents=500,base_coins=100,bonus_coins=20),actor.id)
    return user,actor,calls

def purchase(db,user,key='purchase-one',kind='coin_pack',code='starter'):
    result=c.checkout(db,user_id=user.id,email=user.email,product_type=kind,code=code,idempotency_key=key)
    return db.scalar(select(Order).where(Order.order_no==result['order_no']))

def pay(db,order):
    return c.process_event(db,order.account_code,{'type':'checkout.session.completed','data':{'object':dict(id=order.external_txn_id,mode='payment',payment_status='paid',amount_total=order.amount_cents,currency=order.currency.lower(),payment_intent='pi_'+order.order_no,metadata={'commerce_order_no':order.order_no})}})

def test_product_version_frozen_and_callback_replay(session,commercial):
    user,actor,_=commercial;order=purchase(session,user)
    c.save_pack(session,'starter',dict(price_cents=900,base_coins=999),actor.id)
    pay(session,order);pay(session,order)
    assert session.get(WalletAccount,user.id).balance==120
    assert session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.biz_type=='purchase'))==1
    assert session.scalar(select(func.count()).select_from(CoinPackVersion))==2
    assert order.product_snapshot['base_coins']==100 and order.amount_cents==500

def test_checkout_idempotency_and_account_scoping(session,commercial):
    user,_,calls= commercial
    first=purchase(session,user);again=purchase(session,user)
    assert first.id==again.id
    assert len([x for x in calls if x[1]=='/checkout/sessions'])==1
    with pytest.raises(BillingError,match='another product'):
        c.checkout(session,user_id=user.id,email=user.email,product_type='membership',code='monthly',idempotency_key='purchase-one')

def test_wrong_amount_or_currency_never_credits(session,commercial):
    user,_,_=commercial;order=purchase(session,user)
    payload={'type':'checkout.session.completed','data':{'object':dict(id=order.external_txn_id,payment_status='paid',amount_total=1,currency='usd',metadata={'commerce_order_no':order.order_no})}}
    with pytest.raises(BillingError,match='amount'):c.process_event(session,'env',payload)
    assert session.get(WalletAccount,user.id).balance==0

def test_fifo_prevents_refunding_spent_source_even_after_new_credit(session,commercial,make):
    user,actor,_=commercial;order=purchase(session,user);pay(session,order)
    drama=make.drama(status='published',free=0,price=60);episode=make.episode(drama,number=1,status='published')
    wallet.unlock_episode(session,user_id=user.id,episode_id=episode.id,idempotency_key='unlock-one')
    assert session.scalar(select(func.sum(CoinAllocation.amount)))==60
    other=purchase(session,user,'purchase-two');pay(session,other)
    assert session.get(WalletAccount,user.id).balance==180
    with pytest.raises(BillingError,match='consumed'):c.request_refund(session,order.order_no,actor.id,'customer request','refund-test')

def test_refund_only_its_own_unconsumed_lot_and_replay(session,commercial):
    user,actor,calls=commercial;first=purchase(session,user);pay(session,first);second=purchase(session,user,'purchase-two');pay(session,second)
    result=c.request_refund(session,first.order_no,actor.id,'customer request','refund-test')
    assert result['status']=='succeeded'
    assert session.get(WalletAccount,user.id).balance==120
    c.request_refund(session,first.order_no,actor.id,'retry','retry-key')
    assert len([x for x in calls if x[1]=='/refunds'])==1
    assert session.get(Order,second.id).status=='paid'

def test_pending_refund_reserves_and_failed_callback_releases(session,commercial,monkeypatch,make):
    user,actor,_=commercial;order=purchase(session,user);pay(session,order)
    def pending(*args,**kwargs):return {'id':'re_pending','status':'pending'}
    monkeypatch.setattr(c,'stripe_request',pending)
    c.request_refund(session,order.order_no,actor.id,'request','reserve-key')
    assert session.get(WalletAccount,user.id).balance==0
    source=session.scalar(select(CoinSource).where(CoinSource.order_id==order.id))
    assert source.remaining==0 and source.reserved==120
    refund=session.scalar(select(CommerceRefund).where(CommerceRefund.order_id==order.id))
    c.finalize_refund(session,refund,'failed');session.commit()
    assert session.get(WalletAccount,user.id).balance==120 and source.remaining==120
    c.finalize_refund(session,refund,'failed');session.commit()
    assert session.get(WalletAccount,user.id).balance==120

def test_unknown_refund_network_keeps_reservation(session,commercial,monkeypatch):
    user,actor,_=commercial;order=purchase(session,user);pay(session,order)
    def uncertain(*args,**kwargs):raise BillingError(503,'response uncertain')
    monkeypatch.setattr(c,'stripe_request',uncertain)
    with pytest.raises(BillingError):c.request_refund(session,order.order_no,actor.id,'request','uncertain-key')
    assert session.get(Order,order.id).status=='refund_pending'
    assert session.get(WalletAccount,user.id).balance==0

def membership_order(db,user,key='membership-one'):
    plan=Plan(code='monthly',name_en='Monthly');db.add(plan);db.flush()
    version=PlanVersion(plan_id=plan.id,version=1,price_cents=1999,base_coins=20,bonus_coins=5,benefits={});db.add(version);db.commit()
    return purchase(db,user,key,kind='membership',code='monthly')

def test_consumed_membership_cannot_refund(session,commercial):
    user,actor,_=commercial;order=membership_order(session,user);pay(session,order)
    assert billing.access_until(session,user.id) is not None
    c.mark_membership_consumed(session,user.id,123);session.commit()
    with pytest.raises(BillingError,match='consumed'):c.request_refund(session,order.order_no,actor.id,'request','member-refund')

def test_refund_membership_preserves_legacy_window(session,commercial,make):
    user,actor,_=commercial;legacy=make.subscription(user);legacy_end=legacy.current_period_end
    order=membership_order(session,user);pay(session,order)
    c.request_refund(session,order.order_no,actor.id,'request','member-refund')
    assert billing.access_until(session,user.id)==legacy_end

def test_frozen_membership_cannot_be_consumed(session,commercial,monkeypatch):
    user,actor,_=commercial;order=membership_order(session,user);pay(session,order)
    monkeypatch.setattr(c,'stripe_request',lambda *a,**k:{'id':'re_pending','status':'pending'})
    c.request_refund(session,order.order_no,actor.id,'request','member-pending')
    assert billing.access_until(session,user.id) is None
    with pytest.raises(BillingError,match='changed'):c.mark_membership_consumed(session,user.id,1)

def test_invoice_is_single_recurring_settlement_and_pins_original_version(session,commercial):
    user,actor,_=commercial
    settings=billing.get_billing_settings(session);settings.billing_mode='subscription';session.commit()
    order=membership_order(session,user)
    order.subscription_ref='sub_unit';session.commit()
    start=int(time.time());end=start+30*86400
    event={'type':'invoice.paid','data':{'object':dict(id='in_first',subscription='sub_unit',customer='cus_unit',payment_intent='pi_first',amount_paid=1999,currency='usd',lines={'data':[{'period':{'start':start,'end':end}}]})}}
    c.process_event(session,'env',event);c.process_event(session,'env',event)
    assert session.get(WalletAccount,user.id).balance==25
    latest=PlanVersion(plan_id=session.get(PlanVersion,order.plan_version_id).plan_id,version=2,price_cents=2999,base_coins=999,bonus_coins=0,benefits={});session.add(latest);session.commit()
    event['data']['object'].update(id='in_second',payment_intent='pi_second');event['data']['object']['lines']['data'][0]['period']={'start':end,'end':end+30*86400}
    c.process_event(session,'env',event)
    assert session.get(WalletAccount,user.id).balance==50
    renewal=session.scalar(select(Order).where(Order.invoice_id=='in_second'))
    assert renewal.plan_version_id==order.plan_version_id and renewal.product_snapshot['base_coins']==20

def test_managed_stripe_secret_never_returned_and_old_account_disabled_only_new_checkout(session,commercial):
    user,actor,_=commercial
    c.save_account(session,'second',dict(name='Secondary',status='active',is_default=True,secret_key='sk_test_SECOND_SECRET',webhook_secret='whsec_SECOND_SECRET'),actor.id)
    public=c.account_list(session)
    assert 'SECOND_SECRET' not in json.dumps(public)
    assert next(x for x in public if x['code']=='second')['secret_key_configured']
    order=purchase(session,user)
    assert order.account_code=='second'
    c.save_account(session,'second',dict(status='disabled'),actor.id)
    pay(session,order)
    assert session.get(WalletAccount,user.id).balance==120
    assert c.get_account(session,'second')['secret_key']=='sk_test_SECOND_SECRET'
    with pytest.raises(BillingError):c.get_account(session,'second',True)

def test_return_url_origin_allowlist_and_signature(session,commercial):
    assert c.return_url('https://jjtv.8jj.net/#watch/1','success').startswith('https://jjtv.8jj.net/')
    for url in ['https://evil.example/','https://jjtv.8jj.net.evil.example/','https://evil@jjtv.8jj.net/','javascript:alert(1)','//jjtv.8jj.net/']:
        with pytest.raises(BillingError):c.return_url(url,'success')
    raw=b'{}';stamp=int(time.time());digest=hmac.new(b'whsec_unit',str(stamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
    c.signature(session,'env',raw,'t='+str(stamp)+',v1='+digest)
    with pytest.raises(BillingError):c.signature(session,'env',raw,'t='+str(stamp)+',v1=bad')


def test_subscription_created_without_paid_invoice_does_not_grant_access(session,commercial):
    user,actor,_=commercial
    billing.get_billing_settings(session).billing_mode='subscription';session.commit()
    order=membership_order(session,user)
    result=c.process_event(session,'env',{'type':'customer.subscription.created','data':{'object':{'id':'sub_unpaid','metadata':{'commerce_order_no':order.order_no,'user_id':str(user.id),'plan_code':'monthly'},'status':'active'}}})
    assert result['pending']
    assert billing.access_until(session,user.id) is None
    assert session.get(WalletAccount,user.id).balance==0


def test_failed_checkout_becomes_terminal_but_late_failure_cannot_revoke_payment(session,commercial):
    user,actor,_=commercial;order=purchase(session,user)
    event={'type':'checkout.session.expired','data':{'object':{'id':order.external_txn_id,'metadata':{'commerce_order_no':order.order_no}}}}
    c.process_event(session,'env',event)
    assert order.status=='failed'
    pay(session,order)
    assert order.status=='paid'
    c.process_event(session,'env',event)
    assert order.status=='paid' and session.get(WalletAccount,user.id).balance==120

def test_renewal_does_not_trust_callback_amount_over_frozen_price(session,commercial):
    user,actor,_=commercial
    billing.get_billing_settings(session).billing_mode='subscription';session.commit()
    order=membership_order(session,user);order.subscription_ref='sub_price_guard';session.commit()
    start=int(time.time());event={'type':'invoice.paid','data':{'object':dict(id='in_good_amount',subscription='sub_price_guard',payment_intent='pi_good',amount_paid=1999,currency='usd',lines={'data':[{'period':{'start':start,'end':start+30*86400}}]})}}
    c.process_event(session,'env',event)
    event['data']['object'].update(id='in_bad_amount',payment_intent='pi_bad',amount_paid=1)
    with pytest.raises(BillingError,match='amount mismatch'):c.process_event(session,'env',event)
    session.rollback()
    assert session.get(WalletAccount,user.id).balance==25


def test_provider_initiated_refund_is_visible_for_manual_reconciliation(session,commercial):
    user,actor,_=commercial;order=purchase(session,user);pay(session,order)
    result=c.process_event(session,'env',{'type':'charge.refunded','data':{'object':{'id':'ch_external','payment_intent':order.payment_intent_id,'amount_refunded':500}}})
    assert result['manual_review']
    assert order.status=='refund_review'
    assert session.scalar(select(CommerceRefund).where(CommerceRefund.order_id==order.id)).status=='manual_review'


def test_admin_revoke_and_manual_extend_respect_managed_membership_grants(session,commercial):
    user,actor,_=commercial;order=membership_order(session,user);pay(session,order)
    original=billing.access_until(session,user.id)
    added=billing.grant_membership(session,user_id=user.id,plan_code='monthly',days=3,mode='extend',reason='support extension',actor_id=actor.id)
    assert datetime.fromisoformat(added['after_until'])>=original+timedelta(days=3)
    billing.revoke_membership(session,user_id=user.id,mode='now',reason='support revocation',actor_id=actor.id)
    assert billing.access_until(session,user.id) is None
    assert session.scalar(select(MembershipGrant).where(MembershipGrant.order_id==order.id)).status=='revoked'


def test_original_legacy_webhook_uses_rotated_managed_signing_secret(session,commercial,make):
    from app.modules.ops.managed_settings import save_config
    user,actor,_=commercial;sub=make.subscription(user);sub.stripe_subscription_id='sub_legacy_rotated';session.commit()
    save_config(session,'stripe:env',dict(secret_key='sk_test_rotated',webhook_secret='whsec_rotated'),secret_fields=('secret_key','webhook_secret'));session.commit()
    plan=session.get(Plan,sub.plan_id);stamp=int(time.time())
    payload={'type':'customer.subscription.updated','data':{'object':dict(id=sub.stripe_subscription_id,status='active',metadata={'user_id':str(user.id),'plan_code':plan.code},current_period_start=stamp,current_period_end=stamp+30*86400)}}
    raw=json.dumps(payload).encode();digest=hmac.new(b'whsec_rotated',str(stamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
    result=billing.apply_stripe_event(session,body=raw,signature='t='+str(stamp)+',v1='+digest,payload=payload)
    assert result['received'] and result['user_id']==user.id

def test_legacy_subscription_cancel_honors_managed_original_merchant_without_env_mutation(session,commercial,make,monkeypatch):
    from app.modules.ops.managed_settings import save_config
    user,actor,_=commercial;sub=make.subscription(user);sub.stripe_subscription_id='sub_legacy_cancel';session.commit()
    save_config(session,'stripe:env',dict(secret_key='sk_test_rotated'),secret_fields=('secret_key','webhook_secret'));session.commit()
    seen=[]
    def remote(path,data=None,method='POST'):
        seen.append((path,billing.stripe_secret()));return {}
    monkeypatch.setattr(billing,'_stripe_request',remote)
    billing.cancel_subscription(session,user.id)
    assert seen==[('/subscriptions/sub_legacy_cancel','sk_test_rotated')]
    assert billing.stripe_secret()=='sk_test_unit'


def test_legacy_unallocated_debit_cannot_be_hidden_by_later_reward_credit(session,commercial):
    user,actor,_=commercial;order=purchase(session,user);pay(session,order)
    wallet_row=session.get(WalletAccount,user.id)
    # Historical import simulates the pre-source-aware admin/reward paths.
    wallet_row.balance-=20
    session.add(CoinLedgerEntry(user_id=user.id,amount=-20,balance_after=100,biz_type='adjustment',biz_key='legacy-unmatched-debit'))
    session.commit()
    wallet_row.balance+=20
    session.add(CoinLedgerEntry(user_id=user.id,amount=20,balance_after=120,biz_type='checkin',biz_key='legacy-replenished-credit'))
    session.commit()
    source=session.scalar(select(CoinSource).where(CoinSource.order_id==order.id))
    assert source.remaining==120 and wallet_row.balance==120
    with pytest.raises(BillingError,match='unmatched legacy'):
        c.request_refund(session,order.order_no,actor.id,'request','restored-balance')


def test_source_aware_admin_debit_and_reward_credit_preserve_consumption_proof(session,commercial):
    from app.modules.ops.flow_router import adjust,AdjustIn
    from app.modules.account.service import _credit
    user,actor,_=commercial;order=purchase(session,user);pay(session,order)
    adjust(user.id,AdjustIn(delta=-20,reason='operator debit'),session,actor)
    _credit(session,user.id,20,'checkin','new-source-reward');session.commit()
    assert session.get(WalletAccount,user.id).balance==120
    debit=session.scalar(select(CoinLedgerEntry).where(CoinLedgerEntry.biz_type=='adjustment'))
    assert session.scalar(select(func.sum(CoinAllocation.amount)).where(CoinAllocation.ledger_id==debit.id))==20
    assert session.scalar(select(CoinSource.remaining).where(CoinSource.order_id==order.id))==100
    with pytest.raises(BillingError,match='consumed'):
        c.request_refund(session,order.order_no,actor.id,'request','tracked-debit')


def test_mixed_fifo_sources_allow_refund_when_only_older_legacy_coins_were_used(session,commercial,make):
    user,actor,_=commercial
    session.get(WalletAccount,user.id).balance=50;session.commit()
    order=purchase(session,user);pay(session,order)
    drama=make.drama(status='published',free=0,price=30);episode=make.episode(drama,number=1,status='published')
    wallet.unlock_episode(session,user_id=user.id,episode_id=episode.id,idempotency_key='mixed-source-unlock')
    source=session.scalar(select(CoinSource).where(CoinSource.order_id==order.id))
    assert source.remaining==120
    debit=session.scalar(select(CoinLedgerEntry).where(CoinLedgerEntry.biz_type=='unlock'))
    allocation=session.scalar(select(CoinAllocation).where(CoinAllocation.ledger_id==debit.id))
    assert allocation.amount==30 and session.get(CoinSource,allocation.source_id).order_id is None
    assert c.request_refund(session,order.order_no,actor.id,'unused purchase','mixed-refund')['status']=='succeeded'
    assert session.get(WalletAccount,user.id).balance==20


def test_other_verified_refund_reservation_is_not_an_unmatched_coin_debit(session,commercial):
    user,actor,_=commercial;first=purchase(session,user);pay(session,first)
    second=purchase(session,user,'other-source-order');pay(session,second)
    c.request_refund(session,first.order_no,actor.id,'first unused','refund-first')
    assert c.request_refund(session,second.order_no,actor.id,'second unused','refund-second')['status']=='succeeded'
    assert session.get(WalletAccount,user.id).balance==0
