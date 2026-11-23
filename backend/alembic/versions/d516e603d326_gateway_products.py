"""Map provider catalog items to tenant-owned product versions."""

import sqlalchemy as sa
from alembic import op

revision = 'd516e603d326'
down_revision = 'f42f86173cd8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'gateway_product_bindings',
        sa.Column('tenant_id', sa.String(64), sa.ForeignKey('tenants.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('provider', sa.String(32), primary_key=True),
        sa.Column('account_code', sa.String(32), primary_key=True),
        sa.Column('product_type', sa.String(16), primary_key=True),
        sa.Column('version_id', sa.BigInteger(), primary_key=True),
        sa.Column('billing_mode', sa.String(16), primary_key=True),
        sa.Column('external_product_id', sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(['provider', 'account_code'], ['gateway_accounts.provider', 'gateway_accounts.code'], ondelete='RESTRICT'),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM orders WHERE provider = 'creem')")):
        raise RuntimeError('Cannot remove the catalog for a database with Creem orders')
    op.drop_table('gateway_product_bindings')
