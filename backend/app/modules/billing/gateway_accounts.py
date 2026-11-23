"""Merchant ownership and credentials for additional payment gateways."""

import os

from sqlalchemy import select, text

from app.modules.billing.commerce_models import GatewayAccount, SiteGatewayBinding
from app.modules.billing.service import BillingError
from app.modules.tenancy.consumer_scope import current_site
from app.modules.tenancy.models import Tenant


def lock_gateways(db, provider):
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:provider))"), {"provider": "gateway_accounts:" + provider})


def native_site(db):
    site = current_site(db)
    tenant = db.get(Tenant, site)
    if tenant is None or tenant.kind != "native" or tenant.status != "active":
        raise BillingError(404, "Payment configuration is available on active native sites")
    return site


def site_binding(db, provider):
    return db.get(SiteGatewayBinding, (current_site(db), provider))


def bound_account(db, provider):
    binding = site_binding(db, provider)
    return db.get(GatewayAccount, (provider, binding.account_code)) if binding else None


def accounts(db, provider):
    return db.scalars(select(GatewayAccount).where(GatewayAccount.provider == provider).order_by(GatewayAccount.code))


def notify_url(provider, code):
    base = os.environ.get("PAYMENT_WEBHOOK_BASE_URL", "https://api.example.com").rstrip("/")
    return f"{base}/v1/billing/{provider.replace('_', '-')}/accounts/{code}/webhook"


def accepting_orders(mode):
    if mode == "production":
        return True
    return (mode == "test" and os.environ.get("PAYMENT_GATEWAY_TEST_MODE") == "1"
            and os.environ.get("APP_ENV", "local").lower() not in {"prod", "production"})


def bind(db, provider, code, actor, *, currency=None, match_account_currency=False):
    from app.modules.billing.commerce import audit
    from app.modules.sites.service import lock_site_settings

    site = native_site(db)
    settings = lock_site_settings(db, site)
    lock_gateways(db, provider)
    row = site_binding(db, provider)
    if code is None:
        if row is not None:
            db.delete(row)
    else:
        account = db.get(GatewayAccount, (provider, code), populate_existing=True)
        if account is None or account.status != "active":
            raise BillingError(409, "Select an active payment merchant")
        required_currency = account.config["currency"] if match_account_currency else currency
        if required_currency is not None and settings.currency != required_currency:
            raise BillingError(409, f'This payment gateway requires a {required_currency} site')
        other = db.scalar(select(SiteGatewayBinding).where(
            SiteGatewayBinding.provider == provider,
            SiteGatewayBinding.account_code == code,
            SiteGatewayBinding.tenant_id != site,
        ))
        if other is not None:
            raise BillingError(409, "This payment merchant is already assigned to another site")
        if row is None:
            row = SiteGatewayBinding(tenant_id=site, provider=provider)
            db.add(row)
        row.account_code = code
    audit(db, actor, "site.gateway.bind", payload={"tenant_id": site, "provider": provider, "account_code": code})
    db.commit()
