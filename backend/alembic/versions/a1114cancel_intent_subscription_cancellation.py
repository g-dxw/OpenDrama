"""Persist one cancellation intent per managed subscription cancel/resume cycle."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a1114cancel_intent"
down_revision = "a1114lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("subscriptions", sa.Column("cancellation_context", postgresql.JSONB(),
        nullable=False, server_default="{}"))


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Cannot downgrade a1114cancel_intent offline: cancellation preflight requires a database connection")
    op.execute("LOCK TABLE subscriptions IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM subscriptions WHERE cancellation_context <> '{}'::jsonb)")):
        raise RuntimeError("Cannot discard durable subscription cancellation intents; reconcile before downgrade")
    op.drop_column("subscriptions", "cancellation_context")
