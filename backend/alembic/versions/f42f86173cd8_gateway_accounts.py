"""Add payment gateway merchants, site bindings, and durable order provider identity."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = 'f42f86173cd8'
down_revision = 'c9e13a2d6f42'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('orders', sa.Column('provider', sa.String(32), nullable=False, server_default='stripe'))
    op.create_table(
        'gateway_accounts',
        sa.Column('provider', sa.String(32), primary_key=True),
        sa.Column('code', sa.String(32), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('identity', sa.String(64), nullable=False),
        sa.Column('config', JSONB, nullable=False),
        sa.Column('credentials', JSONB, nullable=False),
        sa.UniqueConstraint('provider', 'identity', name='uq_gateway_provider_identity'),
        sa.CheckConstraint("status IN ('draft', 'active', 'disabled')", name='ck_gateway_account_status'),
    )
    op.create_table(
        'site_gateway_bindings',
        sa.Column('tenant_id', sa.String(64), sa.ForeignKey('tenants.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('provider', sa.String(32), primary_key=True),
        sa.Column('account_code', sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(['provider', 'account_code'], ['gateway_accounts.provider', 'gateway_accounts.code'], ondelete='RESTRICT'),
        sa.UniqueConstraint('provider', 'account_code', name='uq_site_gateway_merchant'),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM orders WHERE provider <> 'stripe')")):
        raise RuntimeError('Cannot downgrade a database with orders from additional payment gateways')
    op.drop_table('site_gateway_bindings')
    op.drop_table('gateway_accounts')
    op.drop_column('orders', 'provider')
