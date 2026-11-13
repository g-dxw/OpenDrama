"""Attribution configuration and transactional conversion delivery outbox."""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, CheckConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

class AttributionPixel(Base):
    __tablename__="attribution_pixels"
    __table_args__=(CheckConstraint("platform IN ('meta','tiktok')",name="ck_attr_pixel_platform"),UniqueConstraint("tenant_id","platform","pixel_id",name="uq_attr_pixel_identity"))
    id:Mapped[int]=mapped_column(BigInteger,primary_key=True,autoincrement=True)
    tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),nullable=False,default="jjtv")
    name:Mapped[str]=mapped_column(String(120),nullable=False)
    platform:Mapped[str]=mapped_column(String(16),nullable=False)
    pixel_id:Mapped[str]=mapped_column(String(100),nullable=False)
    encrypted_token:Mapped[str]=mapped_column(Text,nullable=False,default="")
    enabled:Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    delivery_mode:Mapped[str]=mapped_column(String(16),nullable=False,default="test")
    test_event_code:Mapped[str]=mapped_column(String(100),nullable=False,default="")
    expires_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),onupdate=func.now(),nullable=False)

class AttributionSource(Base):
    __tablename__="attribution_sources"
    id:Mapped[int]=mapped_column(BigInteger,primary_key=True,autoincrement=True)
    tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),nullable=False,default="jjtv")
    name:Mapped[str]=mapped_column(String(120),nullable=False)
    account_id:Mapped[str]=mapped_column(String(120),nullable=False,default="")
    pixel_id:Mapped[int]=mapped_column(ForeignKey("attribution_pixels.id"),nullable=False)
    timezone:Mapped[str]=mapped_column(String(64),nullable=False,default="Asia/Shanghai")
    currency:Mapped[str]=mapped_column(String(3),nullable=False,default="USD")
    enabled:Mapped[bool]=mapped_column(Boolean,nullable=False,default=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)

class AttributionPlan(Base):
    __tablename__="attribution_plans"
    id:Mapped[int]=mapped_column(BigInteger,primary_key=True,autoincrement=True)
    tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),nullable=False,default="jjtv")
    pid:Mapped[str]=mapped_column(String(48),nullable=False,unique=True)
    name:Mapped[str]=mapped_column(String(120),nullable=False)
    source_id:Mapped[int]=mapped_column(ForeignKey("attribution_sources.id"),nullable=False)
    pixel_id:Mapped[int]=mapped_column(ForeignKey("attribution_pixels.id"),nullable=False)
    landing_path:Mapped[str]=mapped_column(String(500),nullable=False,default="/")
    region:Mapped[str]=mapped_column(String(64),nullable=False,default="")
    enabled:Mapped[bool]=mapped_column(Boolean,nullable=False,default=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)

class AttributionVisit(Base):
    __tablename__="attribution_visits"
    __table_args__=(Index("ix_attr_visit_plan_created","plan_id","created_at"),)
    id:Mapped[str]=mapped_column(String(48),primary_key=True)
    tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),nullable=False,default="jjtv")
    plan_id:Mapped[int]=mapped_column(ForeignKey("attribution_plans.id"),nullable=False)
    client_id:Mapped[str]=mapped_column(String(100),nullable=False,default="")
    context:Mapped[dict]=mapped_column(JSONB,nullable=False,default=dict)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)

class UserAttribution(Base):
    __tablename__="user_attribution"
    user_id:Mapped[int]=mapped_column(ForeignKey("users.id"),primary_key=True)
    tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),nullable=False,default="jjtv")
    plan_id:Mapped[int]=mapped_column(ForeignKey("attribution_plans.id"),nullable=False)
    context:Mapped[dict]=mapped_column(JSONB,nullable=False,default=dict)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)

class AttributionEvent(Base):
    __tablename__="attribution_events"
    __table_args__=(UniqueConstraint("pixel_id","event_id",name="uq_attr_event_dedupe"),Index("ix_attr_delivery_due","status","next_attempt_at"),Index("ix_attr_event_plan_created","plan_id","created_at"))
    id:Mapped[int]=mapped_column(BigInteger,primary_key=True,autoincrement=True)
    tenant_id:Mapped[str]=mapped_column(ForeignKey("tenants.id"),nullable=False,default="jjtv")
    plan_id:Mapped[int|None]=mapped_column(ForeignKey("attribution_plans.id"),nullable=True)
    pixel_id:Mapped[int]=mapped_column(ForeignKey("attribution_pixels.id"),nullable=False)
    event_id:Mapped[str]=mapped_column(String(160),nullable=False)
    kind:Mapped[str]=mapped_column(String(32),nullable=False)
    user_id:Mapped[int|None]=mapped_column(ForeignKey("users.id"),nullable=True)
    order_no:Mapped[str|None]=mapped_column(String(64),nullable=True)
    amount_cents:Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    currency:Mapped[str]=mapped_column(String(3),nullable=False,default="USD")
    test:Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    context:Mapped[dict]=mapped_column(JSONB,nullable=False,default=dict)
    status:Mapped[str]=mapped_column(String(16),nullable=False,default="pending")
    attempts:Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    next_attempt_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())
    last_error:Mapped[str]=mapped_column(String(2000),nullable=False,default="")
    response:Mapped[dict]=mapped_column(JSONB,nullable=False,default=dict)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())
    delivered_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
