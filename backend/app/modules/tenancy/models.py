"""Platform tenant directory and per-operator India authorizations."""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

DEFAULT_TENANT = "jjtv"
INDIA_TENANT = "india-short-drama"

class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint("kind IN ('native','india_legacy')", name="ck_tenants_kind"),
                     CheckConstraint("status IN ('active','paused')", name="ck_tenants_status"),
                     CheckConstraint("kind <> 'india_legacy' OR id = 'india-short-drama'", name="ck_tenants_india_fixed"))
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="native")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class TenantMember(Base):
    __tablename__ = "tenant_members"
    __table_args__ = (CheckConstraint("role IN ('admin','ops','finance')", name="ck_tenant_members_role"),)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="ops")

class IndiaConnection(Base):
    __tablename__ = "india_connections"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True)
    session_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    secret: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
