from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.modules.ops.router import require_tenant_admin
from app.modules.ops import service as auth
from app.modules.ops.models import AdminUser
from app.modules.sites.models import SiteBrand, BrandRevision
from app.modules.sites.service import get_site, get_brand, site_out, features_for
from app.modules.tenancy.models import Tenant, TenantDomain

public_router = APIRouter(prefix="/v1/site", tags=["site"])
tenant_router = APIRouter(prefix="/tenant/v1", tags=["site-management"])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SitePatch(Strict):
    version: int | None = Field(default=None, ge=1)
    target_markets: list[str] | None = Field(default=None, max_length=100)
    default_locale: str | None = None
    locales: list[str] | None = Field(default=None, min_length=1, max_length=30)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    timezone: str | None = Field(default=None, max_length=64)
    web_url: str | None = Field(default=None, max_length=500)
    h5_url: str | None = Field(default=None, max_length=500)
    features: dict[str, bool] | None = None

    @field_validator("target_markets")
    @classmethod
    def countries(cls, value):
        if value is not None and (len(value) != len(set(value)) or any(not re.fullmatch(r"[A-Z]{2}", x) for x in value)):
            raise ValueError("Markets must be unique ISO country codes")
        return value

    @field_validator("locales")
    @classmethod
    def language_codes(cls, value):
        if value is not None and (len(value) != len(set(value)) or any(not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", x) for x in value)):
            raise ValueError("Invalid language codes")
        return value

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        if value is not None:
            try: ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError): raise ValueError("Unknown IANA timezone") from None
        return value


def safe_url(value, *, image=False):
    if not value:
        return value
    if image and value.startswith("/") and not value.startswith("//"):
        return value
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Use an http(s) URL")
    return value


class Brand(Strict):
    name: str = Field(min_length=1, max_length=100)
    logo_url: str = Field(default="", max_length=500)
    favicon_url: str = Field(default="", max_length=500)
    primary_color: str = Field(default="#ff3347", pattern=r"^#[0-9a-fA-F]{6}$")
    background_color: str = Field(default="#07070a", pattern=r"^#[0-9a-fA-F]{6}$")
    font_family: str = Field(default="Inter", pattern=r"^(Inter|Manrope|Poppins|DM Sans|Space Grotesk|System)$")
    button_radius: int = Field(default=12, ge=0, le=32)
    customer_email: str = Field(default="", max_length=254)
    social_links: dict[str, str] = Field(default_factory=dict, max_length=12)
    seo_title: str = Field(default="", max_length=200)
    seo_description: str = Field(default="", max_length=500)

    @field_validator("logo_url", "favicon_url")
    @classmethod
    def image_url(cls, value): return safe_url(value, image=True)

    @field_validator("social_links")
    @classmethod
    def links(cls, value):
        for key, url in value.items():
            if not re.fullmatch(r"[a-z_]{1,32}", key) or len(url) > 500:
                raise ValueError("Invalid social link")
            safe_url(url)
        return value

    @field_validator("customer_email")
    @classmethod
    def email(cls, value):
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Invalid contact email")
        return value


class BrandPut(Strict):
    draft: Brand
    version: int | None = Field(default=None, ge=1)


class Version(Strict):
    version: int | None = Field(default=None, ge=1)


class Rollback(Strict):
    version: int = Field(ge=1)


def brand_out(db, row):
    revisions = db.scalars(select(BrandRevision).where(BrandRevision.tenant_id == row.tenant_id).order_by(BrandRevision.version.desc()))
    return dict(draft=row.draft, published=row.published, version=row.draft_version, published_version=row.version,
                revisions=[dict(version=r.version, created_at=r.created_at.isoformat()) for r in revisions])


def locked_brand(db, tenant):
    get_brand(db, tenant)
    return db.scalar(select(SiteBrand).where(SiteBrand.tenant_id == tenant.id).with_for_update().execution_options(populate_existing=True))


def audit(db, request, user, action):
    from app.modules.tenancy.management import audit as write_audit
    write_audit(db, request, user, request.state.tenant.id, action)


@tenant_router.get("/site")
def site(request: Request, db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    result = site_out(db, request.state.tenant)
    db.commit()
    return result


@tenant_router.patch("/site")
def patch_site(body: SitePatch, request: Request, db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    tenant = request.state.tenant
    row = get_site(db, tenant.id)
    db.refresh(row, with_for_update=True)
    if body.version is not None and body.version != row.version:
        raise HTTPException(409, detail="Site settings changed; refresh before saving")
    values = body.model_dump(exclude_unset=True, exclude={"version"})
    if any(v is None for v in values.values()):
        raise HTTPException(422, detail="Site settings cannot be null")
    for field in ("web_url", "h5_url"):
        if values.get(field):
            try: safe_url(values[field])
            except ValueError as exc: raise HTTPException(422, detail=str(exc)) from None
            host = urlsplit(values[field]).hostname
            from app.modules.sites.service import hostname_site
            bound = hostname_site(db, host)
            if bound is None or bound.id != tenant.id:
                raise HTTPException(422, detail="URL must use a verified domain belonging to this site")
    if values.get("default_locale", row.default_locale) not in values.get("locales", row.locales):
        raise HTTPException(422, detail="Default language must be an enabled language")
    if "features" in values and not set(values["features"]) <= set(features_for(db, tenant)):
        raise HTTPException(422, detail="Unknown feature")
    for key, value in values.items(): setattr(row, key, value)
    row.version += 1
    audit(db, request, user, "site.settings.update")
    db.commit()
    return site_out(db, tenant)


@tenant_router.get("/brand")
def brand(request: Request, db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    result = brand_out(db, get_brand(db, request.state.tenant))
    db.commit()
    return result


@tenant_router.put("/brand")
def save_brand(body: BrandPut, request: Request, db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    row = locked_brand(db, request.state.tenant)
    if body.version is not None and body.version != row.draft_version:
        raise HTTPException(409, detail="Brand draft changed; refresh before saving")
    row.draft = body.draft.model_dump()
    row.draft_version += 1
    audit(db, request, user, "site.brand.save")
    db.commit()
    return brand_out(db, row)


@tenant_router.post("/brand/publish")
def publish(body: Version, request: Request, db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    row = locked_brand(db, request.state.tenant)
    if body.version is not None and body.version != row.draft_version:
        raise HTTPException(409, detail="Brand draft changed; refresh before publishing")
    row.published = Brand.model_validate(row.draft).model_dump()
    row.version += 1
    row.draft_version += 1
    db.add(BrandRevision(tenant_id=row.tenant_id, version=row.version, settings=row.published))
    audit(db, request, user, "site.brand.publish")
    db.commit()
    return brand_out(db, row)


@tenant_router.post("/brand/rollback")
def rollback(body: Rollback, request: Request, db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    row = locked_brand(db, request.state.tenant)
    previous = db.scalar(select(BrandRevision).where(BrandRevision.tenant_id == row.tenant_id, BrandRevision.version == body.version))
    if previous is None: raise HTTPException(404, detail="Brand revision not found")
    row.draft = previous.settings.copy()
    row.published = previous.settings.copy()
    row.version += 1
    row.draft_version += 1
    db.add(BrandRevision(tenant_id=row.tenant_id, version=row.version, settings=row.published))
    audit(db, request, user, "site.brand.rollback")
    db.commit()
    return brand_out(db, row)


@tenant_router.post("/brand/preview")
def preview(request: Request, db: Session = Depends(get_db), user=Depends(require_tenant_admin)):
    row = get_brand(db, request.state.tenant)
    token = secrets.token_urlsafe(32)
    payload = dict(tenant_id=row.tenant_id, brand=row.draft, actor_id=user.id, auth_version=user.auth_version, session_token=request.headers.get("authorization", "").removeprefix("Bearer "))
    try: auth.get_redis().set("site:preview:" + token, json.dumps(payload), ex=300)
    except Exception: raise HTTPException(503, detail="Preview session store unavailable") from None
    db.commit()
    return dict(token=token, expires_at=(datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat())


@public_router.get("/bootstrap")
def bootstrap(request: Request, response: Response, preview_token: str | None = None, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, db.info.get("content_tenant_id"))
    if tenant is None: raise HTTPException(404, detail={"code": "site_not_found"})
    row = get_brand(db, tenant)
    published = row.published
    if preview_token:
        try:
            raw = auth.get_redis().get("site:preview:" + preview_token)
            data = json.loads(raw) if raw else {}
        except Exception: raise HTTPException(503, detail="Preview session store unavailable") from None
        actor = db.get(AdminUser, data.get("actor_id")) if data.get("actor_id") else None
        if data.get("tenant_id") != tenant.id or actor is None or actor.status != "active" or actor.auth_version != data.get("auth_version"):
            raise HTTPException(403, detail="Preview has expired or belongs to another site")
        principal = auth.resolve_principal(db, data.get("session_token", ""))
        from app.modules.tenancy.permissions import effective_grants
        if principal is None or principal.tenant_id != tenant.id or not effective_grants(db, actor, tenant, principal).get("is_tenant_admin"):
            raise HTTPException(403, detail="Preview administrator permission was revoked")
        published = data["brand"]
    import os
    result = dict(tenant=site_out(db, tenant), brand=published, brand_version=row.version,
                  features=features_for(db, tenant), company=dict(name=os.environ.get("COMPANY_NAME", "MiniTV")))
    response.headers["Cache-Control"] = "private, no-store"
    db.commit()
    return result
