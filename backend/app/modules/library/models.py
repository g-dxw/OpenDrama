"""Central masters are separate from site-specific Drama/Episode identities."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class LibraryTitle(Base):
    __tablename__ = 'library_titles'
    __table_args__ = (CheckConstraint("status IN ('draft','approved','archived')", name='ck_library_title_status'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    synopsis: Mapped[str] = mapped_column(Text, nullable=False, default='')
    cover_url: Mapped[str] = mapped_column(String(500), nullable=False, default='')
    original_language: Mapped[str] = mapped_column(String(16), nullable=False, default='en')
    rights_note: Mapped[str] = mapped_column(Text, nullable=False, default='')
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='draft')
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('admin_users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class LibraryEpisode(Base):
    __tablename__ = 'library_episodes'
    __table_args__ = (UniqueConstraint('library_title_id', 'episode_number', name='uq_library_episode_number'),
                     CheckConstraint('episode_number > 0', name='ck_library_episode_number'))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    library_title_id: Mapped[int] = mapped_column(ForeignKey('library_titles.id', ondelete='RESTRICT'), nullable=False, index=True)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default='')
    language: Mapped[str] = mapped_column(String(16), nullable=False, default='en')
    subtitles: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    media_asset_id: Mapped[int | None] = mapped_column(ForeignKey('library_media_assets.id', name='fk_library_episode_current_asset', use_alter=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MediaAsset(Base):
    __tablename__ = 'library_media_assets'
    __table_args__ = (UniqueConstraint('library_episode_id', 'version', name='uq_library_asset_version'),
                     UniqueConstraint('library_episode_id', 'id', name='uq_library_asset_episode_id'),
                     CheckConstraint("status IN ('created','transcoding','ready','failed','retired')", name='ck_library_asset_status'))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    library_episode_id: Mapped[int] = mapped_column(ForeignKey('library_episodes.id', ondelete='RESTRICT'), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default='bunny')
    provider_video_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default='en')
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='created')
    upload_offset: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class LibraryRevision(Base):
    """Immutable approved snapshot; later editing never rewrites an imported edition."""
    __tablename__ = 'library_revisions'
    __table_args__ = (UniqueConstraint('library_title_id', 'version', name='uq_library_revision'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    library_title_id: Mapped[int] = mapped_column(ForeignKey('library_titles.id', ondelete='RESTRICT'), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey('admin_users.id'), nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DistributionGrant(Base):
    __tablename__ = 'distribution_grants'
    __table_args__ = (
        UniqueConstraint('library_title_id', 'tenant_id', 'title_version', name='uq_distribution_grant_edition'),
        UniqueConstraint('tenant_id', 'id', 'library_title_id', 'title_version', name='uq_distribution_grant_site_source'),
        ForeignKeyConstraint(['library_title_id', 'title_version'], ['library_revisions.library_title_id', 'library_revisions.version'], name='fk_distribution_approved_revision'),
        CheckConstraint("status IN ('active','revoked')", name='ck_distribution_grant_status'),
        CheckConstraint('sales_until IS NULL OR play_until IS NULL OR sales_until <= play_until', name='ck_distribution_sale_before_play'),
        CheckConstraint('starts_at IS NULL OR play_until IS NULL OR starts_at < play_until', name='ck_distribution_grant_window'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    library_title_id: Mapped[int] = mapped_column(ForeignKey('library_titles.id', ondelete='RESTRICT'), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False, index=True)
    title_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='active')
    countries: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    languages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sales_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    play_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('admin_users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
