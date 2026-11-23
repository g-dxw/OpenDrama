"""Open new modules only for the explicitly bound JJTV business."""
from alembic import op
import sqlalchemy as sa
revision='a1114permissions'
down_revision='a1114support'
branch_labels=None
depends_on=None
KEYS=('login_settings','stripe','coin_packs','coins','attribution','support')
def upgrade():
    bind=op.get_bind()
    for key in KEYS:
        bind.execute(sa.text("INSERT INTO legacy_business_bindings(module_key,tenant_id) VALUES (:key,'jjtv') ON CONFLICT (module_key) DO NOTHING"),dict(key=key))
        bind.execute(sa.text("INSERT INTO tenant_modules(tenant_id,module_key,enabled) SELECT id,:key,id='jjtv' FROM tenants ON CONFLICT (tenant_id,module_key) DO NOTHING"),dict(key=key))
def downgrade():
    bind=op.get_bind()
    for key in KEYS:
        bind.execute(sa.text('DELETE FROM tenant_modules WHERE module_key=:key'),dict(key=key))
        bind.execute(sa.text('DELETE FROM legacy_business_bindings WHERE module_key=:key'),dict(key=key))
