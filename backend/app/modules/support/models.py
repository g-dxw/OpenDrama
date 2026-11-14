"""JJTV customer support; public messages and private staff notes stay separate."""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, CheckConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

class SupportConfig(Base):
    __tablename__ = "support_config"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    assignment_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (CheckConstraint("status IN ('open','in_progress','waiting_customer','resolved','closed','merged')", name="ck_support_status"),
        CheckConstraint("priority IN ('low','normal','high','urgent')", name="ck_support_priority"),
        CheckConstraint("csat_score IS NULL OR csat_score BETWEEN 1 AND 5", name="ck_support_csat"),
        Index("ix_support_tenant_status_updated", "tenant_id", "status", "updated_at"), Index("ix_support_user", "user_id", "created_at"))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, default="jjtv")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    order_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    team_code: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("support_tickets.id"), nullable=True)
    sla_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    paused_minutes: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_admin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_user_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    csat_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    csat_comment: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class SupportMessage(Base):
    __tablename__ = "support_messages"
    __table_args__ = (Index("ix_support_messages_ticket", "ticket_id", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, default="jjtv")
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), nullable=False)
    author_type: Mapped[str] = mapped_column(String(16), nullable=False)
    author_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class SupportEvent(Base):
    __tablename__ = "support_events"
    __table_args__ = (Index("ix_support_events_ticket", "ticket_id", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
