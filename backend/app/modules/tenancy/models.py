"""Platform tenant directory and per-operator India authorizations."""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
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

class TenantRole(Base):
    __tablename__ = "tenant_roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_tenant_roles_tenant_role_id"),
        UniqueConstraint("tenant_id", "builtin_key", name="uq_tenant_roles_tenant_builtin"),
        UniqueConstraint("tenant_id", "name", name="uq_tenant_roles_tenant_name"),
        CheckConstraint("builtin_key IS NULL OR builtin_key IN ('admin','ops','finance')", name="ck_tenant_roles_builtin_key"),
        CheckConstraint("jsonb_typeof(permissions) = 'array'", name="ck_tenant_roles_permissions_array"),
        CheckConstraint("version >= 1", name="ck_tenant_roles_version_positive"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    builtin_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    permissions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

class TenantMember(Base):
    __tablename__ = "tenant_members"
    __table_args__ = (
        CheckConstraint("role IN ('admin','ops','finance')", name="ck_tenant_members_role"),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"], ["tenant_roles.tenant_id", "tenant_roles.id"],
            name="fk_tenant_members_tenant_role", ondelete="RESTRICT",
        ),
        Index("ix_tenant_members_tenant_role", "tenant_id", "role_id"),
    )
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="ops")
    role_id: Mapped[str] = mapped_column(String(64), nullable=False)

class IndiaConnection(Base):
    __tablename__ = "india_connections"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True)
    session_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    secret: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
