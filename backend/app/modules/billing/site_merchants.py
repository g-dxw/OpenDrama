"""Explicit merchant selection and signed cross-site webhook routing."""
from contextlib import contextmanager
import hashlib
import json
from sqlalchemy import or_,select,text
from sqlalchemy.exc import IntegrityError
from app.modules.billing.models import Order,Subscription
from app.modules.billing.commerce_models import SiteMerchantBinding,StripeWebhookReceipt,StripeAccount
from app.modules.billing.service import BillingError
from app.modules.tenancy.consumer_scope import current_site,site_context

def binding(db):
    site=current_site(db)
    row=db.get(SiteMerchantBinding,site)
    if row is None and site=='jjtv':
        default=db.scalar(select(StripeAccount).where(StripeAccount.is_default.is_(True)))
        return dict(account_code=default.code if default else 'env',explicit_reuse=False)
    return dict(account_code=row.account_code,explicit_reuse=row.explicit_reuse) if row else dict(account_code=None,explicit_reuse=False)

def binding_info(db):
    from app.modules.billing.commerce import account_list
    accounts=account_list(db)
    safe=[{k:x.get(k) for k in ('code','name','status','remote_id','livemode')} for x in accounts if x.get('secret_key_configured') and x.get('webhook_secret_configured')]
    result=binding(db)
    result.update(configured=any(x['code']==result['account_code'] and x['status']=='active' for x in safe),available_accounts=safe)
    return result


def merchant_alias_codes(db,code):
    merchant=db.get(StripeAccount,code)
    if merchant is None or not merchant.remote_id:return [code]
    return list(db.scalars(select(StripeAccount.code).where(StripeAccount.remote_id==merchant.remote_id,StripeAccount.livemode==merchant.livemode)))

def require_known_bound_merchants(db,code,site):
    """Do not infer that an unverified historical merchant is independent."""
    from app.modules.billing.commerce import account_config
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('site-merchant-bindings'))"))
    previous_codes=set(db.scalars(select(SiteMerchantBinding.account_code).where(SiteMerchantBinding.tenant_id!=site,SiteMerchantBinding.account_code!=code)))
    for previous in sorted(previous_codes):
        row=db.get(StripeAccount,previous)
        if row is not None and row.remote_id:continue
        # Empty compatibility merchants on a fresh installation cannot collect
        # money and must not prevent provisioning the first real site merchant.
        if not account_config(db,previous).get('secret_key'):continue
        raise BillingError(409,dict(code='merchant_identity_verification_required',
            message='Verify the existing merchant identity before assigning another merchant to an independent site.',
            action='verify_merchant',account_code=previous))

def bind_merchant(db,code,allow_reuse,actor):
    from app.modules.billing.commerce import get_account,audit
    site=current_site(db)
    get_account(db,code,True)
    # Serialize all binding mutations. A second site must explicitly acknowledge
    # shared collection even when two admins save simultaneously.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('site-merchant-bindings'))"))
    require_known_bound_merchants(db,code,site)
    aliases=merchant_alias_codes(db,code)
    other=db.scalar(select(SiteMerchantBinding).where(SiteMerchantBinding.account_code.in_(aliases),SiteMerchantBinding.tenant_id!=site))
    legacy_owner=code=='env' and site!='jjtv'
    if (other is not None or legacy_owner) and not allow_reuse:raise BillingError(409,'This merchant is already used by another site; explicitly allow account reuse')
    row=db.get(SiteMerchantBinding,site)
    if row is None:row=SiteMerchantBinding(tenant_id=site,account_code=code);db.add(row)
    row.account_code=code;row.explicit_reuse=bool(allow_reuse);row.updated_by=actor
    audit(db,actor,'site.merchant.bind',payload=dict(tenant_id=site,account_code=code,explicit_reuse=row.explicit_reuse))
    db.commit()
    return binding_info(db)

def purchase_account(db,requested=None):
    from app.modules.billing.commerce import get_account
    item=binding(db);code=item['account_code']
    if not code:raise BillingError(503,'This site has no configured payment merchant')
    if requested and requested!=code:raise BillingError(403,'Payment merchant is not enabled for this site')
    return get_account(db,code,True)

def channels(db):
    from app.modules.billing.creem_commerce import channels as creem_channels
    from app.modules.billing.epay_commerce import channels as epay_channels

    info=binding_info(db)
    stripe = [dict(code=x['code'],name=x['name'],provider='stripe',enabled=True) for x in info['available_accounts'] if x['code']==info['account_code'] and x['status']=='active']
    return stripe + epay_channels(db) + creem_channels(db)

def event_order(db,code,payload):
    obj=(payload.get('data') or {}).get('object') or {}
    meta=obj.get('metadata') or {}
    parent=((obj.get('parent') or {}).get('subscription_details') or {})
    # First invoices can precede checkout completion. Use the same legacy and
    # parent metadata shapes as settlement, then verify the stored merchant.
    details_meta=parent.get('metadata') or (obj.get('subscription_details') or {}).get('metadata') or {}
    number=meta.get('commerce_order_no') or details_meta.get('commerce_order_no')
    conditions=[]
    if number:
        identified=db.scalar(select(Order).where(Order.order_no==number,Order.provider=='stripe').execution_options(include_all_tenants=True))
        if identified is not None and identified.account_code!=code:raise BillingError(401,'Payment merchant does not match the stored order')
        conditions.append(Order.order_no==number)
    identifier=obj.get('id')
    if identifier:
        conditions.extend([Order.external_txn_id==identifier,Order.invoice_id==identifier,Order.payment_intent_id==identifier])
        if payload.get('type','').startswith('customer.subscription.'):conditions.append(Order.subscription_ref==identifier)
    subscription=obj.get('subscription') or parent.get('subscription')
    if isinstance(subscription,dict):subscription=subscription.get('id')
    if subscription:conditions.append(Order.subscription_ref==subscription)
    payment=obj.get('payment_intent')
    if isinstance(payment,dict):payment=payment.get('id')
    if payment:conditions.append(Order.payment_intent_id==payment)
    if not conditions:return None
    # Host and payload tenant IDs are never used to choose the receiving site.
    rows=list(db.scalars(select(Order).where(Order.account_code==code,Order.provider=='stripe',or_(*conditions)).order_by(Order.id).execution_options(include_all_tenants=True)))
    sites={x.tenant_id for x in rows}
    if len(sites)>1:raise BillingError(409,'Payment object has conflicting site ownership')
    return rows[0] if rows else None

def receive(db,code,body,signature,payload,legacy_handler=None):
    from app.modules.billing import commerce
    commerce.signature(db,code,body,signature)
    if not isinstance(payload,dict) or not isinstance((payload.get('data') or {}).get('object') or {},dict):raise BillingError(400,'Invalid Stripe event payload')
    account=db.get(StripeAccount,code)
    if account is not None and account.remote_id:
        if payload.get('account') and payload['account']!=account.remote_id:raise BillingError(401,'Stripe merchant mismatch')
        # Only a verified merchant identity makes this a frozen provider fact.
        expected_livemode=bool(account.livemode)
    else:
        # The original migration seeded env with livemode=false and no remote
        # identity, even for a live-key installation. Preserve those historical
        # callbacks using the trusted credential's mode, never the event's mode.
        secret=commerce.account_config(db,code).get('secret_key','')
        if secret.startswith(('sk_live_','rk_live_')):expected_livemode=True
        elif secret.startswith(('sk_test_','rk_test_')):expected_livemode=False
        else:expected_livemode=None
    if 'livemode' in payload:
        if type(payload['livemode']) is not bool:raise BillingError(400,'Invalid Stripe merchant mode')
        if expected_livemode is None:raise BillingError(503,'Stripe merchant mode cannot be verified from the configured credential')
        if payload['livemode']!=expected_livemode:raise BillingError(401,'Stripe merchant mode mismatch')
    order=event_order(db,code,payload)
    site=order.tenant_id if order else 'jjtv' if code=='env' else None
    if site is None:return dict(received=True,ignored=payload.get('type'))
    event_id=payload.get('id')
    if event_id is not None and (not isinstance(event_id,str) or len(event_id)>100):raise BillingError(400,'Invalid provider event ID')
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    receipt=db.get(StripeWebhookReceipt,(code,event_id)) if event_id else None
    if receipt:
        if receipt.payload_hash!=digest or receipt.tenant_id!=site:raise BillingError(409,'Provider event identity was reused with a different payload')
        if receipt.processed:return dict(received=True,replayed=True,type=payload.get('type'))
    if event_id and receipt is None:
        try:
            with db.begin_nested():
                receipt=StripeWebhookReceipt(account_code=code,event_id=event_id,tenant_id=site,payload_hash=digest,processed=False)
                db.add(receipt);db.flush()
        except IntegrityError:
            receipt=db.get(StripeWebhookReceipt,(code,event_id),populate_existing=True)
            if receipt is None:raise
            if receipt.payload_hash!=digest or receipt.tenant_id!=site:raise BillingError(409,'Provider event identity mismatch')
            if receipt.processed:return dict(received=True,replayed=True,type=payload.get('type'))
    with site_context(db,site):
        result=commerce.process_event(db,code,payload)
        if result is None and legacy_handler is not None and site=='jjtv':result=legacy_handler()
        if receipt:receipt.processed=True
        db.commit()
    output=dict(received=True,type=payload.get('type'));output.update(result or {'ignored':payload.get('type')});return output


def site_currency(db):
    from app.modules.sites.models import SiteSettings
    value=db.scalar(select(SiteSettings.currency).where(SiteSettings.tenant_id==current_site(db)))
    return (value or 'USD').upper()

def assert_site_currency(db,currency):
    expected=site_currency(db)
    if not isinstance(currency,str) or currency.upper()!=expected:
        raise BillingError(409,'Product currency must match this site currency ('+expected+'); create an explicit new price version')
    return expected
