"""Immutable product versions, merchant bindings and refundable sources."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='a1114commerce'
down_revision='a1114content'
branch_labels=None
depends_on=None

def upgrade():
    for name in ('base_coins','bonus_coins'):op.add_column('plan_versions',sa.Column(name,sa.Integer(),nullable=False,server_default='0'))
    op.add_column('subscriptions',sa.Column('account_code',sa.String(32),nullable=False,server_default='env'))
    op.add_column('subscriptions',sa.Column('managed_commerce',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.alter_column('orders','plan_version_id',existing_type=sa.BigInteger(),nullable=True)
    for column in [sa.Column('product_type',sa.String(16),nullable=False,server_default='membership'),sa.Column('product_snapshot',postgresql.JSONB(),nullable=False,server_default='{}'),sa.Column('account_code',sa.String(32),nullable=False,server_default='env'),sa.Column('checkout_url',sa.String(2000)),sa.Column('checkout_key',sa.String(100)),sa.Column('payment_intent_id',sa.String(100)),sa.Column('invoice_id',sa.String(100)),sa.Column('subscription_ref',sa.String(100)),sa.Column('attribution_pid',sa.String(100))]:op.add_column('orders',column)
    op.create_unique_constraint('uq_orders_checkout_key','orders',['checkout_key'])
    op.create_unique_constraint('uq_orders_invoice_id','orders',['invoice_id'])
    op.create_table('coin_packs',sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('code',sa.String(32),nullable=False,unique=True),sa.Column('name_en',sa.String(64),nullable=False),sa.Column('name_zh',sa.String(64),nullable=False),sa.Column('status',sa.String(16),nullable=False),sa.Column('sort_order',sa.Integer(),nullable=False),sa.Column('badge',sa.String(64),nullable=False))
    op.create_table('coin_pack_versions',sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('pack_id',sa.BigInteger(),sa.ForeignKey('coin_packs.id'),nullable=False),sa.Column('version',sa.Integer(),nullable=False),sa.Column('price_cents',sa.Integer(),nullable=False),sa.Column('currency',sa.String(8),nullable=False),sa.Column('base_coins',sa.Integer(),nullable=False),sa.Column('bonus_coins',sa.Integer(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.UniqueConstraint('pack_id','version'),sa.CheckConstraint('price_cents >= 50 AND base_coins > 0 AND bonus_coins >= 0',name='ck_pack_values'))
    op.create_table('stripe_accounts',sa.Column('code',sa.String(32),primary_key=True),sa.Column('name',sa.String(100),nullable=False),sa.Column('status',sa.String(16),nullable=False),sa.Column('is_default',sa.Boolean(),nullable=False),sa.Column('remote_id',sa.String(100)),sa.Column('livemode',sa.Boolean(),nullable=False))
    op.create_table('stripe_price_bindings',sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('account_code',sa.String(32),nullable=False),sa.Column('product_type',sa.String(16),nullable=False),sa.Column('version_id',sa.BigInteger(),nullable=False),sa.Column('billing_mode',sa.String(16),nullable=False),sa.Column('price_id',sa.String(100),nullable=False),sa.UniqueConstraint('account_code','product_type','version_id','billing_mode'))
    op.create_table('coin_sources',sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('user_id',sa.BigInteger(),sa.ForeignKey('users.id'),nullable=False),sa.Column('order_id',sa.BigInteger(),sa.ForeignKey('orders.id'),unique=True),sa.Column('original',sa.Integer(),nullable=False),sa.Column('remaining',sa.Integer(),nullable=False),sa.Column('reserved',sa.Integer(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.CheckConstraint('original >= 0 AND remaining >= 0 AND reserved >= 0 AND remaining + reserved <= original',name='ck_coin_source_bounds'))
    op.create_index('ix_coin_sources_user_id','coin_sources',['user_id'])
    op.create_table('coin_allocations',sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('source_id',sa.BigInteger(),sa.ForeignKey('coin_sources.id'),nullable=False),sa.Column('ledger_id',sa.BigInteger(),sa.ForeignKey('coin_ledger.id'),nullable=False),sa.Column('amount',sa.Integer(),nullable=False),sa.UniqueConstraint('source_id','ledger_id'),sa.CheckConstraint('amount > 0',name='ck_coin_allocation_positive'))
    op.create_table('membership_grants',sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('order_id',sa.BigInteger(),sa.ForeignKey('orders.id'),nullable=False,unique=True),sa.Column('user_id',sa.BigInteger(),sa.ForeignKey('users.id'),nullable=False),sa.Column('starts_at',sa.DateTime(timezone=True),nullable=False),sa.Column('ends_at',sa.DateTime(timezone=True),nullable=False),sa.Column('status',sa.String(24),nullable=False),sa.Column('access_consumed',sa.Boolean(),nullable=False))
    op.create_index('ix_membership_grants_user_id','membership_grants',['user_id'])
    op.create_table('commerce_settlements',sa.Column('key',sa.String(200),primary_key=True),sa.Column('order_id',sa.BigInteger(),sa.ForeignKey('orders.id'),nullable=False,unique=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_table('commerce_refunds',sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('order_id',sa.BigInteger(),sa.ForeignKey('orders.id'),nullable=False,unique=True),sa.Column('idempotency_key',sa.String(100),nullable=False,unique=True),sa.Column('status',sa.String(24),nullable=False),sa.Column('refund_id',sa.String(100),unique=True),sa.Column('reason',sa.String(500),nullable=False),sa.Column('error',sa.Text(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    # Historical balances have unknown source consumption and stay nonrefundable.
    op.execute("INSERT INTO coin_sources(user_id,original,remaining,reserved) SELECT user_id,balance,balance,0 FROM wallet_accounts WHERE balance > 0")
    # Keep the original environment merchant explicit without importing secrets.
    op.execute("INSERT INTO stripe_accounts(code,name,status,is_default,livemode) VALUES ('env','Original Stripe account','active',true,false)")

def downgrade():
    for name in ('commerce_refunds','commerce_settlements','membership_grants','coin_allocations','coin_sources','stripe_price_bindings','stripe_accounts','coin_pack_versions','coin_packs'):op.drop_table(name)
    for name in ('attribution_pid','subscription_ref','invoice_id','payment_intent_id','checkout_key','checkout_url','account_code','product_snapshot','product_type'):op.drop_column('orders',name)
    # Do not silently destroy paid coin orders during a schema rollback.
    op.execute("DELETE FROM orders WHERE plan_version_id IS NULL AND status IN ('created','failed')")
    op.alter_column('orders','plan_version_id',existing_type=sa.BigInteger(),nullable=False)
    op.drop_column('subscriptions','managed_commerce');op.drop_column('subscriptions','account_code')
    op.drop_column('plan_versions','bonus_coins');op.drop_column('plan_versions','base_coins')
