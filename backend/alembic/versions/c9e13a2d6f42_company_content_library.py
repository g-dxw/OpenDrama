"""Company content masters and explicit, versioned site distribution.

Revision ID: c9e13a2d6f42
Revises: b8d02f1c5e31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'c9e13a2d6f42'
down_revision = 'b8d02f1c5e31'
branch_labels = None
depends_on = None


def _id():
    return sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True)


def _created():
    return sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade():
    op.create_table('library_titles', _id(),
        sa.Column('title', sa.String(200), nullable=False), sa.Column('synopsis', sa.Text(), nullable=False),
        sa.Column('cover_url', sa.String(500), nullable=False), sa.Column('original_language', sa.String(16), nullable=False),
        sa.Column('rights_note', sa.Text(), nullable=False), sa.Column('status', sa.String(16), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False), sa.Column('approved_version', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.BigInteger(), sa.ForeignKey('admin_users.id'), nullable=True), _created(),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft','approved','archived')", name='ck_library_title_status'))
    op.create_table('library_episodes', _id(),
        sa.Column('library_title_id', sa.BigInteger(), sa.ForeignKey('library_titles.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('episode_number', sa.Integer(), nullable=False), sa.Column('title', sa.String(200), nullable=False),
        sa.Column('language', sa.String(16), nullable=False),
        sa.Column('subtitles', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('media_asset_id', sa.BigInteger(), nullable=True), _created(),
        sa.UniqueConstraint('library_title_id', 'episode_number', name='uq_library_episode_number'),
        sa.CheckConstraint('episode_number > 0', name='ck_library_episode_number'))
    op.create_index('ix_library_episodes_library_title_id', 'library_episodes', ['library_title_id'])
    op.create_table('library_media_assets', _id(),
        sa.Column('library_episode_id', sa.BigInteger(), sa.ForeignKey('library_episodes.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('provider', sa.String(16), nullable=False), sa.Column('provider_video_id', sa.String(64), nullable=False, unique=True),
        sa.Column('version', sa.Integer(), nullable=False), sa.Column('language', sa.String(16), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False), sa.Column('mime', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False), sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False), sa.Column('duration_seconds', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False), sa.Column('upload_offset', sa.BigInteger(), nullable=False), _created(),
        sa.UniqueConstraint('library_episode_id', 'version', name='uq_library_asset_version'),
        sa.UniqueConstraint('library_episode_id', 'id', name='uq_library_asset_episode_id'),
        sa.CheckConstraint("status IN ('created','transcoding','ready','failed','retired')", name='ck_library_asset_status'))
    op.create_index('ix_library_media_assets_library_episode_id', 'library_media_assets', ['library_episode_id'])
    op.create_foreign_key('fk_library_episode_current_asset', 'library_episodes', 'library_media_assets', ['media_asset_id'], ['id'])
    op.create_table('library_revisions', _id(),
        sa.Column('library_title_id', sa.BigInteger(), sa.ForeignKey('library_titles.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False), sa.Column('snapshot', postgresql.JSONB(), nullable=False),
        sa.Column('approved_by', sa.BigInteger(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('library_title_id', 'version', name='uq_library_revision'))
    op.create_table('distribution_grants', _id(),
        sa.Column('library_title_id', sa.BigInteger(), sa.ForeignKey('library_titles.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('tenant_id', sa.String(64), sa.ForeignKey('tenants.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('title_version', sa.Integer(), nullable=False), sa.Column('status', sa.String(16), nullable=False),
        sa.Column('countries', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('languages', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sales_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('play_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.BigInteger(), sa.ForeignKey('admin_users.id'), nullable=True), _created(),
        sa.UniqueConstraint('library_title_id', 'tenant_id', 'title_version', name='uq_distribution_grant_edition'),
        sa.UniqueConstraint('tenant_id', 'id', 'library_title_id', 'title_version', name='uq_distribution_grant_site_source'),
        sa.ForeignKeyConstraint(['library_title_id','title_version'], ['library_revisions.library_title_id','library_revisions.version'], name='fk_distribution_approved_revision'),
        sa.CheckConstraint("status IN ('active','revoked')", name='ck_distribution_grant_status'),
        sa.CheckConstraint('sales_until IS NULL OR play_until IS NULL OR sales_until <= play_until', name='ck_distribution_sale_before_play'),
        sa.CheckConstraint('starts_at IS NULL OR play_until IS NULL OR starts_at < play_until', name='ck_distribution_grant_window'))
    op.create_index('ix_distribution_grants_library_title_id', 'distribution_grants', ['library_title_id'])
    op.create_index('ix_distribution_grants_tenant_id', 'distribution_grants', ['tenant_id'])
    op.add_column('dramas', sa.Column('library_title_id', sa.BigInteger(), nullable=True))
    op.add_column('dramas', sa.Column('distribution_grant_id', sa.BigInteger(), nullable=True))
    op.add_column('dramas', sa.Column('library_version', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_drama_library_title', 'dramas', 'library_titles', ['library_title_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key('fk_drama_distribution_grant', 'dramas', 'distribution_grants', ['distribution_grant_id'], ['id'], ondelete='RESTRICT')
    op.create_unique_constraint('uq_drama_site_library_title_version', 'dramas', ['tenant_id','library_title_id','library_version'])
    op.create_foreign_key('fk_drama_distribution_site_source', 'dramas', 'distribution_grants',
                          ['tenant_id','distribution_grant_id','library_title_id','library_version'],
                          ['tenant_id','id','library_title_id','title_version'])
    op.create_check_constraint('ck_drama_distribution_complete', 'dramas',
        '(library_title_id IS NULL AND distribution_grant_id IS NULL AND library_version IS NULL) OR (library_title_id IS NOT NULL AND distribution_grant_id IS NOT NULL AND library_version IS NOT NULL)')
    op.add_column('episodes', sa.Column('library_episode_id', sa.BigInteger(), nullable=True))
    op.add_column('episodes', sa.Column('media_asset_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key('fk_episode_library_episode', 'episodes', 'library_episodes', ['library_episode_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key('fk_episode_library_media_asset', 'episodes', 'library_media_assets', ['media_asset_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key('fk_episode_library_asset', 'episodes', 'library_media_assets', ['library_episode_id','media_asset_id'], ['library_episode_id','id'])
    op.create_check_constraint('ck_episode_library_source_complete', 'episodes',
                              '(library_episode_id IS NULL AND media_asset_id IS NULL) OR (library_episode_id IS NOT NULL AND media_asset_id IS NOT NULL)')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM library_titles')):
        raise RuntimeError('Content library is populated; downgrade would discard masters/grants and is intentionally refused. Restore the full pre-upgrade backup instead.')
    for name in ['fk_episode_library_asset','fk_episode_library_media_asset','fk_episode_library_episode']:
        op.drop_constraint(name, 'episodes', type_='foreignkey')
    op.drop_constraint('ck_episode_library_source_complete', 'episodes', type_='check')
    op.drop_column('episodes', 'media_asset_id')
    op.drop_column('episodes', 'library_episode_id')
    op.drop_constraint('fk_drama_distribution_site_source', 'dramas', type_='foreignkey')
    op.drop_constraint('fk_drama_distribution_grant', 'dramas', type_='foreignkey')
    op.drop_constraint('fk_drama_library_title', 'dramas', type_='foreignkey')
    op.drop_constraint('uq_drama_site_library_title_version', 'dramas', type_='unique')
    op.drop_constraint('ck_drama_distribution_complete', 'dramas', type_='check')
    for column in ['library_version','distribution_grant_id','library_title_id']:
        op.drop_column('dramas', column)
    op.drop_table('distribution_grants')
    op.drop_table('library_revisions')
    op.drop_constraint('fk_library_episode_current_asset', 'library_episodes', type_='foreignkey')
    op.drop_table('library_media_assets')
    op.drop_table('library_episodes')
    op.drop_table('library_titles')
