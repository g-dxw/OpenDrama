"""Creem account management and versioned site catalog mappings."""

import hashlib

from sqlalchemy import select

from app.modules.billing import commerce, creem, gateway_accounts, gateway_products
from app.modules.billing.commerce_models import GatewayAccount, GatewayProductBinding, SiteGatewayBinding
from app.modules.billing.models import Order
from app.modules.billing.service import BillingError, billing_mode
from app.modules.billing.site_merchants import site_currency
from app.modules.ops.managed_settings import decrypt_secret, encrypt_secret
from app.modules.tenancy.consumer_scope import current_site

PROVIDER = "creem"


def account_public(row):
    return {
        "code": row.code, "name": row.name, "status": row.status, "mode": row.config["mode"],
        "api_key_configured": bool(row.credentials.get("api_key")),
        "webhook_secret_configured": bool(row.credentials.get("webhook_secret")),
        "notify_url": gateway_accounts.notify_url(PROVIDER, row.code),
    }


def credential(row, field):
    encrypted = row.credentials.get(field)
    return decrypt_secret(encrypted) if encrypted else ""


def api_credentials(row):
    return {"api_key": credential(row, "api_key"), "mode": row.config["mode"]}


def _test(api_key, mode):
    try:
        if creem.verify_merchant(api_key=api_key, mode=mode):
            return
    except creem.CreemConfigurationError:
        pass
    except creem.CreemError:
        raise BillingError(502, "Creem merchant verification is unavailable") from None
    raise BillingError(422, "Creem rejected the API key for the selected environment")


def save_account(db, code, values, actor, *, create=False):
    gateway_accounts.lock_gateways(db, PROVIDER)
    row = db.get(GatewayAccount, (PROVIDER, code))
    if create and row is not None:
        raise BillingError(409, "Creem account code already exists")
    if not create and row is None:
        raise BillingError(404, "Creem account not found")
    mode = values.get("mode", row.config["mode"] if row else "test")
    status = values.get("status", row.status if row else "draft")
    originals = {field: credential(row, field) if row else "" for field in ("api_key", "webhook_secret")}
    candidate = {field: values.get(field) or original for field, original in originals.items()}
    changed = row is not None and (row.config["mode"] != mode or candidate != originals)
    if changed:
        history = db.scalar(select(Order.id).where(Order.provider == PROVIDER, Order.account_code == code)
                            .execution_options(include_all_tenants=True).limit(1))
        bindings = db.scalar(select(GatewayProductBinding.version_id).where(
            GatewayProductBinding.provider == PROVIDER, GatewayProductBinding.account_code == code,
        ).limit(1))
        if history or bindings:
            raise BillingError(409, "Creem orders and product mappings need their original account and environment")
    identity = hashlib.sha256((mode + "\n" + (candidate["api_key"] or "draft:" + code)).encode()).hexdigest()
    existing = db.scalar(select(GatewayAccount.code).where(
        GatewayAccount.provider == PROVIDER, GatewayAccount.identity == identity, GatewayAccount.code != code,
    ))
    if existing:
        raise BillingError(409, "This Creem API key already has an account")
    if status == "active":
        if not all(candidate.values()):
            raise BillingError(422, "Configure the Creem API key and webhook secret before activating")
        if row is None or row.status != "active" or changed:
            _test(candidate["api_key"], mode)
    if row is None:
        row = GatewayAccount(provider=PROVIDER, code=code, credentials={})
        db.add(row)
    row.credentials = {
        field: row.credentials[field] if value == originals[field] else encrypt_secret(value)
        for field, value in candidate.items() if value
    }
    row.name = values.get("name", row.name)
    row.status = status
    row.identity = identity
    row.config = {"mode": mode}
    commerce.audit(db, actor, "gateway.account.save", payload={"provider": PROVIDER, "code": code, "status": status})
    db.commit()
    return account_public(row)


def test_account(db, code):
    row = db.get(GatewayAccount, (PROVIDER, code))
    if row is None:
        raise BillingError(404, "Creem account not found")
    _test(**api_credentials(row))
    return account_public(row)


def binding_info(db):
    site = gateway_accounts.native_site(db)
    bound = gateway_accounts.bound_account(db, PROVIDER)
    taken = select(SiteGatewayBinding.account_code).where(
        SiteGatewayBinding.provider == PROVIDER, SiteGatewayBinding.tenant_id != site,
    )
    available = db.scalars(select(GatewayAccount).where(
        GatewayAccount.provider == PROVIDER, GatewayAccount.code.not_in(taken),
    ).order_by(GatewayAccount.code))
    return {
        "account_code": bound.code if bound else None, "currency": site_currency(db),
        "configured": bool(bound and bound.status == "active" and gateway_accounts.accepting_orders(bound.config["mode"])),
        "available_accounts": [
            {"code": row.code, "name": row.name, "status": row.status, "mode": row.config["mode"]}
            for row in available
        ],
    }


def bind(db, code, actor):
    gateway_accounts.bind(db, PROVIDER, code, actor)
    return binding_info(db)


def product_binding(db, account_code, product_type, version_id, mode):
    return gateway_products.binding(db, PROVIDER, account_code, product_type, version_id, mode)


def catalog(db, account):
    return gateway_products.catalog(db, PROVIDER, account)


def bindings_info(db):
    gateway_accounts.native_site(db)
    account = gateway_accounts.bound_account(db, PROVIDER)
    return {"account_code": account.code if account else None, "currency": site_currency(db),
            "billing_mode": billing_mode(db), "items": catalog(db, account)}


def verify_product(account, external_id, price, currency, mode, interval=None):
    try:
        product = creem.get_product(**api_credentials(account), product_id=external_id)
    except creem.CreemHTTPError as exc:
        if exc.status_code == 404:
            raise BillingError(422, "Creem product is unavailable for this account") from None
        raise BillingError(502, "Creem catalog lookup failed") from None
    except creem.CreemError:
        raise BillingError(502, "Creem catalog lookup failed") from None
    expected_type = "recurring" if mode == "subscription" else "onetime"
    if (product.get("id") != external_id or product.get("status") != "active"
            or product.get("price") != price or str(product.get("currency", "")).upper() != currency
            or product.get("billing_type") != expected_type):
        raise BillingError(409, "Creem product must be active and match the local price, currency and billing mode")
    if mode == "subscription" and (product.get("recurring_interval") != interval
                                   or product.get("recurring_interval_count") != 1
                                   or product.get("trial_period_days") not in (None, 0)):
        raise BillingError(409, "Creem subscription period must match the local plan and have no trial")
    return product


def save_binding(db, values, actor):
    from app.modules.sites.service import lock_site_settings

    site = gateway_accounts.native_site(db)
    lock_site_settings(db, site)
    gateway_accounts.lock_gateways(db, PROVIDER)
    account = gateway_accounts.bound_account(db, PROVIDER)
    if account is None or account.code != values["account_code"] or account.status != "active":
        raise BillingError(409, "The active Creem account changed; reload product mappings")
    kind, version_id, mode = values["product_type"], values["version_id"], values["billing_mode"]
    version = next((
        item for item in gateway_products.current_products(db)
        if (item["product_type"], item["version_id"], item["billing_mode"]) == (kind, version_id, mode)
    ), None)
    if version is None:
        raise BillingError(409, "The local product version changed; reload product mappings")
    external_id = values["external_product_id"]
    binding = product_binding(db, account.code, kind, version_id, mode)
    if external_id is None:
        if binding is not None:
            db.delete(binding)
    else:
        verify_product(account, external_id, version["price_cents"], version["currency"], mode, version["recurring_interval"])
        if binding is None:
            binding = GatewayProductBinding(tenant_id=site, provider=PROVIDER, account_code=account.code,
                                            product_type=kind, version_id=version_id, billing_mode=mode)
            db.add(binding)
        binding.external_product_id = external_id
    commerce.audit(db, actor, "gateway.product.bind", payload={
        "tenant_id": site, "provider": PROVIDER, "account_code": account.code, "product_type": kind,
        "version_id": version_id, "billing_mode": mode, "external_product_id": external_id,
    })
    db.commit()
    return bindings_info(db)
