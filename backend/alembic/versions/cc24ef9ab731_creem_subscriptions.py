"""Bind Creem recurring subscriptions to their original merchant."""

from alembic import op
import sqlalchemy as sa


revision = "cc24ef9ab731"
down_revision = "d516e603d326"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("subscriptions", sa.Column("creem_subscription_id", sa.String(100)))
    op.create_index(
        "uq_subscriptions_creem_account_id", "subscriptions", ["account_code", "creem_subscription_id"],
        unique=True, postgresql_where=sa.text("creem_subscription_id IS NOT NULL"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM subscriptions WHERE creem_subscription_id IS NOT NULL)")):
        raise RuntimeError("Cannot downgrade a database with Creem subscriptions")
    op.drop_index("uq_subscriptions_creem_account_id", table_name="subscriptions")
    op.drop_column("subscriptions", "creem_subscription_id")
