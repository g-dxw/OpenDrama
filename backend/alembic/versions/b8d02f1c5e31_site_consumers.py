"""Independent viewer accounts, commercial state and site-owned read models.

Legacy records keep their IDs and are assigned to JJTV. New relations enforce
site ownership in PostgreSQL as well as the request/session layer.
"""
from alembic import op
import os
import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
revision='b8d02f1c5e31'
down_revision='a7c91e0b4d20'
branch_labels=None
depends_on=None
TABLES='users third_party_identities verification_codes plans plan_versions subscriptions orders billing_settings coin_packs coin_pack_versions stripe_price_bindings coin_sources coin_allocations membership_grants commerce_settlements commerce_refunds stripe_subscription_lifecycle wallet_accounts coin_ledger entitlements playback_events playback_progress daily_metrics reactions comments inbox_messages push_records hot_searches copy_entries marketing_stats testimonials store_links legal_pages reward_configs checkins ad_claims task_claims moderation_settings support_config support_tickets support_messages support_events attribution_pixels attribution_sources attribution_plans attribution_visits user_attribution attribution_events episodes'.split()
UNIQUES={'users':[('email',)],'third_party_identities':[('provider','provider_user_id')],'plans':[('code',)],'coin_packs':[('code',)],'copy_entries':[('key',)],'store_links':[('platform',)],'daily_metrics':[('business_day',)],'reward_configs':[('code','effective_on')]}


def _legacy_secret_configured(bind, code):
    """Freeze the old managed credential overlay without importing runtime ORM."""
    value = os.environ.get('STRIPE_SECRET_KEY', '').strip() if code == 'env' else ''
    row = bind.execute(sa.text(
        'SELECT tenant_id,"values",secrets FROM managed_settings WHERE namespace=:namespace'
    ), {'namespace': 'stripe:' + code}).mappings().first()
    if row is None:
        return bool(value)
    if row['tenant_id'] != 'jjtv':
        raise RuntimeError('Cannot preserve a Stripe configuration owned by another site')
    value = (row['values'] or {}).get('secret_key', value)
    secrets = row['secrets'] or {}
    if 'secret_key' in secrets:
        encrypted = secrets['secret_key']
        if encrypted is None:
            value = ''  # An explicit clear overrides the environment fallback.
        else:
            try:
                key = os.environ.get('SETTINGS_ENCRYPTION_KEY', '').strip().encode()
                value = Fernet(key).decrypt(encrypted.encode()).decode()
            except (InvalidToken, ValueError, TypeError, AttributeError, UnicodeError):
                raise RuntimeError('Cannot verify the existing Stripe merchant configuration') from None
    return bool(value)


def _legacy_purchase_merchant(bind):
    # Old checkout considered only active, configured accounts, preferred their
    # default, and otherwise used the first enabled account ordered by code.
    rows = [dict(row) for row in bind.execute(sa.text(
        'SELECT code,status,is_default FROM stripe_accounts ORDER BY code'
    )).mappings()]
    if not any(row['code'] == 'env' for row in rows) and os.environ.get('STRIPE_SECRET_KEY', '').strip():
        rows.insert(0, dict(code='env', status='active', is_default=not any(row['is_default'] for row in rows)))
    enabled = [row for row in rows if _legacy_secret_configured(bind, row['code']) and row['status'] == 'active']
    chosen = next((row for row in enabled if row['is_default']), enabled[0] if enabled else None)
    # Unconfigured installations retain the compatibility placeholder; they
    # remain unable to collect money until credentials are explicitly added.
    return chosen['code'] if chosen else 'env'

def upgrade():
    bind=op.get_bind();inspector=sa.inspect(bind)
    for table in TABLES:
        columns={c['name'] for c in inspector.get_columns(table)}
        if 'tenant_id' not in columns:
            op.add_column(table,sa.Column('tenant_id',sa.String(64),nullable=False,server_default='jjtv'))
            op.create_foreign_key('fk_site_owner_'+table,table,'tenants',['tenant_id'],['id'],ondelete='RESTRICT')
            op.create_index('ix_'+table+'_tenant_id',table,['tenant_id'])
    bind.execute(sa.text('UPDATE episodes e SET tenant_id=d.tenant_id FROM dramas d WHERE e.drama_id=d.id'))
    # The existing data was JJTV-only; existing support/attribution tables
    # already carry ownership. Refuse inconsistent historical references.
    inspector=sa.inspect(bind)
    for table in TABLES:
        pk=inspector.get_pk_constraint(table)['constrained_columns']
        if table=='legal_pages':
            op.drop_constraint(inspector.get_pk_constraint(table)['name'],table,type_='primary')
            op.create_primary_key('pk_legal_pages_site',table,['slug','tenant_id'])
        elif 'tenant_id' not in pk:
            op.create_unique_constraint(('uq_site_'+table+'_identity')[:63],table,['tenant_id']+pk)
        for cols in UNIQUES.get(table,[]):
            for constraint in inspector.get_unique_constraints(table):
                if tuple(constraint['column_names'])==cols:op.drop_constraint(constraint['name'],table,type_='unique')
            op.create_unique_constraint(('uq_site_'+table+'_'+'_'.join(cols))[:63],table,['tenant_id']+list(cols))
    inspector=sa.inspect(bind)
    for table in TABLES:
        seen=set()
        for fk in inspector.get_foreign_keys(table):
            target=fk['referred_table'];local=fk['constrained_columns'];remote=fk['referred_columns']
            if target not in TABLES+['dramas'] or len(local)!=1 or local==['tenant_id']:continue
            target_pk=inspector.get_pk_constraint(target)['constrained_columns']
            if len(target_pk)!=1 or remote!=target_pk:continue
            key=(local[0],target)
            if key in seen:continue
            seen.add(key)
            op.create_foreign_key(('fk_site_'+table+'_'+local[0])[:63],table,target,['tenant_id']+local,['tenant_id']+remote)
    for table in ('billing_settings','moderation_settings'):
        col=next(c for c in inspector.get_columns(table) if c['name']=='id')
        if not col.get('default') and not col.get('identity'):
            start=bind.execute(sa.text('SELECT COALESCE(MAX(id),0)+1 FROM '+table)).scalar_one()
            op.execute('ALTER TABLE '+table+' ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (START WITH '+str(start)+')')
    op.create_table('site_merchant_bindings',sa.Column('tenant_id',sa.String(64),sa.ForeignKey('tenants.id',ondelete='RESTRICT'),primary_key=True),sa.Column('account_code',sa.String(32),nullable=False),sa.Column('explicit_reuse',sa.Boolean(),nullable=False,server_default=sa.false()),sa.Column('updated_by',sa.BigInteger(),nullable=True),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_table('stripe_webhook_receipts',sa.Column('account_code',sa.String(32),primary_key=True),sa.Column('event_id',sa.String(100),primary_key=True),sa.Column('tenant_id',sa.String(64),sa.ForeignKey('tenants.id'),nullable=False),sa.Column('payload_hash',sa.String(64),nullable=False),sa.Column('processed',sa.Boolean(),nullable=False,server_default=sa.false()),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    bind.execute(sa.text("INSERT INTO site_merchant_bindings(tenant_id,account_code,explicit_reuse) SELECT 'jjtv',:account,false WHERE EXISTS (SELECT 1 FROM tenants WHERE id='jjtv')"), {'account': _legacy_purchase_merchant(bind)})

def downgrade():
    raise RuntimeError('Multi-site consumer identities and payments cannot be merged safely; restore a compatible full backup')
