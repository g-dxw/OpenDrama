"""Both advertised original-merchant webhooks preserve legacy subscriptions.

Stripe is mocked; HTTP requests still use real per-account signatures and the
real migration/schema. No provider writes are made.
"""
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.billing import commerce as commerce
from app.modules.billing import service as billing
from app.modules.billing.commerce_models import CommerceSettlement, MembershipGrant
from app.modules.billing.models import Order, Plan, PlanVersion, Subscription
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount
from tests.test_commerce import commercial, membership_order

UTC = timezone.utc
ENV_WEBHOOK = '/v1/billing/stripe/accounts/env/webhook'
OLD_WEBHOOK = '/v1/billing/stripe/webhook'


def post_event(client, payload, path=ENV_WEBHOOK, secret='whsec_unit'):
    body = json.dumps(payload).encode()
    stamp = int(time.time())
    digest = hmac.new(secret.encode(), str(stamp).encode() + b'.' + body, hashlib.sha256).hexdigest()
    return client.post(path, content=body, headers={'Stripe-Signature': f't={stamp},v1={digest}'})


def legacy_subscription(db, user, *, pass_end=None):
    plan = Plan(code='monthly', name_en='Monthly')
    db.add(plan); db.flush()
    version = PlanVersion(plan_id=plan.id, version=1, price_cents=1999, benefits={})
    db.add(version); db.flush()
    now = datetime.now(UTC).replace(microsecond=0)
    row = Subscription(user_id=user.id, plan_id=plan.id, plan_version_id=version.id,
        status='active', billing_mode='subscription', account_code='env', managed_commerce=False,
        stripe_subscription_id='sub_legacy', stripe_customer_id='cus_legacy',
        current_period_start=now-timedelta(days=28), current_period_end=now+timedelta(days=2), pass_end=pass_end)
    order = Order(order_no='LEGACY-ORDER', user_id=user.id, plan_version_id=version.id,
        external_txn_id='cs_legacy', amount_cents=1999, currency='USD', status='paid', billing_mode='subscription')
    db.add_all([row, order]); db.commit()
    return row, order


def lifecycle(row, kind='customer.subscription.updated'):
    start = int(row.current_period_end.timestamp())
    return {'id': 'evt_legacy_'+kind, 'type': kind, 'data': {'object': {
        'id': row.stripe_subscription_id, 'customer': row.stripe_customer_id,
        'status': 'canceled' if kind.endswith('deleted') else 'active',
        'metadata': {'user_id': str(row.user_id), 'plan_code': 'monthly'},
        'items': {'data': [{'price': {'id': 'price_legacy'}, 'current_period_start': start,
                            'current_period_end': start+30*86400}]}}}}


def test_advertised_env_webhook_renews_legacy_subscription_without_repricing_or_minting(session, commercial):
    user, _, _ = commercial
    row, order = legacy_subscription(session, user)
    original_version = row.plan_version_id
    original_order = (order.amount_cents, order.plan_version_id, order.checkout_key, order.subscription_ref)
    session.add(PlanVersion(plan_id=row.plan_id, version=2, price_cents=2999, base_coins=999, benefits={}))
    session.commit()
    event = lifecycle(row); expected_end = datetime.fromtimestamp(event['data']['object']['items']['data'][0]['current_period_end'], UTC)
    client = TestClient(app)
    for path in [ENV_WEBHOOK, OLD_WEBHOOK, ENV_WEBHOOK]:
        response = post_event(client, event, path)
        assert response.status_code == 200, response.text
        session.expire_all()
        assert session.get(Subscription, row.id).current_period_end == expected_end
    session.expire_all(); row = session.get(Subscription, row.id); order = session.get(Order, order.id)
    assert row.current_period_end == expected_end
    assert row.plan_version_id == original_version and not row.managed_commerce and row.account_code == 'env'
    assert (order.amount_cents, order.plan_version_id, order.checkout_key, order.subscription_ref) == original_order
    assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert session.get(WalletAccount, user.id).balance == 0
    assert session.scalar(select(func.count()).select_from(CoinLedgerEntry)) == 0
    assert session.scalar(select(func.count()).select_from(MembershipGrant)) == 0


@pytest.mark.parametrize('keep_pass', [False, True])
def test_advertised_env_webhook_cancels_legacy_subscription_and_preserves_paid_pass(session, commercial, keep_pass):
    user, _, _ = commercial
    pass_end = datetime.now(UTC) + timedelta(days=90) if keep_pass else None
    row, _ = legacy_subscription(session, user, pass_end=pass_end)
    event = lifecycle(row, 'customer.subscription.deleted')
    client = TestClient(app)
    for _ in range(2):
        response = post_event(client, event)
        assert response.status_code == 200, response.text
    session.expire_all(); row = session.get(Subscription, row.id)
    assert row.canceled_at is not None
    assert row.status == ('active' if keep_pass else 'canceled')
    assert billing.access_until(session, user.id) == pass_end


def test_non_original_merchant_does_not_dispatch_env_legacy_events(session, commercial):
    user, actor, _ = commercial
    row, _ = legacy_subscription(session, user); old_end = row.current_period_end
    commerce.save_account(session, 'second', {'name': 'Second', 'status': 'active',
        'secret_key': 'sk_test_SECOND', 'webhook_secret': 'whsec_second'}, actor.id)
    response = post_event(TestClient(app), lifecycle(row), '/v1/billing/stripe/accounts/second/webhook', 'whsec_second')
    assert response.status_code == 200, response.text
    session.expire_all()
    assert session.get(Subscription, row.id).current_period_end == old_end
    assert session.get(Subscription, row.id).account_code == 'env'


def test_env_webhook_rejects_invalid_signature_before_legacy_dispatch(session, commercial):
    user, _, _ = commercial
    row, _ = legacy_subscription(session, user); old_end = row.current_period_end
    response = post_event(TestClient(app), lifecycle(row), secret='whsec_wrong')
    assert response.status_code == 401
    session.expire_all()
    assert session.get(Subscription, row.id).current_period_end == old_end


def test_original_webhook_aliases_settle_managed_invoice_once_without_legacy_access(session, commercial):
    user, _, _ = commercial
    billing.get_billing_settings(session).billing_mode = 'subscription'; session.commit()
    order = membership_order(session, user)
    event = {'type': 'customer.subscription.created', 'data': {'object': {
        'id': 'sub_managed_env', 'status': 'active',
        'metadata': {'commerce_order_no': order.order_no, 'user_id': str(user.id), 'plan_code': 'monthly'}}}}
    client = TestClient(app)
    for path in [ENV_WEBHOOK, OLD_WEBHOOK]:
        response = post_event(client, event, path)
        assert response.status_code == 200, response.text
    session.expire_all()
    assert billing.access_until(session, user.id) is None
    assert session.scalar(select(func.count()).select_from(Subscription)) == 0
    stamp = int(time.time())
    event = {'type': 'invoice.paid', 'data': {'object': {
        'id': 'in_managed_env', 'subscription': 'sub_managed_env', 'payment_intent': 'pi_managed_env',
        'amount_paid': 1999, 'currency': 'usd', 'customer': 'cus_managed_env',
        'lines': {'data': [{'period': {'start': stamp, 'end': stamp+30*86400}}]}}}}
    for path in [ENV_WEBHOOK, OLD_WEBHOOK, ENV_WEBHOOK]:
        response = post_event(client, event, path)
        assert response.status_code == 200, response.text
    session.expire_all()
    assert session.get(WalletAccount, user.id).balance == 25
    assert session.scalar(select(func.count()).select_from(CommerceSettlement)) == 1
    assert session.scalar(select(func.count()).select_from(MembershipGrant)) == 1
    assert session.scalar(select(Subscription)).managed_commerce is True


@pytest.mark.parametrize('path',[OLD_WEBHOOK,ENV_WEBHOOK])
def test_migrated_unverified_env_live_key_accepts_signed_live_payments(session,commercial,monkeypatch,path):
    from app.modules.billing.commerce_models import StripeAccount
    user,_actor,_calls=commercial
    monkeypatch.setenv('STRIPE_SECRET_KEY','sk_live_historical_fixture')
    # Match a1114commerce's original seed, including its placeholder false mode.
    account=StripeAccount(code='env',name='Migrated original merchant',status='active',is_default=True,remote_id=None,livemode=False)
    session.add(account);session.commit()
    order=membership_order(session,user,'migrated-live-merchant')
    payload={'id':'evt_historical_live','livemode':True,'type':'checkout.session.completed','data':{'object':{
        'id':order.external_txn_id,'mode':'payment','payment_status':'paid',
        'amount_total':order.amount_cents,'currency':order.currency.lower(),
        'payment_intent':'pi_historical_live','metadata':{'commerce_order_no':order.order_no}}}}
    client=TestClient(app)
    first=post_event(client,payload,path)
    assert first.status_code==200,first.text
    session.expire_all()
    assert session.get(Order,order.id).status=='paid'
    assert session.scalar(select(MembershipGrant).where(MembershipGrant.order_id==order.id)).status=='active'
    balance=session.get(WalletAccount,user.id).balance
    assert balance==25
    repeat=post_event(client,payload,path)
    assert repeat.status_code==200,repeat.text
    assert repeat.json()['replayed'] is True
    session.expire_all()
    assert session.get(WalletAccount,user.id).balance==balance
    assert session.scalar(select(func.count()).select_from(CommerceSettlement).where(CommerceSettlement.order_id==order.id))==1


def test_verified_merchant_mode_remains_authoritative_over_live_key_prefix(session,commercial,monkeypatch):
    from app.modules.billing.commerce_models import StripeAccount
    user,_actor,_calls=commercial
    monkeypatch.setenv('STRIPE_SECRET_KEY','sk_live_historical_fixture')
    session.add(StripeAccount(code='env',name='Verified test merchant',status='active',is_default=True,remote_id='acct_verified_test',livemode=False));session.commit()
    order=membership_order(session,user,'verified-merchant-mode-fence')
    payload={'id':'evt_wrong_verified_mode','account':'acct_verified_test','livemode':True,'type':'checkout.session.completed','data':{'object':{
        'id':order.external_txn_id,'mode':'payment','payment_status':'paid',
        'amount_total':order.amount_cents,'currency':order.currency.lower(),
        'payment_intent':'pi_verified_mode','metadata':{'commerce_order_no':order.order_no}}}}
    before=session.get(WalletAccount,user.id).balance
    response=post_event(TestClient(app),payload)
    assert response.status_code==401,response.text
    session.expire_all()
    assert session.get(Order,order.id).status=='created'
    assert session.get(WalletAccount,user.id).balance==before
    assert session.scalar(select(func.count()).select_from(CommerceSettlement).where(CommerceSettlement.order_id==order.id))==0
    assert session.scalar(select(func.count()).select_from(MembershipGrant).where(MembershipGrant.order_id==order.id))==0
