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
from app.modules.billing.commerce_models import (CoinPack,CoinPackVersion,StripeAccount,StripePriceBinding,CoinSource,CoinAllocation,MembershipGrant,CommerceSettlement,CommerceRefund)
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
        db.add(CoinPackVersion(pack_id=pack.id,version=latest.version+1 if latest else 1,**values))
    audit(db,actor,'coin_pack.save',pack.id,dict(code=code));db.commit()
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

def stripe_request(db,code,path,data=None,method='POST',idempotency_key=None):
    account=get_account(db,code)
    payload=None if data is None else urllib.parse.urlencode(data).encode()
    request=urllib.request.Request('https://api.stripe.com/v1'+path,data=payload,method=method)
    request.add_header('Authorization','Bearer '+account['secret_key'])
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

def save_account(db,code,data,actor):
    row=db.get(StripeAccount,code)
    if row is None:row=StripeAccount(code=code,name=data.get('name',code),status='draft');db.add(row)
    for field in ('name','status','is_default'):
        if field in data and data[field] is not None:setattr(row,field,data[field])
    if row.status not in {'draft','active','disabled'}:raise BillingError(422,'invalid account status')
    save_config(db,'stripe:'+code,{k:v for k,v in data.items() if k in {'secret_key','webhook_secret','clear_secret_key','clear_webhook_secret'}},secret_fields=('secret_key','webhook_secret'))
    if row.is_default:
        for other in db.scalars(select(StripeAccount).where(StripeAccount.code!=code)):other.is_default=False
    if row.status=='active':
        credentials=account_config(db,code)
        if not credentials.get('webhook_secret') or not credentials.get('secret_key'):raise BillingError(422,'configure Stripe key and webhook signing secret before activating')
    audit(db,actor,'stripe_account.save',payload=dict(code=code,status=row.status));db.commit()
    return next(x for x in account_list(db) if x['code']==code)

def test_account(db,code):
    result=stripe_request(db,code,'/account',method='GET')
    row=db.get(StripeAccount,code)
    if row:row.remote_id=result.get('id');row.livemode=get_account(db,code)['secret_key'].startswith('sk_live_');db.commit()
    return dict(ok=True,account_id=result.get('id'),country=result.get('country'),livemode=get_account(db,code)['secret_key'].startswith('sk_live_'))

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

def checkout(db,*,user_id,email,product_type,code,idempotency_key,account_code=None,success_url=None,cancel_url=None,source_drama_id=None,attribution_pid=None):
    success=return_url(success_url,'success');cancel=return_url(cancel_url,'cancel')
    key='checkout:'+str(user_id)+':'+hashlib.sha256(idempotency_key.encode()).hexdigest()
    wallet_lock(db,user_id)
    order=db.scalar(select(Order).where(Order.checkout_key==key))
    if order:
        if order.product_type!=product_type or order.product_snapshot.get('code')!=code:raise BillingError(409,'idempotency key was used for another product')
        if order.checkout_url:return dict(url=order.checkout_url,session_id=order.external_txn_id,order_no=order.order_no)
        account=get_account(db,order.account_code);snapshot=order.product_snapshot
    else:
        snapshot=product_snapshot(db,product_type,code)
        if snapshot['billing_mode']=='subscription':
            existing_subscription=db.scalar(select(Subscription).where(Subscription.user_id==user_id,Subscription.stripe_subscription_id.is_not(None),Subscription.status=='active',Subscription.canceled_at.is_(None)))
            if existing_subscription is not None:raise BillingError(409,'an active renewing subscription already exists; manage it before starting another')
        account=select_account(db,user_id,account_code)
        if snapshot['price_cents']<50:raise BillingError(422,'Stripe price must be at least USD 0.50')
        if source_drama_id is not None:
            from app.modules.content.models import Drama
            drama=db.get(Drama,source_drama_id)
            if drama is None or drama.tenant_id!='jjtv' or drama.status!='published':raise BillingError(404,'source drama not found')
        order=Order(order_no=secrets.token_hex(12),user_id=user_id,plan_version_id=snapshot['version_id'] if product_type=='membership' else None,product_type=product_type,product_snapshot=snapshot,amount_cents=snapshot['price_cents'],currency=snapshot['currency'],billing_mode=snapshot['billing_mode'],status='created',account_code=account['code'],checkout_key=key,source_drama_id=source_drama_id,attribution_pid=attribution_pid)
        db.add(order);db.commit()
    price=ensure_price(db,account['code'],snapshot)
    data={'mode':'subscription' if snapshot['billing_mode']=='subscription' else 'payment','line_items[0][price]':price,'line_items[0][quantity]':'1','success_url':success,'cancel_url':cancel,'client_reference_id':str(user_id),'metadata[commerce_order_no]':order.order_no,'metadata[user_id]':str(user_id),'metadata[plan_code]':code,'metadata[account_code]':account['code']}
    if email:data['customer_email']=email
    if snapshot['billing_mode']=='subscription':
        data['subscription_data[metadata][commerce_order_no]']=order.order_no
        data['subscription_data[metadata][user_id]']=str(user_id)
        data['subscription_data[metadata][plan_code]']=code
    else:data['payment_intent_data[metadata][commerce_order_no]']=order.order_no
    remote=stripe_request(db,account['code'],'/checkout/sessions',data,idempotency_key=key)
    order.external_txn_id=remote['id'];order.checkout_url=remote['url'];db.commit()
    return dict(url=remote['url'],session_id=remote['id'],order_no=order.order_no)

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
    if order.status in {'paid','refund_pending','refunded'}:return dict(replayed=True,order_no=order.order_no)
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
    secret=account_config(db,code).get('webhook_secret')
    if not secret:raise BillingError(503,'Stripe webhook signing secret is not configured')
    pieces=[x.partition('=') for x in (header or '').split(',')]
    stamps=[int(v) for k,_,v in pieces if k=='t' and v.isdigit()]
    if not stamps or abs(time.time()-max(stamps))>300:raise BillingError(400,'invalid or expired Stripe signature')
    expected=hmac.new(secret.encode(),str(max(stamps)).encode()+b'.'+body,hashlib.sha256).hexdigest()
    if not any(k=='v1' and hmac.compare_digest(v,expected) for k,_,v in pieces):raise BillingError(401,'bad Stripe signature')

def process_event(db,code,payload):
    kind=payload.get('type','');obj=(payload.get('data') or {}).get('object') or {}
    metadata=obj.get('metadata') or {};number=metadata.get('commerce_order_no')
    order=db.scalar(select(Order).where(Order.order_no==number,Order.account_code==code)) if number else None
    if kind.startswith('checkout.session.'):
        order=order or db.scalar(select(Order).where(Order.external_txn_id==obj.get('id'),Order.account_code==code,Order.checkout_key.is_not(None)))
        if order is None:return None
        if kind in {'checkout.session.expired','checkout.session.async_payment_failed'}:
            wallet_lock(db,order.user_id)
            order=db.get(Order,order.id,with_for_update=True,populate_existing=True)
            if order.status=='created':order.status='failed'
            db.commit();return dict(order_no=order.order_no,status=order.status)
        if kind not in {'checkout.session.completed','checkout.session.async_payment_succeeded'}:return dict(ignored=kind)
        if obj.get('payment_status')!='paid':return dict(pending=True)
        if int(obj.get('amount_total',-1))!=order.amount_cents or str(obj.get('currency','')).upper()!=order.currency:raise BillingError(409,'Stripe amount does not match frozen order')
        order.payment_intent_id=obj.get('payment_intent') or order.payment_intent_id
        order.subscription_ref=obj.get('subscription') or order.subscription_ref
        if order.billing_mode=='subscription':
            # Invoice is the sole money-settlement authority for recurring plans.
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
        initial.subscription_ref=subscription
        wallet_lock(db,initial.user_id)
        order=db.scalar(select(Order).where(Order.invoice_id==obj['id']))
        if order is None:
            if initial.status=='created' and initial.invoice_id is None:order=initial
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
        sub=db.scalar(select(Subscription).where(Subscription.stripe_subscription_id==subscription))
        if sub is None:
            old=db.scalar(select(Subscription).where(Subscription.user_id==order.user_id,Subscription.status=='active').with_for_update())
            if old and old.stripe_subscription_id and old.stripe_subscription_id!=subscription:raise BillingError(409,'another subscription already exists')
            if old:
                if not old.managed_commerce:old.pass_end=max(filter(None,[aware(old.pass_end),aware(old.current_period_end)]))
                sub=old
            else:
                version=db.get(PlanVersion,order.plan_version_id);sub=Subscription(user_id=order.user_id,plan_id=version.plan_id,plan_version_id=version.id,status='active',current_period_start=start,current_period_end=end);db.add(sub)
            sub.stripe_subscription_id=subscription;sub.managed_commerce=True;sub.account_code=code
        if end>aware(sub.current_period_end):sub.current_period_start=start;sub.current_period_end=end
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
        if kind=='charge.refunded':
            order=db.get(Order,refund.order_id)
            if int(obj.get('amount_refunded',0))!=order.amount_cents:return dict(manual_review=True)
            state='succeeded'
        else:state=obj.get('status')
        finalize_refund(db,refund,state);db.commit();return dict(refund_status=refund.status)
    if kind.startswith('customer.subscription.'):
        sub=db.scalar(select(Subscription).where(Subscription.stripe_subscription_id==obj.get('id'),Subscription.account_code==code,Subscription.managed_commerce.is_(True)))
        if sub is None:
            # A subscription object is not proof that its invoice was paid.
            if order is not None:return dict(pending=True,order_no=order.order_no)
            return None
        if kind=='customer.subscription.deleted' or obj.get('cancel_at_period_end'):sub.canceled_at=now()
        db.commit();return dict(received=True)
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
        if existing.status in {'succeeded','failed','canceled'}:return refund_out(existing)
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
    refund.refund_id=remote['id'];finalize_refund(db,refund,remote.get('status','pending'));db.commit();return refund_out(refund)

def finalize_refund(db,refund,status):
    order=db.get(Order,refund.order_id);wallet=wallet_lock(db,order.user_id)
    refund=db.get(CommerceRefund,refund.id,with_for_update=True,populate_existing=True)
    if refund.status in {'succeeded','failed','canceled'}:return
    source=db.scalar(select(CoinSource).where(CoinSource.order_id==order.id).with_for_update())
    grant=db.scalar(select(MembershipGrant).where(MembershipGrant.order_id==order.id).with_for_update())
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

def list_orders(db,user_id=None,limit=50,offset=0):
    stmt=select(Order).order_by(Order.created_at.desc(),Order.id.desc())
    if user_id is not None:stmt=stmt.where(Order.user_id==user_id)
    rows=list(db.scalars(stmt.offset(offset).limit(limit+1)))
    out=[]
    for row in rows[:limit]:
        snapshot=row.product_snapshot or {};plan=db.get(PlanVersion,row.plan_version_id) if row.plan_version_id else None
        product=db.get(Plan,plan.plan_id) if plan else None
        out.append(dict(order_no=row.order_no,product_type=row.product_type,product_code=snapshot.get('code') or (product.code if product else ''),product_name=snapshot.get('name') or (product.name_en if product else ''),plan_code=snapshot.get('code') or (product.code if product else ''),plan_name_en=snapshot.get('name') or (product.name_en if product else ''),plan_name_zh=snapshot.get('name_zh',''),amount_cents=row.amount_cents,currency=row.currency,status=row.status,billing_mode=row.billing_mode,account_code=row.account_code,paid_at=row.paid_at.isoformat() if row.paid_at else None,created_at=row.created_at.isoformat(),coins=snapshot.get('base_coins',0)+snapshot.get('bonus_coins',0)))
    return dict(items=out,has_more=len(rows)>limit)
