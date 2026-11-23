"""Tenant-owned homepage channels/banners and stable default item identities.

Existing IDs and configuration values are retained. Legacy global home
configuration is assigned only to its explicitly registered native owner.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e6a1c3d9b742'
down_revision = 'c4d8e0a1b295'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(sa.text('LOCK TABLE tenants,legacy_business_bindings,channel_tabs,banners,home_groups,dramas,tags IN ACCESS EXCLUSIVE MODE'))
    owner = bind.execute(sa.text("SELECT t.id FROM legacy_business_bindings b JOIN tenants t ON t.id=b.tenant_id WHERE b.module_key='home_ops' AND t.kind='native'")).scalar_one_or_none()
    if owner is None:
        raise RuntimeError('Tenant home migration requires an explicit native home_ops business owner')
    foreign_groups = bind.scalar(sa.text('SELECT count(*) FROM channel_tabs t JOIN home_groups g ON g.id=t.group_id WHERE g.tenant_id<>:owner'),dict(owner=owner))
    foreign_dramas = bind.scalar(sa.text('SELECT count(*) FROM banners b JOIN dramas d ON d.id=b.drama_id WHERE d.tenant_id<>:owner'),dict(owner=owner))
    if foreign_groups or foreign_dramas:
        raise RuntimeError('Legacy homepage references content outside its registered owner; reconcile ownership before migration')

    for table in ('tags','home_groups'):
        op.add_column(table,sa.Column('default_key',sa.String(64),nullable=True))
        op.create_unique_constraint('uq_'+table+'_tenant_default_key',table,['tenant_id','default_key'])
    for table in ('tags','home_groups','dramas'):
        op.create_unique_constraint('uq_'+table+'_tenant_id',table,['tenant_id','id'])

    for table in ('channel_tabs','banners'):
        op.add_column(table,sa.Column('tenant_id',sa.String(64),nullable=False,server_default='jjtv'))
        bind.execute(sa.text('UPDATE '+table+' SET tenant_id=:owner'),dict(owner=owner))
        op.create_foreign_key('fk_'+table+'_tenant',table,'tenants',['tenant_id'],['id'],ondelete='RESTRICT')
        op.create_index('ix_'+table+'_tenant',table,['tenant_id'])
    for constraint in sa.inspect(bind).get_unique_constraints('channel_tabs'):
        if constraint['column_names']==['code']:
            op.drop_constraint(constraint['name'],'channel_tabs',type_='unique')
    op.create_unique_constraint('uq_channel_tabs_tenant_code','channel_tabs',['tenant_id','code'])
    op.create_foreign_key('fk_channel_tabs_tenant_group','channel_tabs','home_groups',['tenant_id','group_id'],['tenant_id','id'])
    op.create_foreign_key('fk_banners_tenant_drama','banners','dramas',['tenant_id','drama_id'],['tenant_id','id'])


def downgrade():
    raise RuntimeError('Tenant homepage configuration and default identities cannot be recombined into globally shared storage; restore a compatible backup')
