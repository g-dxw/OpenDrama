"""Checkout reservation, out-of-order lifecycle, and merchant rotation regressions."""
import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import sessionmaker
from app.modules.billing import commerce as c
from app.modules.billing import service as billing
from app.modules.billing.models import Order,Plan,PlanVersion,Subscription
from app.modules.billing.commerce_models import StripeAccount,StripeSubscriptionLifecycle,MembershipGrant,CommerceSettlement
from app.modules.billing.service import BillingError
from app.modules.ops.managed_settings import get_config
from app.modules.wallet.models import WalletAccount
from tests.test_commerce import commercial,purchase,membership_order

_REAL_STRIPE_IDENTITY=c._stripe_identity


def subscription_order(db,user,key='subscription-first'):
    billing.get_billing_settings(db).billing_mode='subscription';db.commit()
    return membership_order(db,user,key)


def checkout(db,user,key='subscription-next',code='monthly',account_code=None,**kwargs):
    return c.checkout(db,user_id=user.id,email=user.email,product_type='membership',code=code,account_code=account_code,idempotency_key=key,**kwargs)


def invoice(order,subscription='sub_lifecycle',number='in_lifecycle'):
    stamp=int(time.time())
    return {'type':'invoice.paid','data':{'object':dict(id=number,subscription=subscription,payment_intent='pi_'+number,customer='cus_lifecycle',amount_paid=order.amount_cents,currency='usd',parent={'subscription_details':{'metadata':{'commerce_order_no':order.order_no}}},lines={'data':[{'period':{'start':stamp,'end':stamp+30*86400}}]})}}


def lifecycle(order,*,kind='customer.subscription.deleted',created=100,cancel=True,subscription='sub_lifecycle'):
    return {'id':'evt_'+str(created)+kind,'created':created,'type':kind,'data':{'object':dict(id=subscription,status='canceled' if kind.endswith('deleted') else 'active',cancel_at_period_end=cancel,metadata={'commerce_order_no':order.order_no})}}


def test_pending_different_keys_reuse_one_session_and_frozen_request(session,commercial):
    user,_,calls=commercial
    first=subscription_order(session,user)
    result=checkout(session,user,success_url='https://jjtv.8jj.net/changed',cancel_url='https://jjtv.8jj.net/changed')
    assert result['reused'] and result['order_no']==first.order_no
    assert session.scalar(select(func.count()).select_from(Order))==1
    assert len([x for x in calls if x[1]=='/checkout/sessions'])==1


def test_concurrent_tabs_cannot_create_two_subscription_sessions(session,commercial,engine):
    user,_,calls=commercial
    billing.get_billing_settings(session).billing_mode='subscription'
    plan=Plan(code='monthly',name_en='Monthly');session.add(plan);session.flush()
    session.add(PlanVersion(plan_id=plan.id,version=1,price_cents=1999,benefits={}));session.commit()
    user_id,email=user.id,user.email
    factory=sessionmaker(bind=engine,expire_on_commit=False)
    def start(key):
        with factory() as db:
            return c.checkout(db,user_id=user_id,email=email,product_type='membership',code='monthly',idempotency_key=key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(start,['parallel-sub-one','parallel-sub-two']))
    assert len({x['order_no'] for x in results})==1
    assert len([x for x in calls if x[1]=='/checkout/sessions'])==1


def test_different_product_or_account_conflicts_until_confirmed_expiration(session,commercial):
    user,actor,_=commercial;first=subscription_order(session,user)
    plan=Plan(code='yearly',name_en='Yearly');session.add(plan);session.flush()
    session.add(PlanVersion(plan_id=plan.id,version=1,price_cents=9999,benefits={}));session.commit()
    c.save_account(session,'another',dict(name='Another',status='active',secret_key='sk_test_another',webhook_secret='whsec_another'),actor.id)
    for kwargs in [dict(code='yearly'),dict(account_code='another')]:
        with pytest.raises(BillingError) as error:checkout(session,user,**kwargs)
        assert error.value.status_code==409
        assert error.value.detail==dict(code='pending_subscription_checkout',message='Finish or cancel your existing checkout before choosing another subscription.',order_no=first.order_no,action='resume_or_cancel',checkout_url=first.checkout_url)
        session.rollback()
    assert c.cancel_checkout(session,user.id,first.order_no)==dict(order_no=first.order_no,status='failed',canceled=True)
    result=checkout(session,user,key='after-expire-key',code='yearly')
    assert result['order_no']!=first.order_no
    with pytest.raises(BillingError) as error:checkout(session,user,key='subscription-first')
    assert error.value.detail['code']=='checkout_expired'


@pytest.mark.parametrize('paid_status',['created','paid'])
def test_complete_checkout_reserves_slot_until_invoice_grants_even_if_local_status_paid(session,commercial,paid_status):
    user,_,_=commercial;order=subscription_order(session,user)
    completed={'type':'checkout.session.completed','data':{'object':dict(id=order.external_txn_id,payment_status='paid',amount_total=order.amount_cents,currency='usd',subscription='sub_lifecycle',metadata={'commerce_order_no':order.order_no})}}
    c.process_event(session,'env',completed)
    order.status=paid_status;session.commit()
    for operation in [lambda:checkout(session,user),lambda:c.cancel_checkout(session,user.id,order.order_no)]:
        with pytest.raises(BillingError) as error:operation()
        assert error.value.detail['code']=='checkout_payment_pending'
        session.rollback()
    assert session.scalar(select(func.count()).select_from(Subscription))==0
    c.process_event(session,'env',invoice(order))
    assert session.scalar(select(func.count()).select_from(MembershipGrant))==1
    assert session.scalar(select(func.count()).select_from(CommerceSettlement))==1
    with pytest.raises(BillingError,match='active renewing'):checkout(session,user)


def test_remote_complete_without_webhook_blocks_replacement_and_cancellation(session,commercial,monkeypatch):
    user,_,_=commercial;order=subscription_order(session,user)
    original=c.stripe_request
    def remote(db,code,path,data=None,method='POST',idempotency_key=None):
        if method=='GET' and path.startswith('/checkout/sessions/'):
            return dict(id=order.external_txn_id,status='complete',payment_status='paid')
        return original(db,code,path,data,method,idempotency_key)
    monkeypatch.setattr(c,'stripe_request',remote)
    with pytest.raises(BillingError) as error:checkout(session,user)
    assert error.value.detail['code']=='checkout_payment_pending'
    session.expire_all()
    assert session.get(Order,order.id).checkout_context['session_complete']
    with pytest.raises(BillingError) as error:c.cancel_checkout(session,user.id,order.order_no)
    assert error.value.detail['code']=='checkout_payment_pending'


def test_unknown_response_retries_original_request_key_not_current_email_or_redirects(session,commercial,monkeypatch):
    user,_,calls=commercial
    original=c.stripe_request;first_payload=[]
    def uncertain(db,code,path,data=None,method='POST',idempotency_key=None):
        if path=='/checkout/sessions':
            first_payload.append((dict(data),idempotency_key));raise BillingError(503,'unknown remote outcome')
        return original(db,code,path,data,method,idempotency_key)
    monkeypatch.setattr(c,'stripe_request',uncertain)
    with pytest.raises(BillingError):subscription_order(session,user)
    session.rollback()
    order=session.scalar(select(Order));assert order.external_txn_id is None
    monkeypatch.setattr(c,'stripe_request',original)
    c.checkout(session,user_id=user.id,email='changed@example.com',product_type='membership',code='monthly',idempotency_key='different-tab-key',success_url='https://jjtv.8jj.net/changed',cancel_url='https://jjtv.8jj.net/changed')
    final=[x for x in calls if x[1]=='/checkout/sessions'][-1]
    assert (final[2],final[4])==first_payload[0]
    assert session.scalar(select(func.count()).select_from(Order))==1


def test_legacy_pending_without_request_or_session_fails_closed(session,commercial):
    user,_,_=commercial;order=subscription_order(session,user)
    order.checkout_context={};order.external_txn_id=None;order.checkout_url=None;session.commit()
    for operation in [lambda:checkout(session,user),lambda:c.cancel_checkout(session,user.id,order.order_no)]:
        with pytest.raises(BillingError) as error:operation()
        assert error.value.detail['code']=='checkout_recovery_required'
        session.rollback()
    assert session.scalar(select(func.count()).select_from(Order))==1


def test_expired_provider_session_allows_new_key_and_cancel_is_owner_only(session,commercial,make,monkeypatch):
    user,_,_=commercial;order=subscription_order(session,user)
    other=make.user(email='wrong-owner@example.com')
    with pytest.raises(BillingError) as error:c.cancel_checkout(session,other.id,order.order_no)
    assert error.value.status_code==404;session.rollback()
    original=c.stripe_request
    def remote(db,code,path,data=None,method='POST',idempotency_key=None):
        if path=='/checkout/sessions/'+order.external_txn_id:return dict(id=order.external_txn_id,status='expired',payment_status='unpaid')
        return original(db,code,path,data,method,idempotency_key)
    monkeypatch.setattr(c,'stripe_request',remote)
    result=checkout(session,user)
    assert result['order_no']!=order.order_no and session.get(Order,order.id).status=='failed'


@pytest.mark.parametrize('kind',['customer.subscription.deleted','customer.subscription.updated'])
def test_cancellation_before_invoice_is_persisted_and_does_not_create_false_renewal(session,commercial,kind):
    user,_,_=commercial;order=subscription_order(session,user)
    event=lifecycle(order,kind=kind)
    c.process_event(session,'env',event);c.process_event(session,'env',event)
    assert session.scalar(select(func.count()).select_from(Subscription))==0
    fact=session.get(StripeSubscriptionLifecycle,('env','sub_lifecycle'))
    assert fact.cancel_at_period_end and bool(fact.deleted)==kind.endswith('deleted')
    c.process_event(session,'env',invoice(order))
    sub=session.scalar(select(Subscription))
    assert sub.canceled_at is not None and sub.status=='active'
    assert session.get(WalletAccount,user.id).balance==25 and billing.access_until(session,user.id)>datetime.now(timezone.utc)
    result=checkout(session,user)
    assert result['order_no']!=order.order_no


def test_lifecycle_ordering_terminal_deletion_cannot_be_reopened(session,commercial):
    user,_,_=commercial;order=subscription_order(session,user)
    c.process_event(session,'env',lifecycle(order,kind='customer.subscription.updated',created=200))
    c.process_event(session,'env',lifecycle(order,kind='customer.subscription.updated',created=100,cancel=False))
    assert session.get(StripeSubscriptionLifecycle,('env','sub_lifecycle')).cancel_at_period_end
    c.process_event(session,'env',lifecycle(order,kind='customer.subscription.updated',created=300,cancel=False))
    assert not session.get(StripeSubscriptionLifecycle,('env','sub_lifecycle')).cancel_at_period_end
    c.process_event(session,'env',lifecycle(order,created=400))
    c.process_event(session,'env',lifecycle(order,kind='customer.subscription.updated',created=500,cancel=False))
    c.process_event(session,'env',invoice(order))
    assert session.scalar(select(Subscription)).canceled_at is not None


@pytest.mark.parametrize('candidate,merchant,mode',[('sk_test_other','acct_other',False),('sk_live_unit','acct_unit',True)])
def test_rotation_rejects_other_merchant_or_mode_without_mutating_credential(session,commercial,monkeypatch,candidate,merchant,mode):
    user,actor,_=commercial
    c.save_account(session,'managed',dict(name='Managed',status='active',secret_key='sk_test_original',webhook_secret='whsec_original'),actor.id)
    monkeypatch.setattr(c,'_stripe_identity',lambda secret:(merchant,mode,{'id':merchant}))
    with pytest.raises(BillingError) as error:
        c.save_account(session,'managed',dict(secret_key=candidate,webhook_secret='whsec_wrong',name='Wrong'),actor.id)
    assert error.value.detail['code']=='stripe_merchant_mismatch'
    # Check BEFORE rollback too: failed validation must stage no changed secret.
    assert get_config(session,'stripe:managed')['secret_key']=='sk_test_original'
    assert get_config(session,'stripe:managed')['webhook_secret']=='whsec_original'
    session.commit();session.expire_all()
    assert session.get(StripeAccount,'managed').name=='Managed'


def test_original_env_account_with_history_binds_old_identity_before_rotation(session,commercial,monkeypatch):
    user,actor,_=commercial;order=purchase(session,user)
    seen=[]
    def identity(secret):
        seen.append(secret);merchant='acct_old' if secret=='sk_test_unit' else 'acct_other'
        return merchant,False,dict(id=merchant)
    monkeypatch.setattr(c,'_stripe_identity',identity)
    with pytest.raises(BillingError):c.save_account(session,'env',dict(secret_key='sk_test_other',webhook_secret='whsec_other'),actor.id)
    assert seen==['sk_test_unit','sk_test_other']
    assert c.account_config(session,'env')['secret_key']=='sk_test_unit'
    assert session.get(StripeAccount,'env') is None


def test_same_merchant_rotation_retains_encrypted_previous_webhook_secret_for_24h(session,commercial,monkeypatch):
    from app.modules.ops.managed_settings import ManagedSetting
    user,actor,_=commercial
    c.save_account(session,'managed',dict(name='Managed',status='active',secret_key='sk_test_original',webhook_secret='whsec_old'),actor.id)
    response=c.save_account(session,'managed',dict(secret_key='sk_test_rotated',webhook_secret='whsec_new'),actor.id)
    public=json.dumps(response)
    assert 'whsec_old' not in public and 'whsec_new' not in public and 'sk_test_rotated' not in public
    raw=b'{}';stamp=int(time.time())
    def signed(secret):return 't='+str(stamp)+',v1='+hmac.new(secret.encode(),str(stamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
    c.signature(session,'managed',raw,signed('whsec_old'));c.signature(session,'managed',raw,signed('whsec_new'))
    setting=session.get(ManagedSetting,'stripe:managed')
    assert 'whsec_old' not in str(setting.values)+str(setting.secrets) and 'whsec_new' not in str(setting.values)+str(setting.secrets)
    original_time=c.time.time
    monkeypatch.setattr(c.time,'time',lambda:original_time()+86401)
    newstamp=int(c.time.time());newsignature='t='+str(newstamp)+',v1='+hmac.new(b'whsec_old',str(newstamp).encode()+b'.'+raw,hashlib.sha256).hexdigest()
    with pytest.raises(BillingError,match='bad Stripe signature'):c.signature(session,'managed',raw,newsignature)


def test_identity_verification_failure_preserves_existing_settings(session,commercial,monkeypatch):
    user,actor,_=commercial
    c.save_account(session,'managed',dict(name='Managed',status='active',secret_key='sk_test_original',webhook_secret='whsec_original'),actor.id)
    def unavailable(secret):raise BillingError(503,'identity unavailable')
    monkeypatch.setattr(c,'_stripe_identity',unavailable)
    with pytest.raises(BillingError):c.save_account(session,'managed',dict(secret_key='sk_test_changed',webhook_secret='whsec_changed'),actor.id)
    session.commit()
    assert c.account_config(session,'managed')['secret_key']=='sk_test_original'


@pytest.mark.parametrize('key,live',[('sk_test_identity',False),('rk_live_identity',True)])
def test_identity_requires_authenticated_remote_merchant_and_explicit_mode(monkeypatch,key,live):
    seen=[]
    def remote(secret,path,data=None,method='POST',idempotency_key=None):
        seen.append((secret,path,method));return {'id':'acct_verified','country':'US'}
    monkeypatch.setattr(c,'_stripe_http',remote)
    assert _REAL_STRIPE_IDENTITY(key)[:2]==('acct_verified',live)
    assert seen==[(key,'/account','GET')]
    monkeypatch.setattr(c,'_stripe_http',lambda *a,**k:{'id':'wrong_object'})
    with pytest.raises(BillingError,match='merchant identity'):_REAL_STRIPE_IDENTITY(key)


def test_account_test_cannot_rebind_existing_merchant(session,commercial,monkeypatch):
    user,actor,_=commercial
    c.save_account(session,'managed',dict(name='Managed',status='active',secret_key='sk_test_original',webhook_secret='whsec_original'),actor.id)
    monkeypatch.setattr(c,'_stripe_identity',lambda secret:('acct_different',False,{'id':'acct_different'}))
    with pytest.raises(BillingError) as error:c.test_account(session,'managed')
    assert error.value.detail['code']=='stripe_merchant_mismatch'
    session.commit();session.expire_all()
    assert session.get(StripeAccount,'managed').remote_id=='acct_unit'


def test_async_failure_without_completion_webhook_does_not_release_remote_subscription(session,commercial):
    user,_,_=commercial;order=subscription_order(session,user)
    c.process_event(session,'env',{'type':'checkout.session.async_payment_failed','data':{'object':dict(id=order.external_txn_id,metadata={'commerce_order_no':order.order_no})}})
    assert order.status=='created'
    with pytest.raises(BillingError) as error:checkout(session,user)
    assert error.value.detail['code']=='checkout_recovery_required'
    assert session.scalar(select(func.count()).select_from(Order))==1


def test_historical_failed_status_without_expiry_proof_cannot_release_pending_subscription(session,commercial):
    user,_,_=commercial;order=subscription_order(session,user)
    order.status='failed';order.external_txn_id=None;order.checkout_url=None;order.checkout_context={};session.commit()
    with pytest.raises(BillingError) as error:checkout(session,user)
    assert error.value.detail['code']=='checkout_recovery_required'
    assert session.scalar(select(func.count()).select_from(Order))==1


def test_expired_checkout_retains_wallet_mutex_until_replacement_is_reserved(session,commercial,engine,monkeypatch):
    """A tab waiting on an expired Session must not enter the commit/relock gap."""
    from threading import Event
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    user,_,_=commercial;old=subscription_order(session,user)
    user_id,email,old_id,old_session=user.id,user.email,old.id,old.external_txn_id
    original_remote=c.stripe_request;original_state=c._checkout_remote_state
    expired_checked=Event();allow_replacement=Event()
    def remote(db,code,path,data=None,method='POST',idempotency_key=None):
        if path=='/checkout/sessions/'+old_session:
            return dict(id=old_session,status='expired',payment_status='unpaid')
        return original_remote(db,code,path,data,method,idempotency_key)
    def paused_expiry(db,order):
        state=original_state(db,order)
        if order.id==old_id and state=='expired':
            expired_checked.set()
            assert allow_replacement.wait(10),'competing checkout did not complete its lock probe'
        return state
    monkeypatch.setattr(c,'stripe_request',remote)
    monkeypatch.setattr(c,'_checkout_remote_state',paused_expiry)
    factory=sessionmaker(bind=engine,expire_on_commit=False)
    def replacement():
        with factory() as db:
            return c.checkout(db,user_id=user_id,email=email,product_type='membership',code='monthly',idempotency_key='replacement-tab-one')
    def competing_tab():
        with factory() as db:
            db.execute(text("SET LOCAL lock_timeout='250ms'"))
            try:
                return c.checkout(db,user_id=user_id,email=email,product_type='membership',code='monthly',idempotency_key='replacement-tab-two')
            except DBAPIError as error:
                assert (getattr(error.orig,'sqlstate',None) or getattr(error.orig,'pgcode',None))=='55P03'
                db.rollback();return 'wallet-locked'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(replacement)
        assert expired_checked.wait(10),'expired checkout was not inspected'
        try:
            second=pool.submit(competing_tab)
            assert second.result(timeout=10)=='wallet-locked'
        finally:allow_replacement.set()
        result=first.result(timeout=10)
    assert result['order_no']!=old.order_no
    session.expire_all()
    assert session.get(Order,old_id).status=='failed'
    assert session.scalar(select(func.count()).select_from(Order).where(Order.status=='created'))==1
