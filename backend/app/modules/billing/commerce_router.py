"""JJTV-only admin commerce and authenticated consumer checkout routes."""
from __future__ import annotations
import csv
import io
from typing import Annotated, Literal
from urllib.parse import parse_qsl
from datetime import date
from fastapi import APIRouter,Depends,HTTPException,Header,Request,Query,Response
from pydantic import BaseModel,Field,SecretStr,StringConstraints
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.db import get_db
from app.modules.ops.router import require_admin,require_platform_admin,require_tenant_admin
from app.modules.tenancy.permissions import require_permission
from app.modules.common.auth import require_user
from app.modules.billing import commerce as service
from app.modules.billing.models import Order,Plan,PlanVersion
from app.modules.billing.commerce_models import CoinPack,CommerceSettlement,StripePriceBinding
from app.modules.billing.service import BillingError, PLAN_DAYS, INTERVAL_OF, billing_mode, apply_stripe_event
from app.modules.wallet.models import CoinLedgerEntry,WalletAccount
from app.modules.ops.managed_settings import get_config,save_config
from app.modules.tenancy.consumer_scope import site_timezone

admin_router=APIRouter(prefix='/admin/v1',tags=['commerce-admin'])
public_router=APIRouter(prefix='/v1',tags=['commerce'])

def call(db,fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except BillingError as exc:
        db.rollback();raise HTTPException(exc.status_code,exc.detail) from exc

class PackIn(BaseModel):
    code:str=Field(pattern='^[a-z0-9_]{2,32}$')
    name_en:str=Field(min_length=1,max_length=64)
    name_zh:str=Field(default='',max_length=64)
    price_cents:int=Field(ge=50)
    currency:str|None=Field(default=None,pattern='^[A-Z]{3}$')
    base_coins:int=Field(ge=1)
    bonus_coins:int=Field(default=0,ge=0)
    sort_order:int=Field(default=0,ge=0)
    badge:str=Field(default='',max_length=64)
    note:str=Field(default='',max_length=500)
class PackPatch(BaseModel):
    currency:str|None=Field(default=None,pattern='^[A-Z]{3}$')
    name_en:str|None=Field(default=None,min_length=1,max_length=64)
    name_zh:str|None=Field(default=None,max_length=64)
    price_cents:int|None=Field(default=None,ge=50)
    base_coins:int|None=Field(default=None,ge=1)
    bonus_coins:int|None=Field(default=None,ge=0)
    sort_order:int|None=Field(default=None,ge=0)
    badge:str|None=Field(default=None,max_length=64)
    note:str=Field(default='',max_length=500)
class StatusIn(BaseModel):status:str=Field(pattern='^(active|retired)$')
class AccountIn(BaseModel):
    code:str=Field(pattern='^[a-z0-9_]{2,32}$')
    name:str=Field(min_length=1,max_length=100)
    status:str=Field(default='draft',pattern='^(active|draft|disabled)$')
    is_default:bool=False
    secret_key:str|None=None
    webhook_secret:str|None=None
class AccountPatch(BaseModel):
    name:str|None=Field(default=None,min_length=1,max_length=100)
    status:str|None=Field(default=None,pattern='^(active|draft|disabled)$')
    is_default:bool|None=None
    secret_key:str|None=None
    webhook_secret:str|None=None
    clear_secret_key:bool=False
    clear_webhook_secret:bool=False
class PlanPriceIn(BaseModel):
    currency:str|None=Field(default=None,pattern='^[A-Z]{3}$')
    price_cents:int=Field(ge=50)
    benefits:dict=Field(default_factory=dict)
    base_coins:int=Field(default=0,ge=0)
    bonus_coins:int=Field(default=0,ge=0)
    note:str=Field(min_length=1,max_length=500)
class SettingsIn(BaseModel):
    base_coins_per_usd:int=Field(ge=1,le=1000000)
class CheckoutIn(BaseModel):
    product_type:str=Field(pattern='^(membership|coin_pack)$')
    code:str=Field(min_length=1,max_length=32)
    account_code:str|None=None
    provider:Literal['stripe','epay','creem','waffo_pancake']='stripe'
    payment_method:str|None=Field(default=None,pattern='^[a-z0-9_]{1,64}$')
    idempotency_key:str=Field(min_length=8,max_length=100)
    success_url:str|None=Field(default=None,max_length=2000)
    cancel_url:str|None=Field(default=None,max_length=2000)
    source_drama_id:int|None=None
    attribution_pid:str|None=Field(default=None,max_length=100)
class RefundIn(BaseModel):
    reason:str=Field(min_length=1,max_length=500)
    idempotency_key:str=Field(min_length=8,max_length=100)

class MembershipRefundResolutionIn(RefundIn):
    expected_revision:str=Field(pattern='^[0-9a-f]{64}$')
class MembershipRefundResolutionOut(BaseModel):
    order_no:str
    resolved:bool
    restored:bool
    released_coins:int=Field(ge=0)
    starts_at:str|None
    ends_at:str|None
    replayed:bool

@public_router.get('/coin-packs')
def coin_packs(db:Session=Depends(get_db)):
    return {'items':service.list_packs(db) if service.sale_allowed(db,'coin_pack') else []}
@public_router.get('/billing/channels')
def channels(db:Session=Depends(get_db)):
    from app.modules.billing.site_merchants import channels as site_channels
    return {'items':site_channels(db)}
@public_router.post('/commerce/checkout')
def checkout(body:CheckoutIn,user=Depends(require_user),db:Session=Depends(get_db)):
    return call(db,service.checkout,db,user_id=user.id,email=user.email,**body.model_dump())
@public_router.post('/commerce/orders/{order_no}/cancel')
def cancel_checkout(order_no:str,user=Depends(require_user),db:Session=Depends(get_db)):
    return call(db,service.cancel_checkout,db,user.id,order_no)
@public_router.get('/commerce/orders')
def orders(limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0),user=Depends(require_user),db:Session=Depends(get_db)):
    return service.list_orders(db,user.id,limit,offset)
@public_router.get('/commerce/orders/{order_no}')
def consumer_order_detail(order_no:str,response:Response,user=Depends(require_user),db:Session=Depends(get_db)):
    return _consumer_order_detail(order_no,response,user,db)


def _consumer_order_detail(order_no,response,user,db,*,reconcile=False):
    row=db.scalar(select(Order).where(Order.order_no==order_no,Order.user_id==user.id))
    if row is None:raise HTTPException(404,'order not found',headers={'Cache-Control':'private, no-store','Vary':'Authorization'})
    if reconcile and row.provider=='epay':
        from app.modules.billing import epay_commerce
        call(db,epay_commerce.reconcile,db,row)
        db.refresh(row)
    elif reconcile and row.provider=='creem':
        from app.modules.billing import creem_commerce
        call(db,creem_commerce.reconcile,db,row)
        db.refresh(row)
    elif reconcile and row.provider=='waffo_pancake':
        from app.modules.billing import pancake_commerce
        call(db,pancake_commerce.reconcile,db,row)
        db.refresh(row)
    snapshot=row.product_snapshot if isinstance(row.product_snapshot,dict) else {}
    code=snapshot.get('code')
    if not isinstance(code,str):code=''
    if not code and row.plan_version_id:
        code=db.scalar(select(Plan.code).join(PlanVersion,PlanVersion.plan_id==Plan.id).where(PlanVersion.id==row.plan_version_id)) or ''
    # Imported/local paid status alone never proves fulfillment. Keep this
    # source fact separate from status, including pending and refunded orders.
    settled=db.scalar(select(CommerceSettlement.order_id).where(CommerceSettlement.order_id==row.id)) is not None
    response.headers['Cache-Control']='private, no-store'
    response.headers['Vary']='Authorization'
    return {'order':dict(order_no=row.order_no,product_type=row.product_type,product_code=code,status=row.status),'settled':settled}


@public_router.post('/commerce/orders/{order_no}/reconcile')
def reconcile_order(order_no:str,response:Response,user=Depends(require_user),db:Session=Depends(get_db)):
    return _consumer_order_detail(order_no,response,user,db,reconcile=True)


@public_router.api_route('/billing/epay/accounts/{code}/webhook', methods=['GET','POST'], include_in_schema=False)
async def epay_webhook(code:str,request:Request,db:Session=Depends(get_db)):
    from fastapi.concurrency import run_in_threadpool
    from app.modules.billing import epay_commerce

    raw = request.scope['query_string']
    if request.method == 'POST':
        if raw or request.headers.get('content-type','').split(';',1)[0].strip().lower() != 'application/x-www-form-urlencoded':
            raise HTTPException(400, 'Expected an Epay form notification')
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > 8192:
                raise HTTPException(413, 'Epay notification is too large')
            chunks.extend(chunk)
        raw = bytes(chunks)
    if len(raw) > 8192:
        raise HTTPException(413, 'Epay notification is too large')
    try:
        pairs = parse_qsl(raw.decode('utf-8'), keep_blank_values=True, strict_parsing=True, errors='strict', max_num_fields=32)
    except ValueError:
        raise HTTPException(400, 'Malformed Epay notification') from None
    params = dict(pairs)
    if len(params) != len(pairs):
        raise HTTPException(400, 'Duplicate Epay notification parameters')
    await run_in_threadpool(call,db,epay_commerce.receive,db,code,params)
    return Response('success', media_type='text/plain', headers={'Cache-Control':'no-store'})
@public_router.post('/billing/stripe/accounts/{code}/webhook')
async def webhook(code:str,request:Request,stripe_signature:str=Header(default='',alias='Stripe-Signature'),db:Session=Depends(get_db)):
    body=await request.body()
    try:payload=__import__('json').loads(body)
    except (ValueError,TypeError):raise HTTPException(400,'invalid JSON')
    if code=='env':return call(db,apply_stripe_event,db,body=body,signature=stripe_signature,payload=payload)
    from app.modules.billing.site_merchants import receive
    return call(db,receive,db,code,body,stripe_signature,payload)


@public_router.post('/billing/creem/accounts/{code}/webhook', include_in_schema=False)
async def creem_webhook(code: str, request: Request, signature: str = Header(default='', alias='creem-signature'),
                        db: Session = Depends(get_db)):
    from fastapi.concurrency import run_in_threadpool
    from app.modules.billing import creem_commerce

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 1024 * 1024:
            raise HTTPException(413, 'Creem notification is too large')
        body.extend(chunk)
    return await run_in_threadpool(call, db, creem_commerce.receive, db, code, bytes(body), signature)


@public_router.post('/billing/waffo-pancake/accounts/{code}/webhook', include_in_schema=False)
async def pancake_webhook(code: str, request: Request, signature: str = Header(default='', alias='X-Waffo-Signature'),
                          db: Session = Depends(get_db)):
    from fastapi.concurrency import run_in_threadpool
    from app.modules.billing import pancake_commerce

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 1024 * 1024:
            raise HTTPException(413, 'Waffo Pancake notification is too large')
        body.extend(chunk)
    return await run_in_threadpool(call, db, pancake_commerce.receive, db, code, bytes(body), signature)

@admin_router.get('/coin-packs')
def admin_packs(user=Depends(require_admin),db:Session=Depends(get_db)):return {'items':service.list_packs(db,True)}
@admin_router.post('/coin-packs')
def create_pack(body:PackIn,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'coin_packs.manage')
    if db.scalar(select(CoinPack).where(CoinPack.code==body.code)):raise HTTPException(409,'pack code already exists')
    return call(db,service.save_pack,db,body.code,body.model_dump(exclude={'code'},exclude_none=True),user.id)
@admin_router.patch('/coin-packs/{code}')
def patch_pack(code:str,body:PackPatch,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'coin_packs.manage')
    if not db.scalar(select(CoinPack).where(CoinPack.code==code)):raise HTTPException(404,'pack not found')
    return call(db,service.save_pack,db,code,body.model_dump(exclude_unset=True),user.id)
@admin_router.post('/coin-packs/{code}/status')
def pack_status(code:str,body:StatusIn,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'coin_packs.manage');row=db.scalar(select(CoinPack).where(CoinPack.code==code))
    if not row:raise HTTPException(404,'pack not found')
    row.status=body.status;service.audit(db,user.id,'coin_pack.status',row.id,body.model_dump());db.commit();return {'status':row.status}

@admin_router.get('/commerce/plans')
def all_plans(user=Depends(require_admin),db:Session=Depends(get_db)):
    out=[]
    mode=billing_mode(db)
    for plan in db.scalars(select(Plan).order_by(Plan.id)):
        version=db.scalar(select(PlanVersion).where(PlanVersion.plan_id==plan.id).order_by(PlanVersion.version.desc()).limit(1))
        if version:out.append(dict(code=plan.code,name_en=plan.name_en,name_zh=plan.name_zh,status=plan.status,price_cents=version.price_cents,currency=version.currency,version=version.version,base_coins=version.base_coins,bonus_coins=version.bonus_coins,benefits=version.benefits,duration_days=PLAN_DAYS.get(plan.code,30) if mode=="one_time" else None,recurring_interval=INTERVAL_OF.get(plan.code,"month") if mode=="subscription" else None,billing_mode=mode))
    return {'items':out,'billing_mode':mode}
@admin_router.post('/plans/{code}/status')
def plan_status(code:str,body:StatusIn,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'plans.manage');row=db.scalar(select(Plan).where(Plan.code==code))
    if not row:raise HTTPException(404,'plan not found')
    row.status=body.status;service.audit(db,user.id,'plan.status',row.id,body.model_dump());db.commit();return {'status':row.status}
@admin_router.post('/commerce/plans/{code}/price')
def plan_price(code:str,body:PlanPriceIn,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'plans.manage');row=db.scalar(select(Plan).where(Plan.code==code).with_for_update())
    if not row:raise HTTPException(404,'plan not found')
    latest=db.scalar(select(PlanVersion).where(PlanVersion.plan_id==row.id).order_by(PlanVersion.version.desc()).limit(1))
    from app.modules.billing.site_merchants import site_currency,assert_site_currency
    values=body.model_dump(exclude_none=True)
    values['currency']=values.get('currency') or (latest.currency if latest else site_currency(db))
    call(db,assert_site_currency,db,values['currency'])
    version=PlanVersion(plan_id=row.id,version=latest.version+1 if latest else 1,**values);db.add(version)
    service.audit(db,user.id,'plan.version',row.id,dict(code=code));db.commit();return {'version':version.version,'price_cents':version.price_cents}

@admin_router.get('/stripe/accounts')
def stripe_accounts(user=Depends(require_admin),db:Session=Depends(get_db)):
    require_legacy_merchant_owner(db)
    return {'items':service.account_list(db)}
@admin_router.post('/stripe/accounts')
def create_account(body:AccountIn,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_legacy_merchant_owner(db)
    require_permission(request,'stripe.manage')
    return call(db,service.save_account,db,body.code,body.model_dump(exclude={'code'}),user.id)
@admin_router.patch('/stripe/accounts/{code}')
def patch_account(code:str,body:AccountPatch,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_legacy_merchant_owner(db)
    require_permission(request,'stripe.manage')
    return call(db,service.save_account,db,code,body.model_dump(exclude_unset=True),user.id)
@admin_router.post('/stripe/accounts/{code}/test')
def account_test(code:str,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_legacy_merchant_owner(db)
    require_permission(request,'stripe.manage');return call(db,service.test_account,db,code)
@admin_router.get('/stripe/prices')
def prices(user=Depends(require_admin),db:Session=Depends(get_db)):
    return {'items':[dict(account_code=x.account_code,product_type=x.product_type,version_id=x.version_id,billing_mode=x.billing_mode,price_id=x.price_id) for x in db.scalars(select(StripePriceBinding))]}
@admin_router.post('/stripe/prices/provision')
def provision(request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_legacy_merchant_owner(db)
    require_permission(request,'stripe.manage');out=[]
    from app.modules.billing.service import list_current_plans
    for account in service.account_list(db):
        if account['status']!='active':continue
        for kind,items in [('membership',list_current_plans(db)),('coin_pack',service.list_packs(db))]:
            for item in items:
                snapshot=service.product_snapshot(db,kind,item['code'])
                for mode in (['subscription','one_time'] if kind=='membership' else ['one_time']):
                    snapshot['billing_mode']=mode
                    try:out.append(dict(account=account['code'],code=item['code'],mode=mode,ok=True,price_id=service.ensure_price(db,account['code'],snapshot)))
                    except BillingError as exc:db.rollback();out.append(dict(account=account['code'],code=item['code'],mode=mode,ok=False,error=exc.detail))
    return {'items':out}
@admin_router.get('/billing/channels')
def admin_channels(user=Depends(require_admin),db:Session=Depends(get_db)):return {'items':service.account_list(db)}
@admin_router.get('/coins/ladder')
def ladder(user=Depends(require_admin),db:Session=Depends(get_db)):
    config=get_config(db,'coin-settings',{'base_coins_per_usd':100});packs=service.list_packs(db,True)
    return {**config,'items':[dict(code=p['code'],coins_per_usd=round(p['base_coins']*100/p['price_cents'],2),bonus_ratio_pct=round(p['bonus_coins']*100/p['base_coins'],2)) for p in packs]}
@admin_router.post('/coins/settings')
def settings(body:SettingsIn,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'coin_packs.manage');save_config(db,'coin-settings',body.model_dump());service.audit(db,user.id,'coins.settings');db.commit();return body.model_dump()

def ledger_query(user_id,biz_type):
    query=select(CoinLedgerEntry)
    if user_id is not None:query=query.where(CoinLedgerEntry.user_id==user_id)
    if biz_type:query=query.where(CoinLedgerEntry.biz_type==biz_type)
    return query.order_by(CoinLedgerEntry.created_at.desc(),CoinLedgerEntry.id.desc())
@admin_router.get('/coins/records')
def records(user_id:int|None=None,biz_type:str|None=None,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),user=Depends(require_admin),db:Session=Depends(get_db)):
    rows=list(db.scalars(ledger_query(user_id,biz_type).offset(offset).limit(limit+1)))
    return {'items':[dict(id=r.id,user_id=r.user_id,amount=r.amount,balance_after=r.balance_after,biz_type=r.biz_type,note=r.note,created_at=r.created_at.isoformat()) for r in rows[:limit]],'has_more':len(rows)>limit}
@admin_router.get('/coins/summary')
def summary(user=Depends(require_admin),db:Session=Depends(get_db)):
    rows=list(db.execute(select(CoinLedgerEntry.biz_type,func.sum(CoinLedgerEntry.amount),func.count()).group_by(CoinLedgerEntry.biz_type)))
    return {'balance':db.scalar(select(func.coalesce(func.sum(WalletAccount.balance),0))) or 0,'items':[dict(biz_type=r[0],amount=int(r[1]),count=r[2]) for r in rows]}
@admin_router.get('/coins/stats')
def stats(user=Depends(require_admin),db:Session=Depends(get_db)):
    day=func.date_trunc('day',func.timezone(site_timezone(db).key,CoinLedgerEntry.created_at))
    rows=list(db.execute(select(day,func.sum(CoinLedgerEntry.amount),func.count()).group_by(day).order_by(day.desc()).limit(30)))
    return {'items':[dict(day=r[0].date().isoformat(),amount=int(r[1]),count=r[2]) for r in rows]}
@admin_router.get('/coins/export')
def export(request:Request,user_id:int|None=None,biz_type:str|None=None,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'coins.export');stream=io.StringIO();writer=csv.writer(stream);writer.writerow(['id','user_id','amount','balance_after','biz_type','created_at'])
    for r in db.scalars(ledger_query(user_id,biz_type).limit(10000)):writer.writerow([r.id,r.user_id,r.amount,r.balance_after,r.biz_type,r.created_at.isoformat()])
    service.audit(db,user.id,'coins.export');db.commit();return {'filename':'coin-ledger.csv','content':stream.getvalue()}
@admin_router.get('/commerce/orders')
def admin_orders(limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),q:str=Query('',max_length=200),status:str|None=Query(None,pattern='^(created|paid|failed|refund_pending|refunded|refund_review)$'),product_type:str|None=Query(None,pattern='^(membership|coin_pack)$'),account_code:str|None=Query(None,max_length=32),billing_mode:str|None=Query(None,pattern='^(one_time|subscription)$'),date_from:date|None=None,date_to:date|None=None,user=Depends(require_admin),db:Session=Depends(get_db)):
    return call(db,service.admin_orders,db,limit,offset,q=q,status=status,product_type=product_type,account_code=account_code,billing_mode=billing_mode,date_from=date_from,date_to=date_to)
@admin_router.get('/commerce/orders/{order_no}')
def order_detail(order_no:str,user=Depends(require_admin),db:Session=Depends(get_db)):
    return call(db,service.admin_order_detail,db,order_no)
@admin_router.post('/finance/orders/{order_no}/refund')
def refund(order_no:str,body:RefundIn,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'finance.refund');return call(db,service.request_refund,db,order_no,user.id,body.reason,body.idempotency_key)

@admin_router.post('/finance/orders/{order_no}/membership-refund-resolution',response_model=MembershipRefundResolutionOut)
def membership_refund_resolution(order_no:str,body:MembershipRefundResolutionIn,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'finance.refund')
    return call(db,service.resolve_membership_refund,db,order_no,user.id,body.reason,body.idempotency_key,body.expected_revision)


platform_router=APIRouter(prefix='/platform/v1',tags=['instance-merchants'])
tenant_router=APIRouter(prefix='/tenant/v1',tags=['site-merchant-binding'])

class MerchantBindingIn(BaseModel):
    account_code:str=Field(pattern='^[a-z0-9_]{2,32}$')
    allow_reuse:bool=False

def require_legacy_merchant_owner(db):
    from app.modules.tenancy.consumer_scope import current_site
    if current_site(db)!='jjtv':raise HTTPException(403,'Merchant credentials are managed by the instance administrator')

@tenant_router.get('/merchant-binding')
def read_site_merchant(db:Session=Depends(get_db),user=Depends(require_tenant_admin)):
    from app.modules.billing.site_merchants import binding_info
    return call(db,binding_info,db)

@tenant_router.put('/merchant-binding')
def set_site_merchant(body:MerchantBindingIn,db:Session=Depends(get_db),user=Depends(require_tenant_admin)):
    from app.modules.billing.site_merchants import bind_merchant
    return call(db,bind_merchant,db,body.account_code,body.allow_reuse,user.id)

@platform_router.get('/stripe/accounts')
def instance_accounts(db:Session=Depends(get_db),user=Depends(require_platform_admin)):
    return {'items':service.account_list(db)}

@platform_router.post('/stripe/accounts')
def instance_create_account(body:AccountIn,db:Session=Depends(get_db),user=Depends(require_platform_admin)):
    return call(db,service.save_account,db,body.code,body.model_dump(exclude_unset=True),user.id)

@platform_router.patch('/stripe/accounts/{code}')
def instance_update_account(code:str,body:AccountPatch,db:Session=Depends(get_db),user=Depends(require_platform_admin)):
    return call(db,service.save_account,db,code,body.model_dump(exclude_unset=True),user.id)

@platform_router.post('/stripe/accounts/{code}/test')
def instance_test_account(code:str,db:Session=Depends(get_db),user=Depends(require_platform_admin)):
    return call(db,service.test_account,db,code)


EpayMethod = Annotated[str, StringConstraints(pattern=r'^[a-z0-9_]{1,64}$')]
EpayMerchant = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')]
GatewayCode = Annotated[str, StringConstraints(pattern=r'^[a-z0-9_]{2,32}$')]
GatewayStatus = Literal['draft', 'active', 'disabled']


class EpayAccountIn(BaseModel):
    code: GatewayCode
    name: str = Field(min_length=1, max_length=100)
    status: GatewayStatus = 'draft'
    endpoint: str = Field(min_length=1, max_length=500)
    merchant_id: EpayMerchant
    payment_methods: list[EpayMethod] = Field(max_length=20)
    secret_key: SecretStr | None = None


class EpayAccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: GatewayStatus | None = None
    endpoint: str | None = Field(default=None, min_length=1, max_length=500)
    merchant_id: EpayMerchant | None = None
    payment_methods: list[EpayMethod] | None = Field(default=None, max_length=20)
    secret_key: SecretStr | None = None


def _gateway_settings(body, fields, *, max_chars=512):
    values = body.model_dump(exclude={'code', *fields}, exclude_none=True)
    for field in fields:
        secret = getattr(body, field)
        if secret is not None:
            value = secret.get_secret_value().strip()
            if len(value) > max_chars:
                raise HTTPException(422, 'Payment credential is too long')
            values[field] = value
    return values


@platform_router.get('/epay/accounts')
def epay_accounts(db: Session = Depends(get_db), user=Depends(require_platform_admin)):
    from app.modules.billing import epay_commerce, gateway_accounts

    return {'items': [epay_commerce.account_public(row) for row in gateway_accounts.accounts(db, 'epay')]}


@platform_router.post('/epay/accounts')
def epay_create_account(body: EpayAccountIn, db: Session = Depends(get_db), user=Depends(require_platform_admin)):
    from app.modules.billing import epay_commerce

    return call(db, epay_commerce.save_account, db, body.code, _gateway_settings(body, ('secret_key',)), user.id, create=True)


@platform_router.patch('/epay/accounts/{code}')
def epay_update_account(code: GatewayCode, body: EpayAccountPatch, db: Session = Depends(get_db), user=Depends(require_platform_admin)):
    from app.modules.billing import epay_commerce

    return call(db, epay_commerce.save_account, db, code, _gateway_settings(body, ('secret_key',)), user.id)


@platform_router.post('/epay/accounts/{code}/test')
def epay_test_account(code: GatewayCode, db: Session = Depends(get_db), user=Depends(require_platform_admin)):
    from app.modules.billing import epay_commerce

    return call(db, epay_commerce.test_account, db, code)


class GatewayBindingIn(BaseModel):
    account_code: GatewayCode | None


@tenant_router.get('/epay/merchant-binding')
def epay_read_binding(db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    from app.modules.billing import epay_commerce

    return call(db, epay_commerce.binding_info, db)


@tenant_router.put('/epay/merchant-binding')
def epay_set_binding(body: GatewayBindingIn, db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    from app.modules.billing import epay_commerce

    return call(db, epay_commerce.bind, db, body.account_code, user.id)


GatewayMode = Literal['test', 'production']
CREEM_SECRETS = ('api_key', 'webhook_secret')


class CreemAccountIn(BaseModel):
    code: GatewayCode
    name: str = Field(min_length=1, max_length=100)
    status: GatewayStatus = 'draft'
    mode: GatewayMode
    api_key: SecretStr | None = None
    webhook_secret: SecretStr | None = None


class CreemAccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: GatewayStatus | None = None
    mode: GatewayMode | None = None
    api_key: SecretStr | None = None
    webhook_secret: SecretStr | None = None


class ProductBindingIn(BaseModel):
    account_code: GatewayCode
    product_type: Literal['coin_pack', 'membership']
    version_id: int = Field(ge=1)
    billing_mode: Literal['one_time', 'subscription']


class CreemProductBindingIn(ProductBindingIn):
    external_product_id: Annotated[str, StringConstraints(pattern=r'^prod_[a-zA-Z0-9_-]{1,122}$')] | None


@platform_router.get('/creem/accounts')
def creem_account_list(db: Session = Depends(get_db), user=Depends(require_platform_admin)):
    from app.modules.billing import creem_accounts, gateway_accounts

    return {'items': [creem_accounts.account_public(row) for row in gateway_accounts.accounts(db, 'creem')]}


@platform_router.post('/creem/accounts')
def creem_create_account(body: CreemAccountIn, db: Session = Depends(get_db), user=Depends(require_platform_admin)):
    from app.modules.billing import creem_accounts

    return call(db, creem_accounts.save_account, db, body.code, _gateway_settings(body, CREEM_SECRETS), user.id, create=True)


@platform_router.patch('/creem/accounts/{code}')
def creem_update_account(code: GatewayCode, body: CreemAccountPatch, db: Session = Depends(get_db), user=Depends(require_platform_admin)):
    from app.modules.billing import creem_accounts

    return call(db, creem_accounts.save_account, db, code, _gateway_settings(body, CREEM_SECRETS), user.id)


@platform_router.post('/creem/accounts/{code}/test')
def creem_test_account(code: GatewayCode, db: Session = Depends(get_db), user=Depends(require_platform_admin)):
    from app.modules.billing import creem_accounts

    return call(db, creem_accounts.test_account, db, code)


@tenant_router.get('/creem/merchant-binding')
def creem_read_binding(db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    from app.modules.billing import creem_accounts

    return call(db, creem_accounts.binding_info, db)


@tenant_router.put('/creem/merchant-binding')
def creem_set_binding(body: GatewayBindingIn, db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    from app.modules.billing import creem_accounts

    return call(db, creem_accounts.bind, db, body.account_code, user.id)


@tenant_router.get('/creem/product-bindings')
def creem_product_bindings(db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    from app.modules.billing import creem_accounts

    return call(db, creem_accounts.bindings_info, db)


@tenant_router.put('/creem/product-bindings')
def creem_save_product(body: CreemProductBindingIn, db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    from app.modules.billing import creem_accounts

    return call(db, creem_accounts.save_binding, db, body.model_dump(), user.id)


PancakeMerchant = Annotated[str, StringConstraints(pattern=r'^MER_[a-zA-Z0-9]{1,120}$')]
PancakeStore = Annotated[str, StringConstraints(pattern=r'^STO_[a-zA-Z0-9]{1,120}$')]


class PancakeAccountIn(BaseModel):
    code: GatewayCode
    name: str = Field(min_length=1, max_length=100)
    status: GatewayStatus = 'draft'
    mode: GatewayMode
    merchant_id: PancakeMerchant
    store_id: PancakeStore
    private_key: SecretStr | None = None


class PancakeAccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: GatewayStatus | None = None
    mode: GatewayMode | None = None
    merchant_id: PancakeMerchant | None = None
    store_id: PancakeStore | None = None
    private_key: SecretStr | None = None


class PancakeProductBindingIn(ProductBindingIn):
    external_product_id: Annotated[str, StringConstraints(pattern=r'^PROD_[a-zA-Z0-9]{1,120}$')] | None


@platform_router.get('/waffo-pancake/accounts')
def pancake_account_list(db: Session = Depends(get_db), user=Depends(require_platform_admin)):
    from app.modules.billing import gateway_accounts, pancake_accounts

    return {'items': [pancake_accounts.account_public(row) for row in gateway_accounts.accounts(db, 'waffo_pancake')]}


@platform_router.post('/waffo-pancake/accounts')
def pancake_create_account(body: PancakeAccountIn, db: Session = Depends(get_db), user=Depends(require_platform_admin)):
    from app.modules.billing import pancake_accounts

    return call(db, pancake_accounts.save_account, db, body.code,
                _gateway_settings(body, ('private_key',), max_chars=8192), user.id, create=True)


@platform_router.patch('/waffo-pancake/accounts/{code}')
def pancake_update_account(code: GatewayCode, body: PancakeAccountPatch, db: Session = Depends(get_db),
                            user=Depends(require_platform_admin)):
    from app.modules.billing import pancake_accounts

    return call(db, pancake_accounts.save_account, db, code,
                _gateway_settings(body, ('private_key',), max_chars=8192), user.id)


@platform_router.post('/waffo-pancake/accounts/{code}/test')
def pancake_test_account(code: GatewayCode, db: Session = Depends(get_db), user=Depends(require_platform_admin)):
    from app.modules.billing import pancake_accounts

    return call(db, pancake_accounts.test_account, db, code)


@platform_router.get('/waffo-pancake/accounts/{code}/catalog')
def pancake_catalog(code: GatewayCode, db: Session = Depends(get_db), user=Depends(require_platform_admin)):
    from app.modules.billing import pancake_accounts

    return call(db, pancake_accounts.merchant_catalog, db, code)


@tenant_router.get('/waffo-pancake/merchant-binding')
def pancake_read_binding(db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    from app.modules.billing import pancake_accounts

    return call(db, pancake_accounts.binding_info, db)


@tenant_router.put('/waffo-pancake/merchant-binding')
def pancake_set_binding(body: GatewayBindingIn, db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    from app.modules.billing import pancake_accounts

    return call(db, pancake_accounts.bind, db, body.account_code, user.id)


@tenant_router.get('/waffo-pancake/product-bindings')
def pancake_product_bindings(db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    from app.modules.billing import pancake_accounts

    return call(db, pancake_accounts.bindings_info, db)


@tenant_router.put('/waffo-pancake/product-bindings')
def pancake_save_product(body: PancakeProductBindingIn, db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    from app.modules.billing import pancake_accounts

    return call(db, pancake_accounts.save_binding, db, body.model_dump(), user.id)
