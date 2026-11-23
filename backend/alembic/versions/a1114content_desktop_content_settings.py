"""Additive desktop content pricing, subtitles and encrypted JJTV settings."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='a1114content'
down_revision='e6a1c3d9b742'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('dramas',sa.Column('language',sa.String(16),nullable=False,server_default=''))
    op.add_column('dramas',sa.Column('access_mode',sa.String(16),nullable=False,server_default='public'))
    op.add_column('dramas',sa.Column('member_pays_coins',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.add_column('dramas',sa.Column('non_member_free_episodes',sa.Integer(),nullable=True))
    op.add_column('episodes',sa.Column('coin_price',sa.Integer(),nullable=True))
    op.create_check_constraint('ck_drama_access_mode','dramas',"access_mode IN ('public','members')")
    op.create_check_constraint('ck_drama_nonmember_free','dramas','non_member_free_episodes IS NULL OR non_member_free_episodes >= 0')
    op.create_check_constraint('ck_episode_coin_price','episodes','coin_price IS NULL OR coin_price >= 0')
    op.create_table('episode_subtitles',sa.Column('episode_id',sa.BigInteger(),sa.ForeignKey('episodes.id',ondelete='CASCADE'),primary_key=True),sa.Column('language',sa.String(35),primary_key=True),sa.Column('label',sa.String(80),nullable=False),sa.Column('url',sa.String(500),nullable=False),sa.Column('is_default',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.create_index('uq_episode_default_subtitle','episode_subtitles',['episode_id'],unique=True,postgresql_where=sa.text('is_default'))
    op.execute("INSERT INTO episode_subtitles(episode_id,language,label,url,is_default) SELECT id,'und','Default',subtitle_url,true FROM episodes WHERE subtitle_url <> ''")
    op.create_table('managed_settings',sa.Column('namespace',sa.String(80),primary_key=True),sa.Column('tenant_id',sa.String(64),sa.ForeignKey('tenants.id'),nullable=False,server_default='jjtv'),sa.Column('values',postgresql.JSONB(),nullable=False),sa.Column('secrets',postgresql.JSONB(),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))

def downgrade():
    op.drop_table('managed_settings')
    op.drop_table('episode_subtitles')
    op.drop_constraint('ck_episode_coin_price','episodes')
    op.drop_column('episodes','coin_price')
    op.drop_constraint('ck_drama_nonmember_free','dramas')
    op.drop_constraint('ck_drama_access_mode','dramas')
    for column in ('non_member_free_episodes','member_pays_coins','access_mode','language'): op.drop_column('dramas',column)
