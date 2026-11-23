"""Waffo Pancake merchants, their stores, and exact product/version mappings."""

import hashlib

from sqlalchemy import select

from app.modules.billing import commerce, gateway_accounts, gateway_money, gateway_products, waffo_pancake as pancake
from app.modules.billing.commerce_models import GatewayAccount, GatewayProductBinding, SiteGatewayBinding
from app.modules.billing.models import Order
from app.modules.billing.service import BillingError, billing_mode
from app.modules.billing.site_merchants import site_currency
from app.modules.ops.managed_settings import decrypt_secret, encrypt_secret

PROVIDER = "waffo_pancake"
PERIODS = {"week": "weekly", "month": "monthly", "year": "yearly"}


def account_public(row):
    return {
        "code": row.code, "name": row.name, "status": row.status, **row.config,
        "private_key_configured": bool(row.credentials.get("private_key")),
        "notify_url": gateway_accounts.notify_url(PROVIDER, row.code),
    }


def credentials(row):
    return {
        "merchant_id": row.config["merchant_id"], "mode": row.config["mode"],
        "private_key": decrypt_secret(row.credentials["private_key"]) if row.credentials.get("private_key") else "",
    }


def _stores(api):
    try:
        return pancake.list_stores(**api)
    except pancake.PancakeError:
        raise BillingError(502, "Waffo Pancake stores could not be verified") from None


def _allowed_store(api, store_id):
    stores = _stores(api)
    store = next((item for item in stores if item["id"] == store_id), None)
    if store is None or store.get("status") != "active" or (api["mode"] == "production" and not store.get("prod_enabled")):
        raise BillingError(409, "The selected Waffo Pancake store is unavailable in this environment")
    return stores


def save_account(db, code, values, actor, *, create=False):
    gateway_accounts.lock_gateways(db, PROVIDER)
    row = db.get(GatewayAccount, (PROVIDER, code))
    if create and row is not None:
        raise BillingError(409, "Waffo Pancake account code already exists")
    if not create and row is None:
        raise BillingError(404, "Waffo Pancake account not found")
    previous = dict(row.config) if row else {}
    config = {**previous, **{field: values[field] for field in ("merchant_id", "store_id", "mode") if field in values}}
    status = values.get("status", row.status if row else "draft")
    old_key = credentials(row)["private_key"] if row else ""
    private_key = values.get("private_key") or old_key
    if row is not None and config != previous:
        history = db.scalar(select(Order.id).where(Order.provider == PROVIDER, Order.account_code == code)
                            .execution_options(include_all_tenants=True).limit(1))
        mapped = db.scalar(select(GatewayProductBinding.version_id).where(
            GatewayProductBinding.provider == PROVIDER, GatewayProductBinding.account_code == code,
        ).limit(1))
        if history or mapped:
            raise BillingError(409, "Waffo Pancake orders and mappings need the original merchant, store and environment")
    identity = hashlib.sha256("\n".join(config[key] for key in ("mode", "merchant_id", "store_id")).encode()).hexdigest()
    alias = db.scalar(select(GatewayAccount.code).where(
        GatewayAccount.provider == PROVIDER, GatewayAccount.identity == identity, GatewayAccount.code != code,
    ))
    if alias:
        raise BillingError(409, "This Waffo Pancake store already has an account")
    if status == "active" and not private_key:
        raise BillingError(422, "Configure the Waffo Pancake private key before activating")
    if private_key and (not row or (row.status != status and status == "active") or config != previous or old_key != private_key):
        _allowed_store({"private_key": private_key, "merchant_id": config["merchant_id"], "mode": config["mode"]},
                       config["store_id"])
    if row is None:
        row = GatewayAccount(provider=PROVIDER, code=code, credentials={})
        db.add(row)
    if private_key != old_key:
        row.credentials = {"private_key": encrypt_secret(private_key)}
    row.name = values.get("name", row.name)
    row.status = status
    row.identity = identity
    row.config = config
    commerce.audit(db, actor, "gateway.account.save", payload={"provider": PROVIDER, "code": code, "status": status})
    db.commit()
    return account_public(row)


def _account(db, code):
    row = db.get(GatewayAccount, (PROVIDER, code))
    if row is None:
        raise BillingError(404, "Waffo Pancake account not found")
    return row


def test_account(db, code):
    row = _account(db, code)
    _allowed_store(credentials(row), row.config["store_id"])
    return account_public(row)


def amount_minor(amount, currency, *, allow_zero=False):
    try:
        return gateway_money.as_minor(amount, currency, allow_zero=allow_zero)
    except ValueError:
        raise BillingError(409, "Waffo Pancake returned an invalid monetary amount") from None


def _products(row):
    try:
        return pancake.get_catalog(**credentials(row), store_id=row.config["store_id"])
    except pancake.PancakeError:
        raise BillingError(502, "Waffo Pancake products could not be verified") from None


def merchant_catalog(db, code):
    row = _account(db, code)
    stores = _allowed_store(credentials(row), row.config["store_id"])
    products = []
    for item in _products(row):
        prices = item.get("prices", [])
        for price in prices or [{}]:
            products.append({
                "id": item["id"], "name": item["name"], "status": item["status"],
                "billing_mode": item["billing_mode"], "currency": price.get("currency"),
                "price_minor": amount_minor(price["amount"], price["currency"]) if price.get("amount") else None,
            })
    return {"stores": stores, "products": products}


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


def verify_product(row, external_id, price_cents, currency, mode, interval):
    product = next((item for item in _products(row) if item["id"] == external_id), None)
    if not product or product["status"] != "active" or product["billing_mode"] != mode:
        raise BillingError(409, "Select an active Waffo Pancake product from the bound store with the local billing mode")
    price = next((item for item in product["prices"] if item["currency"] == currency), None)
    if price is None or amount_minor(price["amount"], currency) != price_cents:
        raise BillingError(409, "Waffo Pancake product price and currency must match the local version")
    if mode == "subscription" and product.get("billing_period") != PERIODS.get(interval):
        raise BillingError(409, "Waffo Pancake product must match the local renewal period")
    return price


def bindings_info(db):
    gateway_accounts.native_site(db)
    bound = gateway_accounts.bound_account(db, PROVIDER)
    return {"account_code": bound.code if bound else None, "currency": site_currency(db),
            "billing_mode": billing_mode(db), "items": gateway_products.catalog(db, PROVIDER, bound)}


def save_binding(db, values, actor):
    from app.modules.sites.service import lock_site_settings

    site = gateway_accounts.native_site(db)
    lock_site_settings(db, site)
    gateway_accounts.lock_gateways(db, PROVIDER)
    account = gateway_accounts.bound_account(db, PROVIDER)
    if account is None or account.code != values["account_code"] or account.status != "active":
        raise BillingError(409, "The active Waffo Pancake account changed; reload product mappings")
    kind, version_id, mode = values["product_type"], values["version_id"], values["billing_mode"]
    version = next((product for product in gateway_products.current_products(db)
                    if (product["product_type"], product["version_id"], product["billing_mode"]) == (kind, version_id, mode)), None)
    if version is None:
        raise BillingError(409, "The local product version changed; reload product mappings")
    binding = gateway_products.binding(db, PROVIDER, account.code, kind, version_id, mode)
    external = values["external_product_id"]
    if external is None:
        if binding is not None:
            db.delete(binding)
    else:
        verify_product(account, external, version["price_cents"], version["currency"], mode, version["recurring_interval"])
        if binding is None:
            binding = GatewayProductBinding(tenant_id=site, provider=PROVIDER, account_code=account.code,
                                            product_type=kind, version_id=version_id, billing_mode=mode)
            db.add(binding)
        binding.external_product_id = external
    commerce.audit(db, actor, "gateway.product.bind", payload={
        "tenant_id": site, "provider": PROVIDER, "account_code": account.code, "product_type": kind,
        "version_id": version_id, "billing_mode": mode, "external_product_id": external,
    })
    db.commit()
    return bindings_info(db)
