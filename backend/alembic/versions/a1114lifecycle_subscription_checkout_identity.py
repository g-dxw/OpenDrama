"""Retain pre-invoice lifecycle facts and frozen Checkout request context."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='a1114lifecycle'
down_revision='a1114permissions'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('orders',sa.Column('checkout_context',postgresql.JSONB(),nullable=False,server_default='{}'))
    op.create_table('stripe_subscription_lifecycle',
        sa.Column('account_code',sa.String(32),primary_key=True),
        sa.Column('subscription_id',sa.String(100),primary_key=True),
        sa.Column('order_id',sa.BigInteger(),sa.ForeignKey('orders.id'),nullable=False),
        sa.Column('user_id',sa.BigInteger(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('deleted',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column('cancel_at_period_end',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column('canceled_at',sa.DateTime(timezone=True)),
        sa.Column('last_event_created',sa.BigInteger(),nullable=False,server_default='0'),
        sa.Column('last_event_id',sa.String(100),nullable=False,server_default=''))

def downgrade():
    op.drop_table('stripe_subscription_lifecycle')
    op.drop_column('orders','checkout_context')
