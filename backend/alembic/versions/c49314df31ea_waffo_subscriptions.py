"""Add a source binding for Waffo enterprise subscriptions."""

from alembic import op
import sqlalchemy as sa


revision = "c49314df31ea"
down_revision = "b6d7aef19042"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("subscriptions", sa.Column("waffo_subscription_id", sa.String(100)))
    op.create_index(
        "uq_subscriptions_waffo_account_id", "subscriptions", ["account_code", "waffo_subscription_id"],
        unique=True, postgresql_where=sa.text("waffo_subscription_id IS NOT NULL"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM subscriptions WHERE waffo_subscription_id IS NOT NULL)"
    )):
        raise RuntimeError("Cannot downgrade a database with Waffo subscriptions")
    op.drop_index("uq_subscriptions_waffo_account_id", table_name="subscriptions")
    op.drop_column("subscriptions", "waffo_subscription_id")
