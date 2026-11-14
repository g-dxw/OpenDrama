"""Stripe-only commerce. Product and entitlement facts are frozen before Checkout.
External writes use stable Stripe idempotency keys; wallet row locks serialize
settlement, playback consumption, FIFO spending and refundable-source reserves.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.modules.billing.models import Order, Plan, PlanVersion, Subscription
from app.modules.billing.commerce_models import (CoinPack,CoinPackVersion,StripeAccount,StripePriceBinding,CoinSource,CoinAllocation,MembershipGrant,CommerceSettlement,CommerceRefund,StripeSubscriptionLifecycle)
from app.modules.wallet.models import WalletAccount,CoinLedgerEntry
from app.modules.common.models import AuditLog
from app.modules.billing.service import BillingError
from app.modules.ops.managed_settings import get_config,save_config,public_config

UTC=timezone.utc

def now(): return datetime.now(UTC)
def aware(dt): return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt

def audit(db,actor,action,target_id=None,payload=None):
    db.add(AuditLog(actor_type='admin',actor_id=actor,action=action,target_type='commerce',target_id=target_id,payload=payload or {}))

def wallet_lock(db,user_id):
    row=db.get(WalletAccount,user_id,with_for_update=True,populate_existing=True)
    if row is None:
        # Accounts normally provision a wallet at registration. An advisory lock
        # covers imported accounts without one, avoiding a duplicate primary key.
        from sqlalchemy import text
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':user_id})
        row=db.get(WalletAccount,user_id,with_for_update=True,populate_existing=True)
        if row is None:
            row=WalletAccount(user_id=user_id,balance=0);db.add(row);db.flush()
    return row

def ensure_sources(db,wallet):
    total=db.scalar(select(func.coalesce(func.sum(CoinSource.remaining),0)).where(CoinSource.user_id==wallet.user_id)) or 0
    if total<wallet.balance:
        db.add(CoinSource(user_id=wallet.user_id,original=wallet.balance-total,remaining=wallet.balance-total,reserved=0));db.flush()
    elif total>wallet.balance:
        # Reconcile external legacy adjustments conservatively against FIFO lots.
        delta=total-wallet.balance
        for source in db.scalars(select(CoinSource).where(CoinSource.user_id==wallet.user_id,CoinSource.remaining>0).order_by(CoinSource.created_at,CoinSource.id).with_for_update()):
            take=min(delta,source.remaining);source.remaining-=take;delta-=take
            if not delta:break
        db.flush()

def allocate_spend(db,wallet,ledger,amount):
    ensure_sources(db,wallet)
    db.flush()
    remaining=amount
    for source in db.scalars(select(CoinSource).where(CoinSource.user_id==wallet.user_id,CoinSource.remaining>0).order_by(CoinSource.created_at,CoinSource.id).with_for_update()):
        take=min(source.remaining,remaining);source.remaining-=take;remaining-=take
        db.add(CoinAllocation(source_id=source.id,ledger_id=ledger.id,amount=take))
        if not remaining:break
    if remaining:raise BillingError(409,'wallet source allocation changed; retry')

def legacy_access_until(db,user_id):
    row=db.scalar(select(Subscription).where(Subscription.user_id==user_id,Subscription.status=='active'))
    if row is None:return None
    ends=[aware(row.pass_end)]
    if not row.managed_commerce:ends.append(aware(row.current_period_end))
    valid=[x for x in ends if x and x>now()]
    return max(valid) if valid else None

def grant_access_until(db,user_id):
    grants=list(db.scalars(select(MembershipGrant).where(MembershipGrant.user_id==user_id,MembershipGrant.status=='active').order_by(MembershipGrant.starts_at)))
    end=legacy_access_until(db,user_id)
    point=now()
    for grant in grants:
        start,finish=aware(grant.starts_at),aware(grant.ends_at)
        if finish<=point:continue
        if start<=max(point,end or point):end=max(end or finish,finish)
    return end

def mark_membership_consumed(db,user_id,episode_id):
    wallet_lock(db,user_id)
    if legacy_access_until(db,user_id):return
    grant=db.scalar(select(MembershipGrant).where(MembershipGrant.user_id==user_id,MembershipGrant.status=='active',MembershipGrant.starts_at<=now(),MembershipGrant.ends_at>now()).order_by(MembershipGrant.starts_at,MembershipGrant.id).with_for_update())
    if grant is None:raise BillingError(409,'membership access changed; retry')
    grant.access_consumed=True;db.flush()

def list_packs(db,include_retired=False):
    out=[]
    stmt=select(CoinPack).order_by(CoinPack.sort_order,CoinPack.id)
    if not include_retired:stmt=stmt.where(CoinPack.status=='active')
    for pack in db.scalars(stmt):
        version=db.scalar(select(CoinPackVersion).where(CoinPackVersion.pack_id==pack.id).order_by(CoinPackVersion.version.desc()).limit(1))
        if version:out.append(dict(code=pack.code,name_en=pack.name_en,name_zh=pack.name_zh,status=pack.status,sort_order=pack.sort_order,badge=pack.badge,version=version.version,version_id=version.id,price_cents=version.price_cents,currency=version.currency,base_coins=version.base_coins,bonus_coins=version.bonus_coins,coins=version.base_coins+version.bonus_coins))
    return out

def save_pack(db,code,data,actor):
    pack=db.scalar(select(CoinPack).where(CoinPack.code==code).with_for_update())
    if pack is None:
        pack=CoinPack(code=code,name_en=data['name_en'],name_zh=data.get('name_zh',''),status='active');db.add(pack);db.flush()
    for key in ('name_en','name_zh','sort_order','badge'):
        if key in data and data[key] is not None:setattr(pack,key,data[key])
    latest=db.scalar(select(CoinPackVersion).where(CoinPackVersion.pack_id==pack.id).order_by(CoinPackVersion.version.desc()).limit(1))
    values={key:data.get(key,getattr(latest,key,None)) for key in ('price_cents','currency','base_coins','bonus_coins')}
    values['currency']=(values['currency'] or 'USD').upper();values['bonus_coins']=values['bonus_coins'] or 0
    if values['price_cents'] is None or values['price_cents']<50 or values['base_coins'] is None or values['base_coins']<1 or values['bonus_coins']<0:raise BillingError(422,'invalid price or coin amount')
    if values['currency']!='USD':raise BillingError(422,'only USD products are supported')
    if latest is None or any(getattr(latest,key)!=value for key,value in values.items()):
        latest=CoinPackVersion(pack_id=pack.id,version=latest.version+1 if latest else 1,**values);db.add(latest);db.flush()
    audit(db,actor,'coin_pack.save',pack.id,dict(code=code,version_id=latest.id,note=data.get('note','')));db.commit()
    return next(x for x in list_packs(db,True) if x['code']==code)

def env_defaults():return dict(secret_key=os.environ.get('STRIPE_SECRET_KEY','').strip(),webhook_secret=os.environ.get('STRIPE_WEBHOOK_SECRET','').strip())
def account_config(db,code):return get_config(db,'stripe:'+code,env_defaults() if code=='env' else {})

def get_account(db,code,for_purchase=False):
    if code=='env':
        config=account_config(db,code)
        row=db.get(StripeAccount,'env')
        status=row.status if row else 'active'
        data=dict(code='env',name=row.name if row else 'Original Stripe account',status=status,is_default=row.is_default if row else True,**config)
    else:
        row=db.get(StripeAccount,code)
        if row is None:raise BillingError(404,'Stripe account not found')
        data=dict(code=row.code,name=row.name,status=row.status,is_default=row.is_default,**account_config(db,code))
    if not data.get('secret_key'):raise BillingError(503,'Stripe account is not configured')
    if for_purchase and data['status']!='active':raise BillingError(409,'Stripe account does not accept new purchases')
    return data

def account_list(db):
    rows=list(db.scalars(select(StripeAccount).order_by(StripeAccount.code)))
    if not any(x.code=='env' for x in rows) and env_defaults()['secret_key']:
        rows.insert(0,StripeAccount(code='env',name='Original Stripe account',status='active',is_default=not any(x.is_default for x in rows),livemode=env_defaults()['secret_key'].startswith('sk_live_')))
    return [dict(code=x.code,name=x.name,status=x.status,is_default=x.is_default,provider='stripe',livemode=x.livemode,remote_id=x.remote_id,**public_config(db,'stripe:'+x.code,env_defaults() if x.code=='env' else {},secret_fields=('secret_key','webhook_secret'))) for x in rows]

def select_account(db,user_id,code=None):
    if code:return get_account(db,code,True)
    configured=account_list(db)
    enabled=[x for x in configured if x['status']=='active' and x.get('secret_key_configured')]
    chosen=next((x for x in enabled if x['is_default']),enabled[0] if enabled else None)
    if chosen is None:raise BillingError(503,'no configured Stripe payment channel')
    return get_account(db,chosen['code'],True)

def _stripe_http(secret,path,data=None,method='POST',idempotency_key=None):
    payload=None if data is None else urllib.parse.urlencode(data).encode()
    request=urllib.request.Request('https://api.stripe.com/v1'+path,data=payload,method=method)
    request.add_header('Authorization','Bearer '+secret)
    if payload is not None:request.add_header('Content-Type','application/x-www-form-urlencoded')
    if idempotency_key:request.add_header('Idempotency-Key',idempotency_key)
    try:
        with urllib.request.urlopen(request,timeout=15) as response:return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:message=json.loads(exc.read()).get('error',{}).get('message','Stripe request failed')
        except Exception:message='Stripe request failed'
        error=BillingError(502,str(message)[:300]);error.definitive=exc.code in {400,402,404}
        raise error from exc
    except (urllib.error.URLError,TimeoutError) as exc:raise BillingError(503,'Stripe response uncertain; retry the same operation') from exc

def stripe_request(db,code,path,data=None,method='POST',idempotency_key=None):
    return _stripe_http(get_account(db,code)['secret_key'],path,data,method,idempotency_key)

def _stripe_identity(secret):
    # /account proves the merchant; the authenticated key namespace proves mode.
    if secret.startswith(('sk_live_','rk_live_')):live=True
    elif secret.startswith(('sk_test_','rk_test_')):live=False
    else:raise BillingError(422,'use a Stripe secret or restricted key with an explicit test/live mode')
    result=_stripe_http(secret,'/account',method='GET')
    if not str(result.get('id','')).startswith('acct_'):raise BillingError(502,'Stripe did not return a merchant identity')
    if 'livemode' in result and bool(result['livemode'])!=live:raise BillingError(409,'Stripe credential mode does not match merchant response')
    return result['id'],live,result

def _account_lock(db,code):
    from sqlalchemy import text
    key=int.from_bytes(hashlib.sha256(('stripe-account:'+code).encode()).digest()[:8],'big') & ((1<<63)-1)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})

def _account_has_history(db,code):
    return any(db.scalar(select(model).where(field==code).limit(1)) is not None for model,field in ((Order,Order.account_code),(Subscription,Subscription.account_code),(StripePriceBinding,StripePriceBinding.account_code)))

def _identity_conflict():
    return BillingError(409,dict(code='stripe_merchant_mismatch',message='This channel is bound to a different Stripe merchant or mode. Create a new channel instead.'))

def save_account(db,code,data,actor):
    _account_lock(db,code)
    row=db.get(StripeAccount,code,populate_existing=True)
    previous=account_config(db,code)
    candidate=dict(previous)
    for field in ('secret_key','webhook_secret'):
        if data.get('clear_'+field):candidate.pop(field,None)
        elif isinstance(data.get(field),str) and data[field].strip():candidate[field]=data[field].strip()
    status=data.get('status') or (row.status if row else 'draft')
    if status not in {'draft','active','disabled'}:raise BillingError(422,'invalid account status')
    history=_account_has_history(db,code)
    if history and any(previous.get(field) and not candidate.get(field) for field in ('secret_key','webhook_secret')):
        raise BillingError(409,'Historical payments require their original channel credentials; disable new purchases instead of clearing credentials')
    if status=='active' and (not candidate.get('secret_key') or not candidate.get('webhook_secret')):
        raise BillingError(422,'configure Stripe key and webhook signing secret before activating')
    expected=(row.remote_id,row.livemode) if row and row.remote_id else None
    # Existing business facts must be tied to the OLD credential before a change.
    # A failed candidate validation must never mutate either stored credential.
    if history and expected is None:
        if not previous.get('secret_key'):raise BillingError(409,'Original Stripe merchant identity is unavailable; manual channel recovery required')
        remote_id,live,_=_stripe_identity(previous['secret_key']);expected=(remote_id,live)
    verified=None
    if candidate.get('secret_key') and (expected is None or candidate['secret_key']!=previous.get('secret_key')):
        remote_id,live,_=_stripe_identity(candidate['secret_key']);verified=(remote_id,live)
        if expected is not None and verified!=expected:raise _identity_conflict()
    identity=verified or expected
    changes={k:v for k,v in data.items() if k in {'secret_key','webhook_secret','clear_secret_key','clear_webhook_secret'}}
    if candidate.get('webhook_secret') and previous.get('webhook_secret') and candidate['webhook_secret']!=previous['webhook_secret']:
        try:retained=json.loads(previous.get('webhook_secrets_history') or '[]')
        except (ValueError,TypeError):retained=[]
        retained=[x for x in retained if isinstance(x,dict) and x.get('expires_at',0)>time.time()]
        retained.append(dict(secret=previous['webhook_secret'],expires_at=int(time.time())+86400))
        changes['webhook_secrets_history']=json.dumps(retained[-3:])
    if row is None:row=StripeAccount(code=code,name=data.get('name',code),status=status);db.add(row)
    for field in ('name','is_default'):
        if field in data and data[field] is not None:setattr(row,field,data[field])
    row.status=status
    if identity:row.remote_id,row.livemode=identity
    save_config(db,'stripe:'+code,changes,secret_fields=('secret_key','webhook_secret','webhook_secrets_history'))
    if row.is_default:
        for other in db.scalars(select(StripeAccount).where(StripeAccount.code!=code)):other.is_default=False
    audit(db,actor,'stripe_account.save',payload=dict(code=code,status=row.status,remote_id=row.remote_id,livemode=row.livemode));db.commit()
    return next(x for x in account_list(db) if x['code']==code)

def test_account(db,code):
    _account_lock(db,code)
    config=get_account(db,code)
    remote_id,live,result=_stripe_identity(config['secret_key'])
    row=db.get(StripeAccount,code)
    if row and row.remote_id and (row.remote_id,row.livemode)!=(remote_id,live):raise _identity_conflict()
    if row is None:row=StripeAccount(code=code,name=config['name'],status=config['status'],is_default=config['is_default']);db.add(row)
    row.remote_id=remote_id;row.livemode=live;db.commit()
    return dict(ok=True,account_id=remote_id,country=result.get('country'),livemode=live)

def return_url(value,kind):
    base=os.environ.get('H5_PUBLIC_BASE_URL','https://jjtv-h5.8jj.net').rstrip('/')
    if value is None:return base+'/?checkout='+kind
    origins={'https://jjtv.8jj.net','https://jjtv-h5.8jj.net'}
    for entry in [base,os.environ.get('WEB_PUBLIC_BASE_URL','')]+os.environ.get('CHECKOUT_RETURN_ORIGINS','').split(','):
        if entry:
            parsed=urllib.parse.urlsplit(entry.strip());origins.add(parsed.scheme+'://'+parsed.netloc)
    parsed=urllib.parse.urlsplit(value)
    if parsed.username or parsed.password or parsed.scheme not in {'https','http'} or parsed.scheme+'://'+parsed.netloc not in origins:raise BillingError(422,'checkout return URL must use an allowed JJTV origin')
    return value

def product_snapshot(db,kind,code):
    from app.modules.billing import service as billing
    if kind=='coin_pack':
        item=next((x for x in list_packs(db) if x['code']==code),None)
        if item is None:raise BillingError(404,'coin pack not found')
        return dict(product_type=kind,code=code,name=item['name_en'],name_zh=item['name_zh'],version_id=item['version_id'],price_cents=item['price_cents'],currency=item['currency'],base_coins=item['base_coins'],bonus_coins=item['bonus_coins'],billing_mode='one_time')
    if kind!='membership':raise BillingError(422,'unknown product type')
    plan=db.scalar(select(Plan).where(Plan.code==code,Plan.status=='active'))
    if not plan:raise BillingError(404,'plan not found')
    version=billing._latest_version(db,plan)
    return dict(product_type=kind,code=code,name=plan.name_en,name_zh=plan.name_zh,version_id=version.id,price_cents=version.price_cents,currency=version.currency,base_coins=version.base_coins,bonus_coins=version.bonus_coins,benefits=version.benefits,days=billing.PLAN_DAYS.get(code,30),billing_mode=billing.billing_mode(db))

def ensure_price(db,account,snapshot):
    from sqlalchemy import text
    fingerprint=account+':'+snapshot['product_type']+':'+str(snapshot['version_id'])+':'+snapshot['billing_mode']
    lock=int.from_bytes(hashlib.sha256(fingerprint.encode()).digest()[:8],'big') & ((1<<63)-1)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':lock})
    values=dict(account_code=account,product_type=snapshot['product_type'],version_id=snapshot['version_id'],billing_mode=snapshot['billing_mode'])
    binding=db.scalar(select(StripePriceBinding).filter_by(**values))
    if binding:return binding.price_id
    price=None
    if account=='env' and snapshot['product_type']=='membership':
        version=db.get(PlanVersion,snapshot['version_id'])
        price=version.stripe_price_id if snapshot['billing_mode']=='subscription' else version.stripe_one_time_price_id
    if price is None:
        data={'unit_amount':str(snapshot['price_cents']),'currency':snapshot['currency'].lower(),'product_data[name]':snapshot['name'],'metadata[version_id]':str(snapshot['version_id'])}
        if snapshot['billing_mode']=='subscription':data['recurring[interval]']={'weekly':'week','monthly':'month','yearly':'year'}.get(snapshot['code'],'month')
        key='price:'+account+':'+snapshot['product_type']+':'+str(snapshot['version_id'])+':'+snapshot['billing_mode']
        price=stripe_request(db,account,'/prices',data,idempotency_key=key)['id']
    db.add(StripePriceBinding(**values,price_id=price));db.commit();return price

def checkout_conflict(order,code,message,action,include_url=False):
    detail=dict(code=code,message=message,order_no=order.order_no,action=action)
    if include_url and order.checkout_url:detail['checkout_url']=order.checkout_url
    return BillingError(409,detail)

def _checkout_pending(db,order):
    if order.billing_mode!='subscription' or order.product_type!='membership':return False
    if order.status=='created':return True
    if order.status=='failed':return not (order.checkout_context or {}).get('session_expired')
    # A completed Checkout is not an entitlement. Keep its reservation until the
    # authoritative first invoice is settled, including imported paid states.
    return order.status=='paid' and bool(order.checkout_key) and db.scalar(select(CommerceSettlement).where(CommerceSettlement.order_id==order.id)) is None

def _checkout_remote_state(db,order):
    context=order.checkout_context or {}
    if context.get('payment_status')=='failed':
        raise checkout_conflict(order,'checkout_recovery_required','This subscription payment failed and its remote subscription needs support reconciliation before another checkout.','contact_support')
    if context.get('payment_status')=='paid' or context.get('session_complete'):
        raise checkout_conflict(order,'checkout_payment_pending','Payment is processing. Wait for the first invoice before starting another subscription.','wait_for_payment')
    if not order.external_txn_id:
        if not context.get('request'):
            raise checkout_conflict(order,'checkout_recovery_required','This historical checkout needs support recovery before another subscription can be started.','contact_support')
        return 'creating'
    remote=stripe_request(db,order.account_code,'/checkout/sessions/'+urllib.parse.quote(order.external_txn_id,safe=''),method='GET')
    if remote.get('id')!=order.external_txn_id:
        raise BillingError(502,'Stripe returned an unexpected Checkout session')
    if remote.get('status')=='complete' or remote.get('payment_status')=='paid':
        order.checkout_context={**context,'session_complete':True,'payment_status':remote.get('payment_status')};db.commit()
        raise checkout_conflict(order,'checkout_payment_pending','Payment is processing. Wait for the first invoice before starting another subscription.','wait_for_payment')
    if remote.get('status')=='expired':
        # Keep the wallet mutex until a replacement reservation is persisted.
        # Callers returning an expiry response commit the terminal fact instead.
        order.status='failed';order.checkout_context={**context,'session_expired':True};db.flush();return 'expired'
    if remote.get('status')!='open':
        raise checkout_conflict(order,'checkout_recovery_required','Stripe could not confirm whether this checkout is still payable. Contact support.','contact_support')
    return 'open'

def _checkout_result(order,reused=False):
    return dict(url=order.checkout_url,session_id=order.external_txn_id,order_no=order.order_no,reused=reused)

def assert_no_renewing_subscription(db,user_id):
    # Event.created has only second precision. A same-second cancel/resume or
    # delayed lifecycle event is not proof that an old subscription stopped
    # renewing. Confirm planned cancellations through their ORIGINAL merchant,
    # while the wallet mutex still protects the replacement reservation.
    rows=db.scalars(select(Subscription).where(Subscription.user_id==user_id,Subscription.stripe_subscription_id.is_not(None),Subscription.status=='active').execution_options(populate_existing=True))
    for row in rows:
        fact=db.get(StripeSubscriptionLifecycle,(row.account_code,row.stripe_subscription_id),populate_existing=True)
        if fact is not None and fact.user_id!=user_id:raise BillingError(409,'Subscription identity needs manual reconciliation before another purchase')
        if fact is not None and fact.deleted:continue
        if row.canceled_at is None:raise BillingError(409,'an active renewing subscription already exists; manage it before starting another')
        remote=stripe_request(db,row.account_code,'/subscriptions/'+urllib.parse.quote(row.stripe_subscription_id,safe=''),method='GET')
        if not isinstance(remote,dict) or remote.get('id')!=row.stripe_subscription_id:raise BillingError(409,'Could not verify the original subscription cancellation; retry or contact support before another purchase')
        if remote.get('status') in {'canceled','incomplete_expired'}:continue
        if remote.get('status') not in {'active','trialing'} or type(remote.get('cancel_at_period_end')) is not bool:
            raise BillingError(409,'Original subscription cancellation is not confirmed; retry or contact support before another purchase')
        if not remote['cancel_at_period_end']:raise BillingError(409,'an active renewing subscription already exists; manage it before starting another')

def checkout(db,*,user_id,email,product_type,code,idempotency_key,account_code=None,success_url=None,cancel_url=None,source_drama_id=None,attribution_pid=None):
    success=return_url(success_url,'success');cancel=return_url(cancel_url,'cancel')
    key='checkout:'+str(user_id)+':'+hashlib.sha256(idempotency_key.encode()).hexdigest()
    wallet_lock(db,user_id)
    order=db.scalar(select(Order).where(Order.checkout_key==key))
    reused=order is not None
    if order:
        if order.product_type!=product_type or order.product_snapshot.get('code')!=code:raise BillingError(409,'idempotency key was used for another product')
        if account_code and order.account_code!=account_code:raise BillingError(409,'idempotency key was used for another payment channel')
        if order.billing_mode=='subscription':
            if order.status=='failed' and (order.checkout_context or {}).get('session_expired'):raise checkout_conflict(order,'checkout_expired','This checkout has expired. Start a new purchase.','new_purchase')
            if not _checkout_pending(db,order):raise checkout_conflict(order,'checkout_already_paid','This order was already paid. View your orders.','view_orders')
            state=_checkout_remote_state(db,order)
            if state=='expired':
                db.commit()
                raise checkout_conflict(order,'checkout_expired','This checkout has expired. Start a new purchase.','new_purchase')
            if state=='open':return _checkout_result(order,True)
        elif order.checkout_url:return _checkout_result(order,True)
        account=get_account(db,order.account_code);snapshot=order.product_snapshot
    else:
        snapshot=product_snapshot(db,product_type,code)
        if snapshot['billing_mode']=='subscription':
            # Persisted orders reserve the right to create ONE subscription even
            # while a remote response or its first invoice has not arrived.
            pending=list(db.scalars(select(Order).where(Order.user_id==user_id,Order.product_type=='membership',Order.billing_mode=='subscription',Order.status.in_(('created','paid','failed'))).order_by(Order.id)))
            for candidate in pending:
                if not _checkout_pending(db,candidate):continue
                state=_checkout_remote_state(db,candidate)
                if state=='expired':continue
                if candidate.product_snapshot.get('code')!=code or (account_code and candidate.account_code!=account_code):
                    raise checkout_conflict(candidate,'pending_subscription_checkout','Finish or cancel your existing checkout before choosing another subscription.','resume_or_cancel',state=='open')
                order=candidate;account=get_account(db,order.account_code);snapshot=order.product_snapshot;reused=True
                if state=='open':return _checkout_result(order,True)
                break
            if order is None:
                assert_no_renewing_subscription(db,user_id)
        if order is None:
            account=select_account(db,user_id,account_code)
            if snapshot['price_cents']<50:raise BillingError(422,'Stripe price must be at least USD 0.50')
            if source_drama_id is not None:
                from app.modules.content.models import Drama
                drama=db.get(Drama,source_drama_id)
                if drama is None or drama.tenant_id!='jjtv' or drama.status!='published':raise BillingError(404,'source drama not found')
            order=Order(order_no=secrets.token_hex(12),user_id=user_id,plan_version_id=snapshot['version_id'] if product_type=='membership' else None,product_type=product_type,product_snapshot=snapshot,amount_cents=snapshot['price_cents'],currency=snapshot['currency'],billing_mode=snapshot['billing_mode'],status='created',account_code=account['code'],checkout_key=key,source_drama_id=source_drama_id,attribution_pid=attribution_pid,checkout_context={'request':dict(success_url=success,cancel_url=cancel,email=email)})
            db.add(order);db.commit()
    price=ensure_price(db,account['code'],snapshot)
    # ensure_price may commit. Reacquire the user mutex before refreshing or
    # creating a Session so concurrent tabs reuse the same remote idempotency key.
    wallet_lock(db,user_id)
    order=db.get(Order,order.id,populate_existing=True)
    if order.billing_mode=='subscription':
        state=_checkout_remote_state(db,order)
        if state=='expired':
            db.commit()
            raise checkout_conflict(order,'checkout_expired','This checkout has expired. Start a new purchase.','new_purchase')
        if state=='open':return _checkout_result(order,True)
    elif order.checkout_url:return _checkout_result(order,True)
    context=(order.checkout_context or {}).get('request')
    if not context:raise checkout_conflict(order,'checkout_recovery_required','Original checkout request is unavailable. Contact support.','contact_support')
    data={'mode':'subscription' if snapshot['billing_mode']=='subscription' else 'payment','line_items[0][price]':price,'line_items[0][quantity]':'1','success_url':context['success_url'],'cancel_url':context['cancel_url'],'client_reference_id':str(user_id),'metadata[commerce_order_no]':order.order_no,'metadata[user_id]':str(user_id),'metadata[plan_code]':snapshot['code'],'metadata[account_code]':account['code']}
    if context.get('email'):data['customer_email']=context['email']
    if snapshot['billing_mode']=='subscription':
        data['subscription_data[metadata][commerce_order_no]']=order.order_no
        data['subscription_data[metadata][user_id]']=str(user_id)
        data['subscription_data[metadata][plan_code]']=snapshot['code']
    else:data['payment_intent_data[metadata][commerce_order_no]']=order.order_no
    remote=stripe_request(db,account['code'],'/checkout/sessions',data,idempotency_key=order.checkout_key)
    order.external_txn_id=remote['id'];order.checkout_url=remote['url'];db.commit()
    return _checkout_result(order,reused)

def cancel_checkout(db,user_id,order_no):
    wallet_lock(db,user_id)
    order=db.scalar(select(Order).where(Order.order_no==order_no,Order.user_id==user_id).with_for_update())
    if order is None:raise BillingError(404,'order not found')
    if order.billing_mode!='subscription' or order.product_type!='membership':raise BillingError(409,'Only unpaid subscription checkouts can be canceled')
    if order.status=='failed' and (order.checkout_context or {}).get('session_expired'):return dict(order_no=order.order_no,status='failed',canceled=True)
    if not _checkout_pending(db,order):raise checkout_conflict(order,'checkout_already_paid','This order is already settled and cannot be canceled.','view_orders')
    state=_checkout_remote_state(db,order)
    if state=='creating':raise checkout_conflict(order,'checkout_in_progress','Checkout creation is still processing. Retry the original purchase before canceling.','retry')
    if state=='open':
        remote=stripe_request(db,order.account_code,'/checkout/sessions/'+urllib.parse.quote(order.external_txn_id,safe='')+'/expire',{},idempotency_key='expire:'+order.order_no)
        if remote.get('id')!=order.external_txn_id or remote.get('status')!='expired':
            raise checkout_conflict(order,'checkout_payment_pending','Stripe has not confirmed cancellation. Wait for payment status before retrying.','wait_for_payment')
        order.status='failed';order.checkout_context={**(order.checkout_context or {}),'session_expired':True}
    db.commit()
    return dict(order_no=order.order_no,status='failed',canceled=True)

def credit_order(db,order,wallet):
    coins=int(order.product_snapshot.get('base_coins',0))+int(order.product_snapshot.get('bonus_coins',0))
    if coins<=0:return
    ensure_sources(db,wallet)
    wallet.balance+=coins
    db.add(CoinLedgerEntry(user_id=order.user_id,amount=coins,balance_after=wallet.balance,biz_type='purchase',biz_key='purchase:'+order.order_no,note=order.product_type))
    db.add(CoinSource(user_id=order.user_id,order_id=order.id,original=coins,remaining=coins,reserved=0))

def settle_order(db,order,settlement_key,period_start=None,period_end=None):
    wallet=wallet_lock(db,order.user_id)
    order=db.get(Order,order.id,with_for_update=True,populate_existing=True)
    if order.status in {'refund_pending','refunded','refund_review'}:return dict(replayed=True,order_no=order.order_no)
    if order.status=='paid' and (period_start is None or db.scalar(select(CommerceSettlement).where(CommerceSettlement.order_id==order.id)) is not None):return dict(replayed=True,order_no=order.order_no)
    if db.get(CommerceSettlement,settlement_key):return dict(replayed=True,order_no=order.order_no)
    order.status='paid';order.paid_at=now()
    db.add(CommerceSettlement(key=settlement_key,order_id=order.id));credit_order(db,order,wallet)
    if order.product_type=='membership':
        if period_start is None:
            end=grant_access_until(db,order.user_id)
            start=max(now(),end or now());finish=start+timedelta(days=order.product_snapshot.get('days',30))
        else:start,finish=period_start,period_end
        db.add(MembershipGrant(order_id=order.id,user_id=order.user_id,starts_at=start,ends_at=finish,status='active',access_consumed=False))
    db.flush()
    from app.modules.attribution.service import record_payment
    record_payment(db,order_no=order.order_no,user_id=order.user_id,pid=order.attribution_pid,amount_cents=order.amount_cents,currency=order.currency)
    db.commit();return dict(order_no=order.order_no,status='paid')

def signature(db,code,body,header):
    config=account_config(db,code)
    secrets_to_try=[config.get('webhook_secret')]
    try:history=json.loads(config.get('webhook_secrets_history') or '[]')
    except (ValueError,TypeError):history=[]
    secrets_to_try.extend(x.get('secret') for x in history if isinstance(x,dict) and x.get('expires_at',0)>time.time())
    secrets_to_try=[x for x in secrets_to_try if isinstance(x,str) and x]
    if not secrets_to_try:raise BillingError(503,'Stripe webhook signing secret is not configured')
    pieces=[x.partition('=') for x in (header or '').split(',')]
    stamps=[int(v) for k,_,v in pieces if k=='t' and v.isdigit()]
    if not stamps or abs(time.time()-max(stamps))>300:raise BillingError(400,'invalid or expired Stripe signature')
    signed=str(max(stamps)).encode()+b'.'+body
    for secret in secrets_to_try:
        expected=hmac.new(secret.encode(),signed,hashlib.sha256).hexdigest()
        if any(k=='v1' and hmac.compare_digest(v,expected) for k,_,v in pieces):return
    raise BillingError(401,'bad Stripe signature')

def record_subscription_lifecycle(db,code,payload,obj,order):
    subscription=obj.get('id')
    sub=db.scalar(select(Subscription).where(Subscription.stripe_subscription_id==subscription,Subscription.account_code==code,Subscription.managed_commerce.is_(True)))
    order=order or db.scalar(select(Order).where(Order.subscription_ref==subscription,Order.account_code==code,Order.checkout_key.is_not(None)).order_by(Order.id))
    if order is None:return None
    if order.billing_mode!='subscription' or order.product_type!='membership' or not order.checkout_key:return None
    wallet_lock(db,order.user_id)
    order=db.get(Order,order.id,populate_existing=True)
    if order.subscription_ref and order.subscription_ref!=subscription:raise BillingError(409,'subscription does not match frozen order')
    sub=db.scalar(select(Subscription).where(Subscription.stripe_subscription_id==subscription,Subscription.account_code==code,Subscription.managed_commerce.is_(True)).execution_options(populate_existing=True))
    order.subscription_ref=subscription
    fact=db.get(StripeSubscriptionLifecycle,(code,subscription),with_for_update=True,populate_existing=True)
    if fact is None:
        fact=StripeSubscriptionLifecycle(account_code=code,subscription_id=subscription,order_id=order.id,user_id=order.user_id,deleted=False,cancel_at_period_end=False,last_event_created=0,last_event_id='');db.add(fact)
    event_created=int(payload.get('created') or 0)
    deleted=payload.get('type')=='customer.subscription.deleted' or obj.get('status') in {'canceled','incomplete_expired'}
    cancel=deleted or bool(obj.get('cancel_at_period_end'))
    # A terminal deletion is monotonic; older snapshots cannot resurrect billing.
    if deleted or (not fact.deleted and (event_created>fact.last_event_created or (event_created==fact.last_event_created and cancel))):
        fact.deleted=fact.deleted or deleted
        fact.cancel_at_period_end=cancel
        fact.canceled_at=datetime.fromtimestamp(obj['canceled_at'],UTC) if obj.get('canceled_at') else (fact.canceled_at or now()) if cancel else None
        fact.last_event_created=max(event_created,fact.last_event_created);fact.last_event_id=str(payload.get('id') or '')
    if sub is not None:
        if sub.user_id!=order.user_id:raise BillingError(409,'subscription owner does not match frozen order')
        sub.canceled_at=fact.canceled_at if fact.deleted or fact.cancel_at_period_end else None
    db.commit();return dict(received=True,pending=sub is None,order_no=order.order_no)

def process_event(db,code,payload):
    kind=payload.get('type','');obj=(payload.get('data') or {}).get('object') or {}
    metadata=obj.get('metadata') or {};number=metadata.get('commerce_order_no')
    order=db.scalar(select(Order).where(Order.order_no==number,Order.account_code==code)) if number else None
    if kind.startswith('checkout.session.'):
        order=order or db.scalar(select(Order).where(Order.external_txn_id==obj.get('id'),Order.account_code==code,Order.checkout_key.is_not(None)))
        if order is None:return None
        wallet_lock(db,order.user_id)
        order=db.get(Order,order.id,with_for_update=True,populate_existing=True)
        context=order.checkout_context or {}
        if kind in {'checkout.session.expired','checkout.session.async_payment_failed'}:
            if order.status=='created' and context.get('payment_status')!='paid':
                if kind=='checkout.session.async_payment_failed' and order.billing_mode=='subscription':
                    # A failed asynchronous first payment may leave a remote
                    # subscription retrying. Only expiry proves it unpayable.
                    order.checkout_context={**context,'session_complete':True,'payment_status':'failed'}
                elif not context.get('session_complete'):
                    order.status='failed'
                    if kind=='checkout.session.expired':order.checkout_context={**context,'session_expired':True}
            db.commit();return dict(order_no=order.order_no,status=order.status)
        if kind not in {'checkout.session.completed','checkout.session.async_payment_succeeded'}:return dict(ignored=kind)
        if obj.get('payment_status')!='paid':
            if order.billing_mode=='subscription':
                order.checkout_context={**context,'session_complete':True,'payment_status':obj.get('payment_status')}
                order.subscription_ref=obj.get('subscription') or order.subscription_ref;db.commit()
            return dict(pending=True,order_no=order.order_no)
        if int(obj.get('amount_total',-1))!=order.amount_cents or str(obj.get('currency','')).upper()!=order.currency:raise BillingError(409,'Stripe amount does not match frozen order')
        order.payment_intent_id=obj.get('payment_intent') or order.payment_intent_id
        order.subscription_ref=obj.get('subscription') or order.subscription_ref
        if order.billing_mode=='subscription':
            # Invoice remains the sole settlement authority; completion retains
            # the checkout reservation even if the invoice webhook is delayed.
            order.checkout_context={**context,'session_complete':True,'payment_status':'paid'}
            db.commit();return dict(pending=True,order_no=order.order_no)
        if not order.payment_intent_id:raise BillingError(409,'paid Checkout has no payment intent')
        return settle_order(db,order,code+':pi:'+order.payment_intent_id)
    if kind in {'invoice.paid','invoice.payment_succeeded'}:
        subscription=obj.get('subscription') or ((obj.get('parent') or {}).get('subscription_details') or {}).get('subscription')
        if not subscription:return None
        initial=db.scalar(select(Order).where(Order.subscription_ref==subscription,Order.account_code==code,Order.checkout_key.is_not(None)).order_by(Order.id))
        if initial is None:
            parent_meta=((obj.get('parent') or {}).get('subscription_details') or {}).get('metadata') or obj.get('subscription_details',{}).get('metadata',{})
            initial=db.scalar(select(Order).where(Order.order_no==parent_meta.get('commerce_order_no'),Order.account_code==code))
        if initial is None:return None
        wallet_lock(db,initial.user_id)
        initial=db.get(Order,initial.id,populate_existing=True)
        if initial.subscription_ref and initial.subscription_ref!=subscription:raise BillingError(409,'subscription does not match frozen order')
        initial.subscription_ref=subscription
        order=db.scalar(select(Order).where(Order.invoice_id==obj['id']))
        if order is None:
            if initial.status in {'created','paid'} and initial.invoice_id is None and db.scalar(select(CommerceSettlement).where(CommerceSettlement.order_id==initial.id)) is None:order=initial
            else:
                order=Order(order_no=secrets.token_hex(12),user_id=initial.user_id,plan_version_id=initial.plan_version_id,product_type='membership',product_snapshot=dict(initial.product_snapshot),amount_cents=initial.product_snapshot['price_cents'],currency=initial.product_snapshot['currency'],account_code=code,billing_mode='subscription',status='created',subscription_ref=subscription,attribution_pid=initial.attribution_pid,source_drama_id=initial.source_drama_id)
                db.add(order);db.flush()
            order.invoice_id=obj['id'];order.payment_intent_id=obj.get('payment_intent');order.subscription_ref=subscription
        lines=(obj.get('lines') or {}).get('data') or []
        line=next((x for x in lines if x.get('period')),None)
        if not line:raise BillingError(409,'invoice has no paid entitlement period')
        period=line['period'];start=datetime.fromtimestamp(period['start'],UTC);end=datetime.fromtimestamp(period['end'],UTC)
        if end<=start or int(obj.get('amount_paid',-1))!=order.amount_cents or str(obj.get('currency','')).upper()!=order.currency:raise BillingError(409,'invoice period or amount mismatch')
        # Keep a merchant binding for cancellation while paid grants own access.
        sub=db.scalar(select(Subscription).where(Subscription.stripe_subscription_id==subscription,Subscription.account_code==code).with_for_update())
        if sub is not None and sub.user_id!=order.user_id:raise BillingError(409,'subscription owner does not match frozen order')
        if sub is None:
            old=db.scalar(select(Subscription).where(Subscription.user_id==order.user_id,Subscription.status=='active').with_for_update())
            if old and old.stripe_subscription_id and old.stripe_subscription_id!=subscription and old.canceled_at is None:
                raise BillingError(409,'another renewing subscription already exists')
            preserved_pass=None
            if old:
                # Keep old merchant/subscription IDs and version as history.
                # Managed paid periods live in their own grants; legacy/manual
                # windows must remain usable through the new active binding.
                windows=[aware(old.pass_end)]
                if not old.managed_commerce:windows.append(aware(old.current_period_end))
                preserved_pass=max((value for value in windows if value is not None),default=None)
                old.status='canceled';db.flush()
            version=db.get(PlanVersion,order.plan_version_id)
            sub=Subscription(user_id=order.user_id,plan_id=version.plan_id,plan_version_id=version.id,status='active',current_period_start=start,current_period_end=end,pass_end=preserved_pass,managed_commerce=True,account_code=code,stripe_subscription_id=subscription,billing_mode='subscription')
            db.add(sub)
        if end>aware(sub.current_period_end):sub.current_period_start=start;sub.current_period_end=end
        fact=db.get(StripeSubscriptionLifecycle,(code,subscription))
        if fact is not None:sub.canceled_at=fact.canceled_at if fact.deleted or fact.cancel_at_period_end else None
        sub.stripe_customer_id=obj.get('customer') or sub.stripe_customer_id
        return settle_order(db,order,code+':invoice:'+obj['id'],start,end)
    if kind in {'refund.updated','refund.created','charge.refunded'}:
        query=select(CommerceRefund).join(Order,Order.id==CommerceRefund.order_id).where(Order.account_code==code)
        refund=db.scalar(query.where(CommerceRefund.refund_id==obj.get('id'))) if not kind.startswith('charge') else None
        if refund is None and obj.get('payment_intent'):
            refund=db.scalar(query.where(Order.payment_intent_id==obj['payment_intent']))
        if refund is None:
            external_order=db.scalar(select(Order).where(Order.account_code==code,Order.payment_intent_id==obj.get('payment_intent'))) if obj.get('payment_intent') else None
            if external_order is None:return None
            wallet_lock(db,external_order.user_id)
            # Dashboard/other-provider initiated refunds are facts, but cannot
            # safely reclaim arbitrary previously consumed entitlement sources.
            external_order=db.get(Order,external_order.id,with_for_update=True,populate_existing=True)
            existing=db.scalar(select(CommerceRefund).where(CommerceRefund.order_id==external_order.id))
            if existing:return dict(manual_review=existing.status=='manual_review',refund_status=existing.status)
            db.add(CommerceRefund(order_id=external_order.id,idempotency_key='external:'+external_order.order_no,status='manual_review',refund_id=obj.get('id') if kind!='charge.refunded' else None,reason='Provider initiated refund',error='Provider refund was not requested through the verified entitlement workflow; manual reconciliation required'))
            external_order.status='refund_review';db.commit();return dict(manual_review=True)
        if refund.status=='manual_review':return dict(manual_review=True,refund_status=refund.status)
        if kind=='charge.refunded':
            order=db.get(Order,refund.order_id)
            if int(obj.get('amount_refunded',0))!=order.amount_cents:return dict(manual_review=True)
            state='succeeded'
        else:
            order=db.get(Order,refund.order_id)
            if not matching_refund_object(db,order,refund,obj):db.commit();return dict(manual_review=True,refund_status=refund.status)
            if not refund.refund_id:refund.refund_id=obj.get('id')
            state=obj.get('status')
        finalize_refund(db,refund,state);db.commit();return dict(refund_status=refund.status)
    if kind.startswith('customer.subscription.'):
        return record_subscription_lifecycle(db,code,payload,obj,order)
    return None

def refund_out(row):return dict(id=row.id,status=row.status,refund_id=row.refund_id,error=row.error)

def verify_source_proof(db,order,source):
    """Legacy debits cannot become refundable by replenishing the balance.

    Ledger IDs order facts under the wallet mutex. PostgreSQL NOW timestamps
    cannot order transactions that waited for that mutex. All debits after a
    purchased lot require exact FIFO allocation, or a genuine source-backed
    refund reservation. Unknown historical adjustments require manual review.
    """
    credit=db.scalar(select(CoinLedgerEntry).where(CoinLedgerEntry.user_id==order.user_id,CoinLedgerEntry.biz_key=='purchase:'+order.order_no))
    if credit is None or credit.amount!=source.original:
        raise BillingError(409,'purchased coin source lacks complete ledger proof; manual review required')
    debits=list(db.scalars(select(CoinLedgerEntry).where(CoinLedgerEntry.user_id==order.user_id,CoinLedgerEntry.id>credit.id,CoinLedgerEntry.amount<0).order_by(CoinLedgerEntry.id)))
    if not debits:return
    allocations=dict(db.execute(select(CoinAllocation.ledger_id,func.sum(CoinAllocation.amount)).where(CoinAllocation.ledger_id.in_([debit.id for debit in debits])).group_by(CoinAllocation.ledger_id)).all())
    for debit in debits:
        if allocations.get(debit.id,0)==-debit.amount:continue
        if debit.biz_type=='refund_reserve' and debit.biz_key.startswith('refund_reserve:'):
            number=debit.biz_key.removeprefix('refund_reserve:')
            reserve=db.execute(select(CoinSource.original,CommerceRefund.id).join(Order,Order.id==CoinSource.order_id).join(CommerceRefund,CommerceRefund.order_id==Order.id).where(Order.order_no==number,Order.user_id==order.user_id,CommerceRefund.status.in_(('pending','succeeded','failed','canceled')))).first()
            if reserve is not None and reserve[0]==-debit.amount:continue
        raise BillingError(409,'unmatched legacy or administrative coin debit after purchase; manual refund review required')

def request_refund(db,order_no,actor,reason,idempotency_key):
    order=db.scalar(select(Order).where(Order.order_no==order_no))
    if order is None:raise BillingError(404,'order not found')
    wallet=wallet_lock(db,order.user_id)
    order=db.get(Order,order.id,with_for_update=True,populate_existing=True)
    existing=db.scalar(select(CommerceRefund).where(CommerceRefund.order_id==order.id))
    if existing:
        db.commit()
        if existing.status in {'succeeded','failed','canceled','manual_review'}:return refund_out(existing)
        refund=existing
    else:
        if order.status!='paid' or not order.checkout_key and not order.invoice_id or not order.product_snapshot or not order.payment_intent_id:raise BillingError(409,'legacy or unverified payment requires manual refund review')
        source=db.scalar(select(CoinSource).where(CoinSource.order_id==order.id).with_for_update())
        grant=db.scalar(select(MembershipGrant).where(MembershipGrant.order_id==order.id).with_for_update())
        if source:
            verify_source_proof(db,order,source)
        if source and (source.remaining!=source.original or source.reserved):raise BillingError(409,'purchased coins have been consumed; manual review required')
        if grant and (grant.access_consumed or grant.status!='active'):raise BillingError(409,'membership entitlement was consumed or changed; manual review required')
        if grant and db.scalar(select(AuditLog.id).where(AuditLog.target_type=='user',AuditLog.target_id==order.user_id,AuditLog.action.in_(('membership_grant','membership_revoke')),AuditLog.created_at>=order.paid_at).limit(1)):
            raise BillingError(409,'membership was manually adjusted after purchase; manual refund review required')
        if not source and not grant:raise BillingError(409,'no provably unconsumed entitlement source')
        refund=CommerceRefund(order_id=order.id,idempotency_key='refund:'+order.order_no,status='pending',reason=reason);db.add(refund);db.flush()
        if source:
            if wallet.balance<source.original:raise BillingError(409,'wallet changed; manual review required')
            source.reserved=source.remaining;source.remaining=0;wallet.balance-=source.reserved
            db.add(CoinLedgerEntry(user_id=order.user_id,amount=-source.reserved,balance_after=wallet.balance,biz_type='refund_reserve',biz_key='refund_reserve:'+order.order_no,note=reason))
        if grant:grant.status='refund_pending'
        order.status='refund_pending';audit(db,actor,'commerce.refund.request',order.id,dict(reason=reason));db.commit()
    # Unknown network outcomes deliberately retain reservations for the same-key retry.
    try:remote=stripe_request(db,order.account_code,'/refunds',{'payment_intent':order.payment_intent_id,'amount':str(order.amount_cents)},idempotency_key=refund.idempotency_key)
    except BillingError as exc:
        refund.error=exc.detail
        if getattr(exc,'definitive',False):finalize_refund(db,refund,'failed')
        db.commit();raise
    if not matching_refund_object(db,order,refund,remote):db.commit();return refund_out(refund)
    refund.refund_id=remote['id'];finalize_refund(db,refund,remote.get('status','pending'));db.commit();return refund_out(refund)

def matching_refund_object(db,order,refund,remote):
    """Only the full refund we requested may settle frozen entitlement sources."""
    mismatch=(refund.refund_id is not None and remote.get('id')!=refund.refund_id)
    if remote.get('status')=='succeeded':
        mismatch=mismatch or type(remote.get('amount')) is not int or remote['amount']!=order.amount_cents
    if remote.get('currency') is not None:
        mismatch=mismatch or str(remote['currency']).upper()!=order.currency.upper()
    if mismatch:
        refund.status='manual_review';refund.error='Refund identity or full amount does not match the frozen request; manual reconciliation required';order.status='refund_review';db.flush();return False
    return True

def finalize_refund(db,refund,status):
    order=db.get(Order,refund.order_id);wallet=wallet_lock(db,order.user_id)
    refund=db.get(CommerceRefund,refund.id,with_for_update=True,populate_existing=True)
    if refund.status in {'succeeded','failed','canceled','manual_review'}:return
    source=db.scalar(select(CoinSource).where(CoinSource.order_id==order.id).with_for_update())
    grant=db.scalar(select(MembershipGrant).where(MembershipGrant.order_id==order.id).with_for_update())
    if refund.status!='pending' or order.status!='refund_pending' or (source is None and grant is None) or (source is not None and (source.remaining!=0 or source.reserved!=source.original)) or (grant is not None and (grant.status!='refund_pending' or grant.access_consumed)):
        refund.status='manual_review';refund.error='Refund sources are not completely frozen; manual reconciliation required';order.status='refund_review';db.flush();return
    if status=='succeeded':
        if source:source.reserved=0
        if grant:grant.status='refunded'
        order.status='refunded';order.refunded_at=now();refund.status='succeeded';refund.error=''
    elif status in {'failed','canceled'}:
        if source and source.reserved:
            amount=source.reserved;source.reserved=0;source.remaining=amount;wallet.balance+=amount
            db.add(CoinLedgerEntry(user_id=order.user_id,amount=amount,balance_after=wallet.balance,biz_type='refund_release',biz_key='refund_release:'+order.order_no,note='Stripe refund failed'))
        if grant:grant.status='active'
        order.status='paid';refund.status=status
    else:refund.status='pending'
    db.flush()

def customer_out(user):
    return dict(id=user.id,email=user.email,display_name=user.display_name,public_id=user.public_id)

def order_out(db,row,include_customer=False):
    snapshot=row.product_snapshot or {}
    version=db.get(PlanVersion,row.plan_version_id) if row.plan_version_id else None
    plan=db.get(Plan,version.plan_id) if version else None
    item=dict(order_no=row.order_no,product_type=row.product_type,product_code=snapshot.get('code') or (plan.code if plan else ''),product_name=snapshot.get('name') or (plan.name_en if plan else ''),plan_code=snapshot.get('code') or (plan.code if plan else ''),plan_name_en=snapshot.get('name') or (plan.name_en if plan else ''),plan_name_zh=snapshot.get('name_zh',''),amount_cents=row.amount_cents,currency=row.currency,status=row.status,billing_mode=row.billing_mode,account_code=row.account_code,paid_at=row.paid_at.isoformat() if row.paid_at else None,created_at=row.created_at.isoformat(),coins=snapshot.get('base_coins',0)+snapshot.get('bonus_coins',0))
    if include_customer:
        from app.modules.account.models import User
        user=db.get(User,row.user_id)
        item.update(user_id=row.user_id,user=customer_out(user) if user else None,external_txn_id=row.external_txn_id)
    return item

def list_orders(db,user_id=None,limit=50,offset=0):
    stmt=select(Order).order_by(Order.created_at.desc(),Order.id.desc())
    if user_id is not None:stmt=stmt.where(Order.user_id==user_id)
    rows=list(db.scalars(stmt.offset(offset).limit(limit+1)))
    return dict(items=[order_out(db,row) for row in rows[:limit]],has_more=len(rows)>limit)

def filtered_admin_orders(q=None,status=None,date_from=None,date_to=None,product_type=None,account_code=None,billing_mode=None):
    from app.modules.account.models import User
    from sqlalchemy import or_
    from zoneinfo import ZoneInfo
    stmt=select(Order)
    if q and q.strip():
        value=q.strip()
        terms=[Order.order_no.icontains(value,autoescape=True),Order.external_txn_id.icontains(value,autoescape=True),User.email.icontains(value,autoescape=True),User.display_name.icontains(value,autoescape=True),User.public_id.icontains(value,autoescape=True)]
        if value.isdigit():terms.append(User.id==int(value))
        stmt=stmt.join(User,User.id==Order.user_id).where(or_(*terms))
    if status:stmt=stmt.where(Order.status==status)
    if product_type:stmt=stmt.where(Order.product_type==product_type)
    if account_code:stmt=stmt.where(Order.account_code==account_code)
    if billing_mode:stmt=stmt.where(Order.billing_mode==billing_mode)
    if date_from and date_to and date_from>date_to:raise BillingError(422,'date_from must not be after date_to')
    zone=ZoneInfo('America/Los_Angeles')
    if date_from:stmt=stmt.where(Order.created_at>=datetime.combine(date_from,datetime.min.time(),zone))
    if date_to:stmt=stmt.where(Order.created_at<datetime.combine(date_to+timedelta(days=1),datetime.min.time(),zone))
    return stmt

def admin_orders(db,limit=50,offset=0,**filters):
    stmt=filtered_admin_orders(**filters)
    selected=stmt.subquery()
    grouped=list(db.execute(select(selected.c.currency,selected.c.status,func.count().label('orders'),func.sum(selected.c.amount_cents).label('cents')).group_by(selected.c.currency,selected.c.status)))
    currencies={}
    counts={}
    for currency,state,count,cents in grouped:
        bucket=currencies.setdefault(currency,dict(currency=currency,gross_cents=0,paid_cents=0,refunded_cents=0,net_cents=0,pending_cents=0,review_cents=0))
        counts[state]=counts.get(state,0)+count
        if state in {'paid','refund_pending','refunded','refund_review'}:bucket['gross_cents']+=int(cents)
        if state=='refunded':bucket['refunded_cents']+=int(cents)
        if state=='created':bucket['pending_cents']+=int(cents)
        if state=='refund_review':bucket['review_cents']+=int(cents)
    for bucket in currencies.values():
        bucket['paid_cents']=bucket['gross_cents']
        bucket['net_cents']=bucket['gross_cents']-bucket['refunded_cents']
    usd=currencies.get('USD',dict(currency='USD',gross_cents=0,paid_cents=0,refunded_cents=0,net_cents=0,pending_cents=0,review_cents=0))
    summary=dict(**usd,orders=sum(counts.values()),paid_orders=sum(counts.get(s,0) for s in ('paid','refund_pending','refunded','refund_review')),refunded_orders=counts.get('refunded',0),pending_orders=counts.get('created',0),pending_refund_orders=counts.get('refund_pending',0),review_orders=counts.get('refund_review',0),failed_orders=counts.get('failed',0),currencies=list(currencies.values()))
    rows=list(db.scalars(stmt.order_by(Order.created_at.desc(),Order.id.desc()).offset(offset).limit(limit+1)))
    return dict(items=[order_out(db,row,True) for row in rows[:limit]],has_more=len(rows)>limit,total=summary['orders'],summary=summary)

def refund_eligibility(db,order):
    existing=db.scalar(select(CommerceRefund).where(CommerceRefund.order_id==order.id))
    if existing:
        return dict(eligible=False,reason=existing.error or ('退款处理中，可重试现有申请' if existing.status=='pending' else '该订单已有退款记录：'+existing.status))
    if order.status!='paid' or (not order.checkout_key and not order.invoice_id) or not order.product_snapshot or not order.payment_intent_id:return dict(eligible=False,reason='历史订单或支付来源不完整，需要人工核对')
    source=db.scalar(select(CoinSource).where(CoinSource.order_id==order.id))
    grant=db.scalar(select(MembershipGrant).where(MembershipGrant.order_id==order.id))
    if source:
        try:verify_source_proof(db,order,source)
        except BillingError as exc:return dict(eligible=False,reason=exc.detail)
        if source.remaining!=source.original or source.reserved:return dict(eligible=False,reason='本订单金币已消费或冻结，需人工核对')
    if grant:
        if grant.access_consumed or grant.status!='active':return dict(eligible=False,reason='本订单会员权益已消费或变更，需人工核对')
        changed=db.scalar(select(AuditLog.id).where(AuditLog.target_type=='user',AuditLog.target_id==order.user_id,AuditLog.action.in_(('membership_grant','membership_revoke')),AuditLog.created_at>=order.paid_at).limit(1))
        if changed:return dict(eligible=False,reason='购买后存在人工会员调整，需人工核对')
    if source is None and grant is None:return dict(eligible=False,reason='没有可证明未消费的订单权益来源')
    return dict(eligible=True,reason='可申请未消费权益的全额退款；提交时服务端会重新核验并冻结')

def admin_order_detail(db,order_no):
    from app.modules.account.models import User
    order=db.scalar(select(Order).where(Order.order_no==order_no))
    if order is None:raise BillingError(404,'order not found')
    item=order_out(db,order,True)
    item.update(source_drama_id=order.source_drama_id,version_id=order.plan_version_id or order.product_snapshot.get('version_id'),snapshot=order.product_snapshot,payment_intent_id=order.payment_intent_id,invoice_id=order.invoice_id,subscription_ref=order.subscription_ref)
    source=db.scalar(select(CoinSource).where(CoinSource.order_id==order.id))
    grant=db.scalar(select(MembershipGrant).where(MembershipGrant.order_id==order.id))
    refund=db.scalar(select(CommerceRefund).where(CommerceRefund.order_id==order.id))
    return dict(order=item,customer=customer_out(db.get(User,order.user_id)),refund=refund_out(refund) if refund else None,entitlement_sources=dict(coins=dict(original=source.original,remaining=source.remaining,reserved=source.reserved) if source else None,membership=dict(starts_at=grant.starts_at.isoformat(),ends_at=grant.ends_at.isoformat(),status=grant.status,access_consumed=grant.access_consumed) if grant else None),refund_eligibility=refund_eligibility(db,order))
