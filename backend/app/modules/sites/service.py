from __future__ import annotations

import os
from datetime import datetime, timezone
from urllib.parse import urlsplit
from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.modules.sites.models import SiteSettings, SiteBrand, BrandRevision
from app.modules.tenancy.models import Tenant, TenantDomain


def default_brand(tenant):
    return dict(name="JJTV" if tenant.id == "jjtv" else tenant.name, logo_url="", favicon_url="", primary_color="#ff3347",
                background_color="#07070a", font_family="Inter", button_radius=12,
                customer_email="", social_links={}, seo_title="JJTV" if tenant.id == "jjtv" else tenant.name, seo_description="")


def get_site(db, tenant_id):
    row = db.get(SiteSettings, tenant_id)
    if row is None:
        row = SiteSettings(tenant_id=tenant_id, timezone="America/Los_Angeles" if tenant_id == "jjtv" else "UTC")
        db.add(row)
        db.flush()
    return row


def lock_site_settings(db, tenant_id):
    """Shared currency/order lock, held until the caller commits or rolls back.

    Platform edits already lock Tenant first. Take a compatible key-share lock
    before SiteSettings so an order's tenant FK cannot invert that lock order.
    """
    if db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update(read=True, key_share=True)) is None:
        raise HTTPException(404, detail='Site not found')
    # Concurrent first checkouts may both observe a missing settings row.
    # An atomic insert keeps their compatible Tenant key-share locks safe.
    db.execute(pg_insert(SiteSettings).values(tenant_id=tenant_id,
               timezone="America/Los_Angeles" if tenant_id == "jjtv" else "UTC")
               .on_conflict_do_nothing(index_elements=["tenant_id"]))
    return db.scalar(select(SiteSettings).where(SiteSettings.tenant_id == tenant_id)
                     .with_for_update().execution_options(populate_existing=True))


def assert_currency_change_allowed(db, locked_settings, requested_currency):
    """Every order state freezes the site's reporting denomination, including
    pending, failed and refunded orders whose immutable money snapshots remain.
    The caller must retain lock_site_settings through its settings commit.
    """
    if requested_currency == locked_settings.currency:
        return
    from app.modules.billing.models import Order
    orders = Order.__table__
    existing = db.scalar(select(orders.c.id).where(orders.c.tenant_id == locked_settings.tenant_id).limit(1))
    if existing is not None:
        raise HTTPException(409, detail={
            'code': 'site_currency_has_orders',
            'message': 'Currency cannot be changed after this site has order history; create a separate site for another currency',
            'currency': locked_settings.currency,
        })


def get_brand(db, tenant):
    row = db.get(SiteBrand, tenant.id)
    if row is None:
        settings = default_brand(tenant)
        row = SiteBrand(tenant_id=tenant.id, draft=settings, published=settings.copy())
        db.add(row)
        db.add(BrandRevision(tenant_id=tenant.id, version=1, settings=settings))
        db.flush()
    return row


def site_out(db, tenant):
    row = get_site(db, tenant.id)
    fields = ("target_markets", "default_locale", "locales", "currency", "timezone", "web_url", "h5_url", "features", "version")
    return dict(id=tenant.id, code=tenant.code, name=tenant.name, **{k: getattr(row, k) for k in fields})


def provision_site(db, tenant):
    row = get_site(db, tenant.id)
    get_brand(db, tenant)
    base = os.environ.get("SITE_BASE_DOMAIN", "").strip().lower().strip(".")
    if base and tenant.kind == "native":
        for prefix, field in ((tenant.code, "web_url"), ("m-" + tenant.code, "h5_url")):
            host = prefix + "." + base
            from app.modules.tenancy.domains import shared_hosts
            if host in shared_hosts():
                raise HTTPException(422, detail="Default site hostname is reserved for the installation control plane")
            existing = db.scalar(select(TenantDomain).where(func.lower(TenantDomain.hostname) == host))
            if existing and existing.tenant_id != tenant.id:
                raise HTTPException(409, detail="Default site hostname already belongs to another site")
            if existing is None:
                db.add(TenantDomain(tenant_id=tenant.id, hostname=host, kind="site", status="active", verified_at=datetime.now(timezone.utc)))
            if not getattr(row, field):
                setattr(row, field, "https://" + host)
    return row


def hostname_site(db, hostname):
    if not hostname:
        return None
    hostname = hostname.lower().rstrip(".")
    binding = db.scalar(select(TenantDomain).where(func.lower(TenantDomain.hostname) == hostname,
                         TenantDomain.kind == "site", TenantDomain.status == "active", TenantDomain.verified_at.is_not(None)))
    tenant = db.get(Tenant, binding.tenant_id) if binding else None
    if tenant and (tenant.status != "active" or tenant.kind != "native"):
        return None
    return tenant


def resolve_public_site(db, request: Request):
    host = (request.url.hostname or "").lower().rstrip(".")
    api_hosts = {x.strip().lower() for x in os.environ.get("SITE_API_HOSTS", "").split(",") if x.strip()}
    if host in api_hosts and request.headers.get("origin"):
        try: origin = urlsplit(request.headers["origin"])
        except ValueError: raise HTTPException(404, detail={"code": "site_not_found"}) from None
        if origin.scheme not in {"https", "http"}:
            raise HTTPException(404, detail={"code": "site_not_found"})
        host = origin.hostname or ""
    tenant = hostname_site(db, host)
    if tenant is None:
        raise HTTPException(404, detail={"code": "site_not_found", "message": "This domain is not an active site"})
    request.state.tenant_id = tenant.id
    request.state.site = tenant
    db.info["content_tenant_id"] = tenant.id
    # Geographic headers are accepted only from explicitly trusted reverse proxies.
    # Explicit unknown values prevent stale context or an optional client field
    # from becoming an authorization fallback.
    db.info["trusted_country"] = None
    db.info["viewer_country"] = None
    trusted = {x.strip() for x in os.environ.get("SITE_TRUSTED_PROXIES", "").split(",") if x.strip()}
    if request.client and request.client.host in trusted:
        country = request.headers.get("x-site-country", "").upper()
        if len(country) == 2 and country.isascii() and country.isalpha():
            db.info["trusted_country"] = country
            db.info["viewer_country"] = country
    return tenant


def features_for(db, tenant):
    from app.modules.tenancy.modules import enabled_modules
    modules = enabled_modules(db, tenant)
    result = {key: key in modules for key in ("dramas", "users", "plans", "finance", "dashboard", "rewards", "comments", "support", "attribution")}
    result.update(coins="coins" in modules or "users" in modules,
                  membership="plans" in modules, library="dramas" in modules)
    for key, enabled in (get_site(db, tenant.id).features or {}).items():
        if key in result:
            result[key] = result[key] and bool(enabled)
    return result
