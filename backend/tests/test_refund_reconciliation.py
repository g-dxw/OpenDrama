"""Refund retries reconcile the original operation; they never release on a new POST error."""
from datetime import timedelta
import pytest
from sqlalchemy import select,func
from app.modules.billing import commerce as c
from app.modules.billing.models import Order
from app.modules.billing.commerce_models import CommerceRefund,CoinSource,MembershipGrant
from app.modules.billing.service import BillingError
from app.modules.wallet.models import WalletAccount
from tests.test_commerce import commercial,purchase,pay,membership_order


def paid_order(db,user,kind):
    order=membership_order(db,user) if kind=='membership' else purchase(db,user)
    pay(db,order);return order


def unknown_refund(db,user,actor,order,monkeypatch,remote_refunds):
    def lost(db,code,path,data=None,method='POST',idempotency_key=None):
        assert path=='/refunds' and method=='POST'
        remote_refunds.append(dict(id='re_original',amount=order.amount_cents,currency='usd',payment_intent=order.payment_intent_id,status='succeeded',metadata={key[len('metadata['):-1]:value for key,value in data.items() if key.startswith('metadata[')}))
        raise BillingError(503,'response lost after Stripe completed the refund')
    monkeypatch.setattr(c,'stripe_request',lost)
    with pytest.raises(BillingError):c.request_refund(db,order.order_no,actor.id,'unused purchase','refund-original')
    refund=db.scalar(select(CommerceRefund).where(CommerceRefund.order_id==order.id))
    refund.created_at=c.now()-timedelta(days=2);db.commit()
    return refund


@pytest.mark.parametrize('kind',['coin_pack','membership'])
def test_lost_success_after_idempotency_expiry_is_queried_not_reposted_or_released(session,commercial,monkeypatch,kind):
    user,actor,_=commercial;order=paid_order(session,user,kind);objects=[]
    refund=unknown_refund(session,user,actor,order,monkeypatch,objects);calls=[]
    def remote(db,code,path,data=None,method='POST',idempotency_key=None):
        calls.append((code,path,method))
        if method=='GET':return dict(data=objects,has_more=False)
        error=BillingError(502,'charge already refunded');error.definitive=True;raise error
    monkeypatch.setattr(c,'stripe_request',remote)
    try:c.request_refund(session,order.order_no,actor.id,'retry after 24h','retry-key')
    except BillingError:pass
    assert session.get(WalletAccount,user.id).balance==0
    assert refund.status=='succeeded' and order.status=='refunded'
    assert calls and all(method=='GET' for _,_,method in calls)
    assert calls[0][0]==order.account_code
    event={'type':'refund.updated','data':{'object':objects[0]}}
    c.process_event(session,order.account_code,event)
    assert refund.status=='succeeded' and session.get(WalletAccount,user.id).balance==0


@pytest.mark.parametrize('failure',['lookup_failure','empty','foreign','partial','multiple','truncated'])
def test_unknown_refund_lookup_ambiguity_keeps_all_entitlements_frozen(session,commercial,monkeypatch,failure):
    user,actor,_=commercial;order=paid_order(session,user,'membership');objects=[]
    refund=unknown_refund(session,user,actor,order,monkeypatch,objects)
    candidate=dict(objects[0]);candidate['status']='failed'
    def remote(db,code,path,data=None,method='POST',idempotency_key=None):
        assert method=='GET','retry cannot create another refund'
        if failure=='lookup_failure':raise BillingError(503,'provider query unavailable')
        if failure=='empty':return dict(data=[],has_more=False)
        if failure=='foreign':candidate['metadata']={}
        if failure=='partial':candidate['amount']=1
        if failure=='multiple':return dict(data=[candidate,{**candidate,'id':'re_duplicate'}],has_more=False)
        if failure=='truncated':return dict(data=[],has_more=True)
        return dict(data=[candidate],has_more=False)
    monkeypatch.setattr(c,'stripe_request',remote)
    c.request_refund(session,order.order_no,actor.id,'retry','retry-uncertain')
    assert refund.status=='pending' and order.status=='refund_pending'
    source=session.scalar(select(CoinSource).where(CoinSource.order_id==order.id))
    grant=session.scalar(select(MembershipGrant).where(MembershipGrant.order_id==order.id))
    assert source.reserved==25 and source.remaining==0 and grant.status=='refund_pending'
    assert session.get(WalletAccount,user.id).balance==0 and refund.error


@pytest.mark.parametrize('state',['succeeded','failed','canceled','pending'])
def test_known_refund_id_is_retrieved_and_only_verified_original_failure_releases(session,commercial,monkeypatch,state):
    user,actor,_=commercial;order=purchase(session,user);pay(session,order)
    monkeypatch.setattr(c,'stripe_request',lambda *a,**k:dict(id='re_known',status='pending',amount=500,currency='usd',payment_intent=order.payment_intent_id))
    c.request_refund(session,order.order_no,actor.id,'unused','known-first')
    calls=[]
    def remote(db,code,path,data=None,method='POST',idempotency_key=None):
        calls.append((code,path,method));return dict(id='re_known',status=state,amount=500,currency='usd',payment_intent=order.payment_intent_id)
    monkeypatch.setattr(c,'stripe_request',remote)
    c.request_refund(session,order.order_no,actor.id,'retry','known-retry')
    refund=session.scalar(select(CommerceRefund).where(CommerceRefund.order_id==order.id))
    assert calls==[(order.account_code,'/refunds/re_known','GET')]
    assert refund.status==state
    assert session.get(WalletAccount,user.id).balance==(120 if state in {'failed','canceled'} else 0)


def test_first_create_rejection_is_not_proof_that_existing_remote_refund_failed(session,commercial,monkeypatch):
    user,actor,_=commercial;order=purchase(session,user);pay(session,order)
    def rejected(*args,**kwargs):
        error=BillingError(502,'charge already refunded');error.definitive=True;raise error
    monkeypatch.setattr(c,'stripe_request',rejected)
    with pytest.raises(BillingError):c.request_refund(session,order.order_no,actor.id,'unused','first-rejected')
    assert session.get(WalletAccount,user.id).balance==0
    assert order.status=='refund_pending'
    assert session.scalar(select(CommerceRefund)).status=='pending'


def test_foreign_webhook_cannot_bind_unknown_original_refund_and_later_owned_success_can_settle(session,commercial,monkeypatch):
    user,actor,_=commercial;order=purchase(session,user);pay(session,order);objects=[]
    refund=unknown_refund(session,user,actor,order,monkeypatch,objects)
    foreign={**objects[0],'id':'re_foreign','metadata':{}}
    c.process_event(session,order.account_code,{'type':'refund.updated','data':{'object':foreign}})
    assert refund.status=='pending' and refund.refund_id is None and order.status=='refund_pending'
    c.process_event(session,order.account_code,{'type':'refund.updated','data':{'object':objects[0]}})
    assert refund.status=='succeeded' and refund.refund_id=='re_original' and order.status=='refunded'
    assert session.get(WalletAccount,user.id).balance==0


def test_charge_aggregate_without_owned_refund_proof_cannot_settle_original(session,commercial,monkeypatch):
    user,actor,_=commercial;order=purchase(session,user);pay(session,order);objects=[]
    refund=unknown_refund(session,user,actor,order,monkeypatch,objects)
    monkeypatch.setattr(c,'stripe_request',lambda *a,**k:dict(data=[{**objects[0],'metadata':{}}],has_more=False))
    c.process_event(session,order.account_code,{'type':'charge.refunded','data':{'object':dict(id='ch_total',payment_intent=order.payment_intent_id,amount_refunded=500)}})
    assert refund.status=='pending' and order.status=='refund_pending'
    assert session.scalar(select(CoinSource).where(CoinSource.order_id==order.id)).reserved==120


@pytest.mark.parametrize('state',['failed','canceled'])
def test_partial_failed_refund_webhook_cannot_release_full_reservation(session,commercial,monkeypatch,state):
    user,actor,_=commercial;order=purchase(session,user);pay(session,order)
    monkeypatch.setattr(c,'stripe_request',lambda *a,**k:dict(id='re_known',status='pending'))
    c.request_refund(session,order.order_no,actor.id,'unused','partial-first')
    c.process_event(session,order.account_code,{'type':'refund.updated','data':{'object':dict(id='re_known',payment_intent=order.payment_intent_id,status=state,amount=1,currency='usd')}})
    assert session.get(WalletAccount,user.id).balance==0 and order.status=='refund_review'
    assert session.scalar(select(CoinSource).where(CoinSource.order_id==order.id)).reserved==120


def test_shared_payment_intent_refund_is_never_applied_to_scalar_first_order(session,commercial):
    user,actor,_=commercial;first=purchase(session,user);pay(session,first)
    second=purchase(session,user,'second-invoice-order');pay(session,second)
    shared=first.payment_intent_id
    second.checkout_context={'invoice_payment_intent_ids':[shared],'invoice_payment_requires_manual_review':True};session.commit()
    with pytest.raises(BillingError,match='shared'):c.request_refund(session,first.order_no,actor.id,'unused','shared-attempt')
    assert not c.refund_eligibility(session,first)['eligible']
    result=c.process_event(session,'env',{'type':'refund.created','data':{'object':dict(id='re_shared',payment_intent=shared,status='succeeded',amount=500,currency='usd')}})
    assert result['manual_review'] and set(result['affected_orders'])=={first.order_no,second.order_no}
    assert first.status==second.status=='refund_review'
    assert session.scalar(select(func.count()).select_from(CommerceRefund).where(CommerceRefund.status=='manual_review'))==2
    assert session.get(WalletAccount,user.id).balance==240


def test_split_payment_evidence_requires_manual_refund_and_maps_external_events(session,commercial):
    user,actor,_=commercial;order=purchase(session,user);pay(session,order)
    original=order.payment_intent_id
    order.payment_intent_id=None;order.checkout_context={'invoice_payment_intent_ids':[original,'pi_split_second'],'invoice_payment_requires_manual_review':True};session.commit()
    with pytest.raises(BillingError,match='allocation'):c.request_refund(session,order.order_no,actor.id,'unused','split-attempt')
    result=c.process_event(session,'env',{'type':'refund.created','data':{'object':dict(id='re_split',payment_intent=original,status='succeeded',amount=250,currency='usd')}})
    assert result['manual_review'] and order.status=='refund_review'
    assert session.get(WalletAccount,user.id).balance==120


def test_modern_invoice_refund_and_replay_record_payment_source_without_duplicate_grants(session,commercial,monkeypatch):
    from app.modules.billing import service as billing
    from app.modules.wallet.models import CoinLedgerEntry
    from tests.test_stripe_invoice_payments import invoice_object,allocation,payment_intent
    user,actor,_=commercial
    billing.get_billing_settings(session).billing_mode='subscription';session.commit()
    order=membership_order(session,user);order.subscription_ref='sub_basil';session.commit()
    original=c.stripe_request
    def remote(db,code,path,data=None,method='POST',idempotency_key=None):
        assert code==order.account_code
        if path=='/payment_intents/pi_basil':return payment_intent()
        if path.startswith('/invoice_payments'):return dict(data=[allocation()],has_more=False)
        return original(db,code,path,data,method,idempotency_key)
    monkeypatch.setattr(c,'stripe_request',remote)
    event={'type':'invoice.paid','data':{'object':invoice_object([allocation()])}}
    c.process_event(session,'env',event)
    assert order.payment_intent_id=='pi_basil'
    # Simulate a historical modern invoice settled by code that omitted the PI.
    order.payment_intent_id=None;order.checkout_context={};session.commit()
    c.process_event(session,'env',event);session.expire_all()
    order=session.get(Order,order.id)
    assert order.payment_intent_id=='pi_basil' and order.checkout_context['invoice_payment_intent_ids']==['pi_basil']
    assert session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.biz_type=='purchase'))==1
    assert session.get(WalletAccount,user.id).balance==25
    assert c.request_refund(session,order.order_no,actor.id,'unused modern invoice','modern-refund')['status']=='succeeded'
    assert session.get(WalletAccount,user.id).balance==0
    assert session.scalar(select(MembershipGrant).where(MembershipGrant.order_id==order.id)).status=='refunded'


def test_modern_shared_payment_evidence_survives_older_legacy_event_replay(session,commercial,monkeypatch):
    from app.modules.billing import service as billing
    from tests.test_stripe_invoice_payments import invoice_object,allocation,payment_intent
    user,actor,_=commercial
    billing.get_billing_settings(session).billing_mode='subscription';session.commit()
    order=membership_order(session,user);order.subscription_ref='sub_basil';session.commit()
    original=c.stripe_request
    def remote(db,code,path,data=None,method='POST',idempotency_key=None):
        if path=='/payment_intents/pi_basil':return payment_intent(amount=3998)
        if path.startswith('/invoice_payments'):
            return dict(data=[allocation(),allocation(identifier='inpay_other',invoice='in_other')],has_more=False)
        return original(db,code,path,data,method,idempotency_key)
    monkeypatch.setattr(c,'stripe_request',remote)
    modern=invoice_object([allocation()])
    c.process_event(session,'env',{'type':'invoice.paid','data':{'object':modern}})
    assert order.payment_intent_id is None and order.checkout_context['invoice_payment_requires_manual_review']
    import copy
    modern_evidence=copy.deepcopy(order.checkout_context['invoice_payment_evidence'])
    assert modern_evidence['payment_intents'][0]['invoice_ids']==['in_basil','in_other']
    legacy={key:value for key,value in modern.items() if key!='payments'};legacy['payment_intent']='pi_basil'
    c.process_event(session,'env',{'type':'invoice.paid','data':{'object':legacy}});session.expire_all()
    order=session.get(Order,order.id)
    assert order.payment_intent_id is None and order.checkout_context['invoice_payment_requires_manual_review']
    assert order.checkout_context['invoice_payment_intent_ids']==['pi_basil']
    assert order.checkout_context['invoice_payment_evidence']==modern_evidence
    assert not c.refund_eligibility(session,order)['eligible']
    assert session.get(WalletAccount,user.id).balance==25


def test_stale_lookup_response_cannot_overwrite_success_webhook_during_reconciliation(session,commercial,monkeypatch):
    user,actor,_=commercial;order=purchase(session,user);pay(session,order)
    monkeypatch.setattr(c,'stripe_request',lambda *a,**k:dict(id='re_concurrent',status='pending'))
    c.request_refund(session,order.order_no,actor.id,'unused','race-first')
    def remote(db,code,path,data=None,method='POST',idempotency_key=None):
        assert path=='/refunds/re_concurrent' and method=='GET'
        fact=dict(id='re_concurrent',payment_intent=order.payment_intent_id,status='succeeded',amount=500,currency='usd')
        c.process_event(db,code,{'type':'refund.updated','data':{'object':fact}})
        return {**fact,'status':'pending'}
    monkeypatch.setattr(c,'stripe_request',remote)
    result=c.request_refund(session,order.order_no,actor.id,'retry','race-retry')
    assert result['status']=='succeeded' and order.status=='refunded'
    assert session.get(WalletAccount,user.id).balance==0


def test_expanded_legacy_invoice_pointer_is_resolved_before_any_database_string_bind(session,commercial):
    from app.modules.billing import service as billing
    from tests.test_stripe_invoice_payments import invoice_object,payment_intent
    user,_,_=commercial
    billing.get_billing_settings(session).billing_mode='subscription';session.commit()
    order=membership_order(session,user);order.subscription_ref='sub_basil';session.commit()
    invoice=invoice_object(payment_intent=payment_intent())
    c.process_event(session,order.account_code,{'type':'invoice.paid','data':{'object':invoice}})
    session.refresh(order)
    assert order.status=='paid' and order.payment_intent_id=='pi_basil'
    assert order.checkout_context['invoice_payment_evidence']['source']=='legacy_invoice_pointer'
    assert session.get(WalletAccount,user.id).balance==25
    assert c.refund_eligibility(session,order)['eligible']
