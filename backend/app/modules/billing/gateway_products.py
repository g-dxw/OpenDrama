"""Local catalog versions and external gateway product bindings."""

from sqlalchemy import select

from app.modules.billing.commerce_models import CoinPack, CoinPackVersion, GatewayProductBinding
from app.modules.billing.models import Plan, PlanVersion
from app.modules.billing.service import INTERVAL_OF
from app.modules.tenancy.consumer_scope import current_site


def current_products(db):
    site = current_site(db)
    for kind, product, version, owner, modes in (
        ("coin_pack", CoinPack, CoinPackVersion, CoinPackVersion.pack_id, ("one_time",)),
        ("membership", Plan, PlanVersion, PlanVersion.plan_id, ("one_time", "subscription")),
    ):
        stmt = select(product).where(product.tenant_id == site, product.status == "active").order_by(product.code)
        for row in db.scalars(stmt):
            latest = db.scalar(select(version).where(version.tenant_id == site, owner == row.id)
                               .order_by(version.version.desc()).limit(1))
            if latest is None:
                continue
            for mode in modes:
                yield {
                    "product_type": kind, "product_code": row.code, "product_name": row.name_zh or row.name_en,
                    "version_id": latest.id, "version": latest.version, "billing_mode": mode,
                    "price_cents": latest.price_cents, "currency": latest.currency,
                    "recurring_interval": INTERVAL_OF.get(row.code, "month") if mode == "subscription" else None,
                }


def binding(db, provider, account_code, product_type, version_id, mode):
    key = (current_site(db), provider, account_code, product_type, version_id, mode)
    return db.get(GatewayProductBinding, key)


def catalog(db, provider, account):
    mappings = {
        (row.product_type, row.version_id, row.billing_mode): row.external_product_id
        for row in db.scalars(select(GatewayProductBinding).where(
            GatewayProductBinding.tenant_id == current_site(db),
            GatewayProductBinding.provider == provider, GatewayProductBinding.account_code == account.code,
        ))
    } if account else {}
    return [
        {**row, "external_product_id": mappings.get((row["product_type"], row["version_id"], row["billing_mode"]))}
        for row in current_products(db)
    ]
