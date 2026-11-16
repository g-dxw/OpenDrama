"""Lazy billing defaults must remain inside their caller's transaction."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.modules.billing import commerce, service as billing
from app.modules.billing.models import BillingSettings
from app.modules.tenancy.consumer_scope import site_context
from app.modules.wallet.models import WalletAccount


def test_lazy_billing_default_does_not_commit_wallet_changes(session, make):
    user = make.user(coins=77)
    wallet = commerce.wallet_lock(session, user.id)
    wallet.balance = 99
    assert billing.get_billing_settings(session).billing_mode == 'one_time'
    session.rollback()
    session.expire_all()
    # Initializing an optional mode must not make another caller's financial
    # mutation durable or release its transaction before it decides to commit.
    assert session.get(WalletAccount, user.id).balance == 77
    assert session.scalar(select(func.count()).select_from(BillingSettings)) == 0


def test_two_first_billing_reads_share_one_committed_default(engine, session, monkeypatch):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    original = billing.new_billing_settings
    both_observed_missing = Barrier(2)
    def simultaneous_initializers(db, tenant_id=None):
        both_observed_missing.wait(timeout=10)
        return original(db, tenant_id)
    monkeypatch.setattr(billing, 'new_billing_settings', simultaneous_initializers)
    def initialize(_index):
        with factory() as db, site_context(db, 'jjtv'):
            row = billing.get_billing_settings(db)
            db.commit()
            return row.id, row.billing_mode, row.tenant_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(initialize, range(2)))
    assert results[0] == results[1]
    assert results[0][1:] == ('one_time', 'jjtv')
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(BillingSettings)) == 1
