from __future__ import annotations

from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class SiteSettings(Base):
    __tablename__ = "site_settings"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    target_markets: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    default_locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en", server_default="en")
    locales: Mapped[list] = mapped_column(JSONB, nullable=False, default=lambda: ["en", "zh"], server_default='["en","zh"]')
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD", server_default="USD")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")
    web_url: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    h5_url: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    features: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class SiteBrand(Base):
    __tablename__ = "site_brands"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    draft: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    published: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class BrandRevision(Base):
    __tablename__ = "site_brand_revisions"
    __table_args__ = (UniqueConstraint("tenant_id", "version", name="uq_site_brand_revision"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DomainVerification(Base):
    __tablename__ = "site_domain_verifications"
    domain_id: Mapped[str] = mapped_column(ForeignKey("tenant_domains.id", ondelete="CASCADE"), primary_key=True)
    token: Mapped[str] = mapped_column(String(100), nullable=False)
