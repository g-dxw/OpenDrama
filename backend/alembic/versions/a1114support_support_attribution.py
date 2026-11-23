"""Customer support and attribution transactional outbox."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "a1114support"
down_revision = "a1114commerce"
branch_labels = None
depends_on = None

def upgrade():
    # Configuration, ticket history and conversion outbox are isolated to JJTV.
    op.create_table('attribution_pixels',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('platform', sa.String(length=16), nullable=False),
    sa.Column('pixel_id', sa.String(length=100), nullable=False),
    sa.Column('encrypted_token', sa.Text(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('delivery_mode', sa.String(length=16), nullable=False),
    sa.Column('test_event_code', sa.String(length=100), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("platform IN ('meta','tiktok')", name='ck_attr_pixel_platform'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'platform', 'pixel_id', name='uq_attr_pixel_identity')
    )
    op.create_table('support_config',
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('assignment_cursor', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('tenant_id')
    )
    op.create_table('attribution_sources',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('account_id', sa.String(length=120), nullable=False),
    sa.Column('pixel_id', sa.BigInteger(), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['pixel_id'], ['attribution_pixels.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('support_tickets',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('category', sa.String(length=64), nullable=False),
    sa.Column('priority', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('order_no', sa.String(length=64), nullable=True),
    sa.Column('fields', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('assignee_id', sa.BigInteger(), nullable=True),
    sa.Column('team_code', sa.String(length=64), nullable=False),
    sa.Column('merged_into_id', sa.BigInteger(), nullable=True),
    sa.Column('sla_policy', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('paused_minutes', sa.Float(), nullable=False),
    sa.Column('waiting_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_response_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_admin_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_user_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('csat_score', sa.Integer(), nullable=True),
    sa.Column('csat_comment', sa.String(length=1000), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("priority IN ('low','normal','high','urgent')", name='ck_support_priority'),
    sa.CheckConstraint("status IN ('open','in_progress','waiting_customer','resolved','closed','merged')", name='ck_support_status'),
    sa.CheckConstraint('csat_score IS NULL OR csat_score BETWEEN 1 AND 5', name='ck_support_csat'),
    sa.ForeignKeyConstraint(['assignee_id'], ['admin_users.id'], ),
    sa.ForeignKeyConstraint(['merged_into_id'], ['support_tickets.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )
    op.create_index('ix_support_tenant_status_updated', 'support_tickets', ['tenant_id', 'status', 'updated_at'], unique=False)
    op.create_index('ix_support_user', 'support_tickets', ['user_id', 'created_at'], unique=False)
    op.create_table('attribution_plans',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('pid', sa.String(length=48), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('source_id', sa.BigInteger(), nullable=False),
    sa.Column('pixel_id', sa.BigInteger(), nullable=False),
    sa.Column('landing_path', sa.String(length=500), nullable=False),
    sa.Column('region', sa.String(length=64), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['pixel_id'], ['attribution_pixels.id'], ),
    sa.ForeignKeyConstraint(['source_id'], ['attribution_sources.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('pid')
    )
    op.create_table('support_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('actor_id', sa.BigInteger(), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['ticket_id'], ['support_tickets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_support_events_ticket', 'support_events', ['ticket_id', 'created_at'], unique=False)
    op.create_table('support_messages',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('author_type', sa.String(length=16), nullable=False),
    sa.Column('author_id', sa.BigInteger(), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('internal', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['support_tickets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_support_messages_ticket', 'support_messages', ['ticket_id', 'created_at'], unique=False)
    op.create_table('attribution_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('plan_id', sa.BigInteger(), nullable=True),
    sa.Column('pixel_id', sa.BigInteger(), nullable=False),
    sa.Column('event_id', sa.String(length=160), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=True),
    sa.Column('order_no', sa.String(length=64), nullable=True),
    sa.Column('amount_cents', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('test', sa.Boolean(), nullable=False),
    sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_error', sa.String(length=2000), nullable=False),
    sa.Column('response', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['pixel_id'], ['attribution_pixels.id'], ),
    sa.ForeignKeyConstraint(['plan_id'], ['attribution_plans.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('pixel_id', 'event_id', name='uq_attr_event_dedupe')
    )
    op.create_index('ix_attr_event_plan_created', 'attribution_events', ['plan_id', 'created_at'], unique=False)
    op.create_index('ix_attr_delivery_due', 'attribution_events', ['status', 'next_attempt_at'], unique=False)
    op.create_table('attribution_visits',
    sa.Column('id', sa.String(length=48), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('plan_id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.String(length=100), nullable=False),
    sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['attribution_plans.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_attr_visit_plan_created', 'attribution_visits', ['plan_id', 'created_at'], unique=False)
    op.create_table('user_attribution',
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('plan_id', sa.BigInteger(), nullable=False),
    sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['attribution_plans.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id')
    )


def downgrade():
    op.drop_table("user_attribution")
    op.drop_table("attribution_visits")
    op.drop_table("attribution_events")
    op.drop_table("support_messages")
    op.drop_table("support_events")
    op.drop_table("attribution_plans")
    op.drop_table("support_tickets")
    op.drop_table("attribution_sources")
    op.drop_table("support_config")
    op.drop_table("attribution_pixels")
