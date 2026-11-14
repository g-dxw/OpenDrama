"""Add installation-owned site configuration and versioned brands."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a7c91e0b4d20"
down_revision = "a1114cancel_intent"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("site_settings",
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("target_markets", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("default_locale", sa.String(16), nullable=False, server_default="en"),
        sa.Column("locales", postgresql.JSONB(), nullable=False, server_default='["en","zh"]'),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("web_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("h5_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("features", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_table("site_brands",
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("draft", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("published", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("draft_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_table("site_brand_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "version", name="uq_site_brand_revision"))
    op.create_table("site_domain_verifications",
        sa.Column("domain_id", sa.String(64), sa.ForeignKey("tenant_domains.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("token", sa.String(100), nullable=False))
    bind = op.get_bind()
    bind.execute(sa.text("INSERT INTO site_settings(tenant_id,timezone) SELECT id,CASE WHEN id='jjtv' THEN 'America/Los_Angeles' ELSE 'UTC' END FROM tenants"))
    bind.execute(sa.text("""INSERT INTO site_brands(tenant_id,draft,published)
        SELECT id, jsonb_build_object('name',CASE WHEN id='jjtv' THEN 'JJTV' ELSE name END,'logo_url','','favicon_url','','primary_color','#ff3347',
        'background_color','#07070a','font_family','Inter','button_radius',12,'customer_email','',
        'social_links','{}'::jsonb,'seo_title',CASE WHEN id='jjtv' THEN 'JJTV' ELSE name END,'seo_description',''),
        jsonb_build_object('name',name,'logo_url','','favicon_url','','primary_color','#ff3347',
        'background_color','#07070a','font_family','Inter','button_radius',12,'customer_email','',
        'social_links','{}'::jsonb,'seo_title',name,'seo_description','') FROM tenants"""))
    bind.execute(sa.text("INSERT INTO site_brand_revisions(tenant_id,version,settings) SELECT tenant_id,version,published FROM site_brands"))


def downgrade():
    raise RuntimeError("Site configuration rollback requires a verified database backup")
