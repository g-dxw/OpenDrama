"""JJTV-only admin commerce and authenticated consumer checkout routes."""
from __future__ import annotations
import csv
import io
from datetime import date
from fastapi import APIRouter,Depends,HTTPException,Header,Request,Query
from pydantic import BaseModel,Field
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.db import get_db
from app.modules.ops.router import require_admin
from app.modules.tenancy.permissions import require_permission
from app.modules.common.auth import require_user
from app.modules.billing import commerce as service
from app.modules.billing.models import Plan,PlanVersion
from app.modules.billing.commerce_models import CoinPack,StripePriceBinding
from app.modules.billing.service import BillingError
from app.modules.wallet.models import CoinLedgerEntry,WalletAccount
from app.modules.ops.managed_settings import get_config,save_config

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
    currency:str='USD'
    base_coins:int=Field(ge=1)
    bonus_coins:int=Field(default=0,ge=0)
    sort_order:int=Field(default=0,ge=0)
    badge:str=Field(default='',max_length=64)
    note:str=Field(default='',max_length=500)
class PackPatch(BaseModel):
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
    idempotency_key:str=Field(min_length=8,max_length=100)
    success_url:str|None=Field(default=None,max_length=2000)
    cancel_url:str|None=Field(default=None,max_length=2000)
    source_drama_id:int|None=None
    attribution_pid:str|None=Field(default=None,max_length=100)
class RefundIn(BaseModel):
    reason:str=Field(min_length=1,max_length=500)
    idempotency_key:str=Field(min_length=8,max_length=100)

@public_router.get('/coin-packs')
def coin_packs(db:Session=Depends(get_db)):return {'items':service.list_packs(db)}
@public_router.get('/billing/channels')
def channels(db:Session=Depends(get_db)):
    return {'items':[dict(code=x['code'],name=x['name'],provider='stripe',enabled=True) for x in service.account_list(db) if x['status']=='active' and x.get('secret_key_configured')]}
@public_router.post('/commerce/checkout')
def checkout(body:CheckoutIn,user=Depends(require_user),db:Session=Depends(get_db)):
    return call(db,service.checkout,db,user_id=user.id,email=user.email,**body.model_dump())
@public_router.post('/commerce/orders/{order_no}/cancel')
def cancel_checkout(order_no:str,user=Depends(require_user),db:Session=Depends(get_db)):
    return call(db,service.cancel_checkout,db,user.id,order_no)
@public_router.get('/commerce/orders')
def orders(limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0),user=Depends(require_user),db:Session=Depends(get_db)):
    return service.list_orders(db,user.id,limit,offset)
@public_router.post('/billing/stripe/accounts/{code}/webhook')
async def webhook(code:str,request:Request,stripe_signature:str=Header(default='',alias='Stripe-Signature'),db:Session=Depends(get_db)):
    body=await request.body()
    call(db,service.signature,db,code,body,stripe_signature)
    try:payload=__import__('json').loads(body)
    except (ValueError,TypeError):raise HTTPException(400,'invalid JSON')
    result=call(db,service.process_event,db,code,payload)
    return {'received':True,**(result or {'ignored':payload.get('type')})}

@admin_router.get('/coin-packs')
def admin_packs(user=Depends(require_admin),db:Session=Depends(get_db)):return {'items':service.list_packs(db,True)}
@admin_router.post('/coin-packs')
def create_pack(body:PackIn,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'coin_packs.manage')
    if db.scalar(select(CoinPack).where(CoinPack.code==body.code)):raise HTTPException(409,'pack code already exists')
    return call(db,service.save_pack,db,body.code,body.model_dump(exclude={'code'}),user.id)
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
    for plan in db.scalars(select(Plan).order_by(Plan.id)):
        version=db.scalar(select(PlanVersion).where(PlanVersion.plan_id==plan.id).order_by(PlanVersion.version.desc()).limit(1))
        if version:out.append(dict(code=plan.code,name_en=plan.name_en,name_zh=plan.name_zh,status=plan.status,price_cents=version.price_cents,currency=version.currency,version=version.version,base_coins=version.base_coins,bonus_coins=version.bonus_coins,benefits=version.benefits))
    from app.modules.billing.service import billing_mode
    return {'items':out,'billing_mode':billing_mode(db)}
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
    version=PlanVersion(plan_id=row.id,version=latest.version+1 if latest else 1,**body.model_dump());db.add(version)
    service.audit(db,user.id,'plan.version',row.id,dict(code=code));db.commit();return {'version':version.version,'price_cents':version.price_cents}

@admin_router.get('/stripe/accounts')
def stripe_accounts(user=Depends(require_admin),db:Session=Depends(get_db)):return {'items':service.account_list(db)}
@admin_router.post('/stripe/accounts')
def create_account(body:AccountIn,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'stripe.manage')
    return call(db,service.save_account,db,body.code,body.model_dump(exclude={'code'}),user.id)
@admin_router.patch('/stripe/accounts/{code}')
def patch_account(code:str,body:AccountPatch,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'stripe.manage')
    return call(db,service.save_account,db,code,body.model_dump(exclude_unset=True),user.id)
@admin_router.post('/stripe/accounts/{code}/test')
def account_test(code:str,request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
    require_permission(request,'stripe.manage');return call(db,service.test_account,db,code)
@admin_router.get('/stripe/prices')
def prices(user=Depends(require_admin),db:Session=Depends(get_db)):
    return {'items':[dict(account_code=x.account_code,product_type=x.product_type,version_id=x.version_id,billing_mode=x.billing_mode,price_id=x.price_id) for x in db.scalars(select(StripePriceBinding))]}
@admin_router.post('/stripe/prices/provision')
def provision(request:Request,user=Depends(require_admin),db:Session=Depends(get_db)):
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
    day=func.date_trunc('day',func.timezone('America/Los_Angeles',CoinLedgerEntry.created_at))
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
