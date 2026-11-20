"""Tenant binding and encrypted credentials for the Waffo acquiring gateway."""

import hashlib

from sqlalchemy import select

from app.modules.billing import commerce, gateway_accounts, waffo
from app.modules.billing.commerce_models import GatewayAccount, SiteGatewayBinding
from app.modules.billing.models import Order
from app.modules.billing.service import BillingError
from app.modules.billing.site_merchants import site_currency
from app.modules.ops.managed_settings import decrypt_secret, encrypt_secret

PROVIDER = "waffo"
SECRET_FIELDS = ("api_key", "private_key", "public_key")


def account_public(row):
    return {
        "code": row.code, "name": row.name, "status": row.status, **row.config,
        **{field + "_configured": bool(row.credentials.get(field)) for field in SECRET_FIELDS},
        "notify_url": gateway_accounts.notify_url(PROVIDER, row.code),
    }


def credentials(row):
    return {
        "merchant_id": row.config["merchant_id"], "mode": row.config["mode"],
        **{field: decrypt_secret(row.credentials[field]) if row.credentials.get(field) else ""
           for field in SECRET_FIELDS},
    }


def _verify(settings):
    try:
        if waffo.verify_merchant(**settings):
            return
    except waffo.WaffoConfigurationError:
        pass
    except waffo.WaffoError:
        raise BillingError(502, "Waffo merchant verification is temporarily unavailable") from None
    raise BillingError(422, "Waffo credentials did not verify this merchant in the selected environment")


def save_account(db, code, values, actor, *, create=False):
    gateway_accounts.lock_gateways(db, PROVIDER)
    row = db.get(GatewayAccount, (PROVIDER, code))
    if create and row is not None:
        raise BillingError(409, "Waffo account code already exists")
    if not create and row is None:
        raise BillingError(404, "Waffo account not found")
    previous = dict(row.config) if row else {}
    config = {
        **previous,
        **{field: values[field] for field in ("merchant_id", "mode", "currency", "subscriptions_enabled")
           if field in values},
    }
    status = values.get("status", row.status if row else "draft")
    immutable = ("merchant_id", "mode", "currency")
    if row is not None and any(config[key] != previous[key] for key in immutable):
        has_history = db.scalar(select(Order.id).where(Order.provider == PROVIDER, Order.account_code == code)
                                 .execution_options(include_all_tenants=True).limit(1))
        if has_history is not None:
            raise BillingError(409, "Existing Waffo orders need their original merchant, environment and currency")
    identity = hashlib.sha256("\n".join(config[key] for key in ("mode", "merchant_id")).encode()).hexdigest()
    alias = db.scalar(select(GatewayAccount.code).where(
        GatewayAccount.provider == PROVIDER, GatewayAccount.identity == identity, GatewayAccount.code != code,
    ))
    if alias is not None:
        raise BillingError(409, "This Waffo merchant already has an account")
    existing = credentials(row) if row else {}
    candidate = {"merchant_id": config["merchant_id"], "mode": config["mode"],
                 **{field: values.get(field) or existing.get(field, "") for field in SECRET_FIELDS}}
    if status == "active":
        if not all(candidate[field] for field in SECRET_FIELDS):
            raise BillingError(422, "Configure API key, merchant private key and Waffo public key before activation")
        if row is None or row.status != "active" or existing != candidate:
            _verify(candidate)
    if row is None:
        row = GatewayAccount(provider=PROVIDER, code=code, credentials={})
        db.add(row)
    row.credentials = {
        field: row.credentials[field] if existing.get(field) == candidate[field] else encrypt_secret(candidate[field])
        for field in SECRET_FIELDS if candidate[field]
    }
    row.name = values.get("name", row.name)
    row.status = status
    row.identity = identity
    row.config = config
    commerce.audit(db, actor, "gateway.account.save", payload={
        "provider": PROVIDER, "code": code, "status": status, "subscriptions_enabled": config["subscriptions_enabled"],
    })
    db.commit()
    return account_public(row)


def test_account(db, code):
    row = db.get(GatewayAccount, (PROVIDER, code))
    if row is None:
        raise BillingError(404, "Waffo account not found")
    _verify(credentials(row))
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
    currency = site_currency(db)
    return {
        "account_code": bound.code if bound else None, "currency": currency,
        "configured": bool(bound and bound.status == "active" and bound.config["currency"] == currency
                           and gateway_accounts.accepting_orders(bound.config["mode"])),
        "available_accounts": [
            {"code": row.code, "name": row.name, "status": row.status,
             "mode": row.config["mode"], "currency": row.config["currency"]}
            for row in available
        ],
    }


def bind(db, code, actor):
    gateway_accounts.bind(db, PROVIDER, code, actor, match_account_currency=True)
    return binding_info(db)
