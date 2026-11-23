"""Platform tenant directory and per-operator India authorizations."""
from __future__ import annotations
from datetime import datetime
from uuid import uuid4
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, String, Text, UniqueConstraint, column, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

DEFAULT_TENANT = "jjtv"

class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (UniqueConstraint("code", name="uq_tenants_code"), CheckConstraint("kind = 'native'", name="ck_tenants_kind"),
                     CheckConstraint("status IN ('active','paused')", name="ck_tenants_status"))
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, default=lambda context: context.get_current_parameters()["id"])
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
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
        CheckConstraint("builtin_key IS NULL OR builtin_key IN ('admin','ops','finance','tenant_admin')", name="ck_tenant_roles_builtin_key"),
        CheckConstraint("kind IN ('business','tenant_admin')", name="ck_tenant_roles_kind"),
        CheckConstraint("(kind = 'tenant_admin' AND builtin_key IS NOT NULL AND builtin_key = 'tenant_admin') OR (kind = 'business' AND (builtin_key IS NULL OR builtin_key <> 'tenant_admin'))", name="ck_tenant_roles_system_kind"),
        CheckConstraint("jsonb_typeof(permissions) = 'array'", name="ck_tenant_roles_permissions_array"),
        CheckConstraint("version >= 1", name="ck_tenant_roles_version_positive"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="business", server_default="business")
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
        ForeignKeyConstraint(["tenant_id", "user_id"], ["admin_users.tenant_id", "admin_users.id"], name="fk_tenant_members_account_tenant", ondelete="CASCADE"),
        Index("ix_tenant_members_tenant_role", "tenant_id", "role_id"),
    )
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="ops")
    role_id: Mapped[str] = mapped_column(String(64), nullable=False)

class TenantModule(Base):
    __tablename__ = 'tenant_modules'
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True)
    module_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default='false')

class TenantDomain(Base):
    __tablename__ = 'tenant_domains'
    __table_args__ = (
        CheckConstraint("kind IN ('admin','site')", name='ck_tenant_domains_kind'),
        CheckConstraint("status IN ('pending','active')", name='ck_tenant_domains_status'),
        CheckConstraint("status <> 'active' OR verified_at IS NOT NULL", name='ck_tenant_domains_verified_active'),
        Index('uq_tenant_domains_hostname_lower', func.lower(column('hostname')), unique=True),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: 'domain-'+uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False)
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='pending', server_default='pending')
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
