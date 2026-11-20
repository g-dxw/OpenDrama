"""Billing module: plans, plan versions, subscriptions, orders.

Price changes create a new plan version; purchases pin the version they
bought, so existing subscriptions keep their original entitlement snapshot
(ADR: price edits never affect existing subscribers).
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sqlalchemy import String, ForeignKey
from app.db import Base


class Plan(Base):
    __tablename__ = "plans"
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # weekly / monthly / yearly
    name_en: Mapped[str] = mapped_column(String(64), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active / retired
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PlanVersion(Base):
    __tablename__ = "plan_versions"
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)  # $19.99 -> 1999
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    benefits: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # entitlement snapshot
    base_coins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    bonus_coins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")  # price change reason, audited
    stripe_price_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # recurring
    stripe_one_time_price_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("plan_id", "version", name="uq_plan_versions_plan_version"),
    )


class Subscription(Base):
    """At most one active subscription per user (partial unique index)."""

    __tablename__ = "subscriptions"
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    plan_version_id: Mapped[int] = mapped_column(ForeignKey("plan_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # active / canceled / expired
    current_period_start: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    canceled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    account_code: Mapped[str] = mapped_column(String(32), nullable=False, default="env", server_default="env")
    managed_commerce: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Durable provider-specific cancellation evidence and replay information.
    cancellation_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_price_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    creem_subscription_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pancake_subscription_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    billing_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="subscription")
    # End of one-time passes; independent from current_period_end so a renewal
    # never wipes access that was bought outright.
    pass_end: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Inherited by renewals for reporting; settlement still counts only the
    # first paid order (see docs/supplier-role-plan.md §6).
    source_drama_id: Mapped[int | None] = mapped_column(
        ForeignKey("dramas.id"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_subscriptions_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_subscriptions_creem_account_id",
            "account_code",
            "creem_subscription_id",
            unique=True,
            postgresql_where=text("creem_subscription_id IS NOT NULL"),
        ),
        Index(
            "uq_subscriptions_pancake_account_id",
            "account_code",
            "pancake_subscription_id",
            unique=True,
            postgresql_where=text("pancake_subscription_id IS NOT NULL"),
        ),
    )


class Order(Base):
    __tablename__ = "orders"
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    product_type: Mapped[str] = mapped_column(String(16), nullable=False, default="membership", server_default="membership")
    product_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="stripe", server_default="stripe")
    account_code: Mapped[str] = mapped_column(String(32), nullable=False, default="env", server_default="env")
    checkout_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default='{}')
    checkout_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    checkout_key: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    payment_intent_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    subscription_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attribution_pid: Mapped[str | None] = mapped_column(String(100), nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    plan_version_id: Mapped[int | None] = mapped_column(ForeignKey("plan_versions.id"), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    billing_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="subscription")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created")  # created / paid / failed / refunded
    external_txn_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True
    )  # provider transaction id; unique => duplicate callbacks cannot double-book
    paid_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Membership attribution: the drama the buyer came from. NULL = organic.
    source_drama_id: Mapped[int | None] = mapped_column(
        ForeignKey("dramas.id"), nullable=True
    )
    # Settlement needs the month a refund happened, not just the status.
    refunded_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_orders_user_created", "user_id", "created_at"),
        Index(
            "ix_orders_source_drama",
            "source_drama_id",
            "paid_at",
            postgresql_where=text("source_drama_id IS NOT NULL"),
        ),
        Index(
            "ix_orders_refunded_at",
            "refunded_at",
            postgresql_where=text("refunded_at IS NOT NULL"),
        ),
    )


class PancakePeriodEvidence(Base):
    """Charge and period notifications arrive independently and may be reordered."""

    __tablename__ = "pancake_period_evidence"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    initial_order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), primary_key=True)
    period_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[str | None] = mapped_column(String(100))
    payment_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    period_start: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    settled_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), unique=True)

    __table_args__ = (
        CheckConstraint("period_number > 0", name="ck_pancake_evidence_positive_period"),
        CheckConstraint("(payment_id IS NULL) = (payment_key IS NULL)", name="ck_pancake_evidence_payment_pair"),
        CheckConstraint(
            "(period_start IS NULL AND period_end IS NULL) OR "
            "(period_start IS NOT NULL AND period_end IS NOT NULL AND period_end > period_start)",
            name="ck_pancake_evidence_valid_window",
        ),
    )


class BillingSettings(Base):
    """Single-row global switch: how *new* purchases are billed."""

    __tablename__ = "billing_settings"
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, default='jjtv', server_default='jjtv', index=True)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    billing_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="one_time")
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
