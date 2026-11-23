"""Persist authenticated Pancake payment and period evidence independently."""

from alembic import op
import sqlalchemy as sa


revision = "b6d7aef19042"
down_revision = "cc24ef9ab731"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("subscriptions", sa.Column("pancake_subscription_id", sa.String(100)))
    op.create_index(
        "uq_subscriptions_pancake_account_id", "subscriptions", ["account_code", "pancake_subscription_id"],
        unique=True, postgresql_where=sa.text("pancake_subscription_id IS NOT NULL"),
    )
    op.create_table(
        "pancake_period_evidence",
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("initial_order_id", sa.BigInteger(), sa.ForeignKey("orders.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("period_number", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.String(100)),
        sa.Column("payment_key", sa.String(128), unique=True),
        sa.Column("period_start", sa.DateTime(timezone=True)),
        sa.Column("period_end", sa.DateTime(timezone=True)),
        sa.Column("settled_order_id", sa.BigInteger(), sa.ForeignKey("orders.id", ondelete="RESTRICT"), unique=True),
        sa.CheckConstraint("period_number > 0", name="ck_pancake_evidence_positive_period"),
        sa.CheckConstraint("(payment_id IS NULL) = (payment_key IS NULL)", name="ck_pancake_evidence_payment_pair"),
        sa.CheckConstraint(
            "(period_start IS NULL AND period_end IS NULL) OR "
            "(period_start IS NOT NULL AND period_end IS NOT NULL AND period_end > period_start)",
            name="ck_pancake_evidence_valid_window",
        ),
    )
    op.create_index("ix_pancake_period_evidence_tenant_id", "pancake_period_evidence", ["tenant_id"])


def downgrade():
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM pancake_period_evidence) OR "
        "EXISTS (SELECT 1 FROM subscriptions WHERE pancake_subscription_id IS NOT NULL)"
    )):
        raise RuntimeError("Cannot downgrade a database with Waffo Pancake subscriptions or payment evidence")
    op.drop_index("ix_pancake_period_evidence_tenant_id", table_name="pancake_period_evidence")
    op.drop_table("pancake_period_evidence")
    op.drop_index("uq_subscriptions_pancake_account_id", table_name="subscriptions")
    op.drop_column("subscriptions", "pancake_subscription_id")
