"""Immutable commercial snapshots and source-tracked refundable entitlements."""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, ForeignKey
from app.db import Base

class CoinPack(Base):
    __tablename__ = 'coin_packs'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name_en: Mapped[str] = mapped_column(String(64))
    name_zh: Mapped[str] = mapped_column(String(64), default='')
    status: Mapped[str] = mapped_column(String(16), default='active')
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    badge: Mapped[str] = mapped_column(String(64), default='')

class CoinPackVersion(Base):
    __tablename__ = 'coin_pack_versions'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pack_id: Mapped[int] = mapped_column(ForeignKey('coin_packs.id'))
    version: Mapped[int] = mapped_column(Integer)
    price_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default='USD')
    base_coins: Mapped[int] = mapped_column(Integer)
    bonus_coins: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint('pack_id','version'), CheckConstraint('price_cents >= 50 AND base_coins > 0 AND bonus_coins >= 0',name='ck_pack_values'))

class StripeAccount(Base):
    __tablename__ = 'stripe_accounts'
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default='draft')
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    livemode: Mapped[bool] = mapped_column(Boolean, default=False)

class StripePriceBinding(Base):
    __tablename__ = 'stripe_price_bindings'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_code: Mapped[str] = mapped_column(String(32))
    product_type: Mapped[str] = mapped_column(String(16))
    version_id: Mapped[int] = mapped_column(BigInteger)
    billing_mode: Mapped[str] = mapped_column(String(16))
    price_id: Mapped[str] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint('account_code','product_type','version_id','billing_mode'),)

class CoinSource(Base):
    __tablename__ = 'coin_sources'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey('orders.id'), nullable=True, unique=True)
    original: Mapped[int] = mapped_column(Integer)
    remaining: Mapped[int] = mapped_column(Integer)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint('original >= 0 AND remaining >= 0 AND reserved >= 0 AND remaining + reserved <= original',name='ck_coin_source_bounds'),)

class CoinAllocation(Base):
    __tablename__ = 'coin_allocations'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey('coin_sources.id'))
    ledger_id: Mapped[int] = mapped_column(ForeignKey('coin_ledger.id'))
    amount: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint('source_id','ledger_id'),CheckConstraint('amount > 0',name='ck_coin_allocation_positive'))

class MembershipGrant(Base):
    __tablename__ = 'membership_grants'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('orders.id'), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default='active')
    access_consumed: Mapped[bool] = mapped_column(Boolean, default=False)

class CommerceSettlement(Base):
    __tablename__ = 'commerce_settlements'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('orders.id'), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class CommerceRefund(Base):
    __tablename__ = 'commerce_refunds'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('orders.id'), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(24), default='pending')
    refund_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    reason: Mapped[str] = mapped_column(String(500))
    error: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StripeSubscriptionLifecycle(Base):
    """Provider lifecycle facts may precede the first paid invoice."""
    __tablename__='stripe_subscription_lifecycle'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)
    account_code: Mapped[str]=mapped_column(String(32),primary_key=True)
    subscription_id: Mapped[str]=mapped_column(String(100),primary_key=True)
    order_id: Mapped[int]=mapped_column(ForeignKey('orders.id'),nullable=False)
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    deleted: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    cancel_at_period_end: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    canceled_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    last_event_created: Mapped[int]=mapped_column(BigInteger,nullable=False,default=0)
    last_event_id: Mapped[str]=mapped_column(String(100),nullable=False,default='')


class SiteMerchantBinding(Base):
    """Explicit site-to-merchant use; credentials remain an instance resource."""
    __tablename__='site_merchant_bindings'
    tenant_id: Mapped[str]=mapped_column(ForeignKey('tenants.id',ondelete='RESTRICT'),primary_key=True)
    account_code: Mapped[str]=mapped_column(String(32),nullable=False)
    explicit_reuse: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    updated_by: Mapped[int|None]=mapped_column(BigInteger,nullable=True)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now(),onupdate=func.now())

class StripeWebhookReceipt(Base):
    """Merchant + provider event identity; incomplete deliveries are retryable."""
    __tablename__='stripe_webhook_receipts'
    account_code: Mapped[str]=mapped_column(String(32),primary_key=True)
    event_id: Mapped[str]=mapped_column(String(100),primary_key=True)
    tenant_id: Mapped[str]=mapped_column(ForeignKey('tenants.id'),nullable=False)
    payload_hash: Mapped[str]=mapped_column(String(64),nullable=False)
    processed: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False,server_default='false')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())


class GatewayAccount(Base):
    """Instance-managed merchant with provider-specific configuration."""

    __tablename__ = 'gateway_accounts'
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    identity: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    credentials: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint('provider', 'identity', name='uq_gateway_provider_identity'),
        CheckConstraint("status IN ('draft', 'active', 'disabled')", name='ck_gateway_account_status'),
    )


class SiteGatewayBinding(Base):
    __tablename__ = 'site_gateway_bindings'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_code: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(['provider', 'account_code'], ['gateway_accounts.provider', 'gateway_accounts.code'], ondelete='RESTRICT'),
        UniqueConstraint('provider', 'account_code', name='uq_site_gateway_merchant'),
    )


class GatewayProductBinding(Base):
    """An external catalog item bound to an exact local product version."""

    __tablename__ = 'gateway_product_bindings'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    product_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    version_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    billing_mode: Mapped[str] = mapped_column(String(16), primary_key=True)
    external_product_id: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(['provider', 'account_code'], ['gateway_accounts.provider', 'gateway_accounts.code'], ondelete='RESTRICT'),
    )
