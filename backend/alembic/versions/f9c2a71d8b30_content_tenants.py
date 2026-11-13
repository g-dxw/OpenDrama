"""Tenant directory, native content ownership and encrypted India connections."""
import sqlalchemy as sa
from alembic import op
revision = "f9c2a71d8b30"
down_revision = "a3c9f1e28b70"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("tenants", sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False), sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('native','india_legacy')", name="ck_tenants_kind"),
        sa.CheckConstraint("status IN ('active','paused')", name="ck_tenants_status"),
        sa.CheckConstraint("kind <> 'india_legacy' OR id = 'india-short-drama'", name="ck_tenants_india_fixed"))
    op.execute("INSERT INTO tenants(id,name,kind,status) VALUES ('jjtv','JJTV 自营','native','active'),('india-short-drama','India Short Drama','india_legacy','active')")
    op.create_table("tenant_members",
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.CheckConstraint("role IN ('admin','ops','finance')", name="ck_tenant_members_role"))
    op.execute("INSERT INTO tenant_members(tenant_id,user_id,role) SELECT 'jjtv',id,role FROM admin_users WHERE role IN ('admin','ops','finance')")
    op.create_table("india_connections",
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("session_key", sa.String(64), primary_key=True), sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    for table in ("dramas", "tags", "home_groups"):
        op.add_column(table, sa.Column("tenant_id", sa.String(64), nullable=False, server_default="jjtv"))
        op.create_foreign_key("fk_"+table+"_tenant", table, "tenants", ["tenant_id"], ["id"])
        op.create_index("ix_"+table+"_tenant_id", table, ["tenant_id"])
        op.create_check_constraint("ck_"+table+"_native_tenant", table, "tenant_id <> 'india-short-drama'")
    op.drop_index("uq_dramas_title_self", table_name="dramas")
    op.drop_index("uq_dramas_title_supplier", table_name="dramas")
    op.create_index("uq_dramas_title_self", "dramas", ["tenant_id","title"], unique=True, postgresql_where=sa.text("supplier_id IS NULL"))
    op.create_index("uq_dramas_title_supplier", "dramas", ["tenant_id","supplier_id","title"], unique=True, postgresql_where=sa.text("supplier_id IS NOT NULL"))
    op.drop_constraint("uq_tags_name_en", "tags", type_="unique")
    op.create_unique_constraint("uq_tags_tenant_name_en", "tags", ["tenant_id","name_en"])

def downgrade():
    bind = op.get_bind()
    for table in ("dramas", "tags", "home_groups"):
        if bind.execute(sa.text("SELECT 1 FROM "+table+" WHERE tenant_id <> 'jjtv' LIMIT 1")).first():
            raise RuntimeError("Refusing to erase content tenant ownership; export/migrate other native tenants first")
    op.drop_constraint("uq_tags_tenant_name_en", "tags", type_="unique")
    op.create_unique_constraint("uq_tags_name_en", "tags", ["name_en"])
    op.drop_index("uq_dramas_title_self", table_name="dramas")
    op.drop_index("uq_dramas_title_supplier", table_name="dramas")
    op.create_index("uq_dramas_title_self", "dramas", ["title"], unique=True, postgresql_where=sa.text("supplier_id IS NULL"))
    op.create_index("uq_dramas_title_supplier", "dramas", ["supplier_id","title"], unique=True, postgresql_where=sa.text("supplier_id IS NOT NULL"))
    for table in ("dramas", "tags", "home_groups"):
        op.drop_constraint("ck_"+table+"_native_tenant", table, type_="check")
        op.drop_index("ix_"+table+"_tenant_id", table_name=table)
        op.drop_constraint("fk_"+table+"_tenant", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")
    op.drop_table("india_connections")
    op.drop_table("tenant_members")
    op.drop_table("tenants")
