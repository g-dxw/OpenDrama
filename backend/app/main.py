"""Local health app for the JJTV test environment."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from contextlib import asynccontextmanager

import psycopg
import redis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("minitv.api")


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


load_env()
@asynccontextmanager
async def lifespan(application):
    from app.modules.attribution.worker import start_worker,stop_worker
    start_worker()
    try:
        yield
    finally:
        stop_worker()

app = FastAPI(title="JJTV API", version="0.0.0", lifespan=lifespan)

_DEFAULT_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "https://jjtv.8jj.net",
    "https://jjtv-admin.8jj.net",
    "https://jjtv-h5.8jj.net",
]


def _cors_origins() -> list[str]:
    extra = os.environ.get("CORS_ORIGINS", "")
    origins = list(_DEFAULT_ORIGINS)
    for item in extra.split(","):
        item = item.strip().rstrip("/")
        if item and item not in origins:
            origins.append(item)
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    db_url = os.environ["DATABASE_URL"]
    redis_url = os.environ["REDIS_URL"]
    with psycopg.connect(db_url, connect_timeout=3) as conn:
        pg_version = conn.execute("SHOW server_version").fetchone()[0]
    client = redis.Redis.from_url(redis_url, socket_connect_timeout=3)
    client.ping()
    return {
        "status": "ok",
        "env": os.environ.get("APP_ENV", "local"),
        "postgres": pg_version,
        "redis": client.info("server").get("redis_version"),
    }


def _register_routers() -> None:
    from app.modules.content.public_router import router as public_router
    from app.modules.ops.api_key_router import router as admin_api_key_router
    from app.modules.ops.flow_router import router as admin_flow_router
    from app.modules.ops.partner_router import router as partner_router
    from app.modules.ops.review_router import router as review_router
    from app.modules.ops.settlement_router import router as settlement_router
    from app.modules.ops.router import router as admin_router
    from app.modules.public_flow import router as public_flow_router
    from app.modules.recommend.router import router as feed_router

    from app.modules.tenancy.router import router as india_router
    from app.modules.tenancy.management import platform_router,tenant_router,legacy_router
    from app.modules.tenancy.tenant_staff import tenant_router as staff_router,legacy_staff_router
    from app.modules.ops.api_key_router import tenant_router as tenant_key_router
    # Replace legacy globally-scoped staff controllers with tenant-owned aliases.
    admin_router.routes[:]=[route for route in admin_router.routes if not getattr(route,'path','').startswith('/admin/v1/staff')]
    app.include_router(platform_router)
    app.include_router(tenant_router)
    app.include_router(staff_router)
    app.include_router(legacy_router)
    app.include_router(legacy_staff_router)
    app.include_router(tenant_key_router)
    app.include_router(india_router)
    app.include_router(admin_router)
    app.include_router(admin_api_key_router)
    from app.modules.content.enhancements import admin_router as content_admin, public_router as catalog_router
    from app.modules.ops.login_router import router as login_settings_router
    from app.modules.billing.commerce_router import admin_router as commerce_admin, public_router as commerce_public
    from app.modules.support.router import admin_router as support_admin, public_router as support_public
    from app.modules.attribution.router import admin_router as attr_admin, public_router as attr_public
    for router in (content_admin,catalog_router,login_settings_router,commerce_admin,commerce_public,support_admin,support_public,attr_admin,attr_public):
        app.include_router(router)
    app.include_router(admin_flow_router)
    app.include_router(partner_router)
    app.include_router(review_router)
    app.include_router(settlement_router)
    app.include_router(public_router)
    app.include_router(public_flow_router)
    app.include_router(feed_router)


_register_routers()

@app.middleware("http")
async def private_admin_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(('/admin/','/partner/','/platform/','/tenant/')):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Pragma"] = "no-cache"
        vary = {value.strip() for value in response.headers.get("Vary", "").split(",") if value.strip()}
        vary.update({"Authorization", "X-JJTV-Tenant", "Origin"})
        response.headers["Vary"] = ", ".join(sorted(vary))
    return response


@app.middleware('http')
async def verified_tenant_domain_cors(request: Request,call_next):
    origin=request.headers.get('Origin')
    approved=False
    if origin and origin not in _cors_origins():
        from urllib.parse import urlsplit
        from app.db import get_engine,make_session_factory
        from app.modules.tenancy.domains import active_admin_domain
        db=make_session_factory(get_engine())()
        try: approved=active_admin_domain(db,urlsplit(origin).hostname) is not None
        finally: db.close()
    if approved and request.method=='OPTIONS':
        from fastapi.responses import Response
        return Response(status_code=204,headers={'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Credentials':'true','Access-Control-Allow-Methods':'GET,POST,PUT,PATCH,DELETE,HEAD,OPTIONS','Access-Control-Allow-Headers':'Authorization,Content-Type,X-JJTV-Tenant,Idempotency-Key,Tus-Resumable,Upload-Offset,Upload-Length,Upload-Metadata','Vary':'Origin','Cache-Control':'no-store'})
    response=await call_next(request)
    if approved:
        response.headers['Access-Control-Allow-Origin']=origin
        response.headers['Access-Control-Allow-Credentials']='true'
        response.headers['Vary']='Origin, Authorization, X-JJTV-Tenant'
    return response


@app.middleware("http")
async def record_api_key_events(request: Request, call_next):
    """Record one row per *write* request made with an API key.

    ``audit_logs`` already says which person acted; this fills in which
    credential did it. GETs are skipped on purpose — they would dominate the
    table — and a failure here must never break the request itself.
    """
    started = time.perf_counter()
    response = await call_next(request)
    state = request.scope.get("state") or {}
    key_id = state.get("api_key_id")
    if key_id and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        from app.db import get_engine, make_session_factory
        from app.modules.ops.models import ApiKeyEvent

        try:
            session = make_session_factory(get_engine())()
            try:
                session.add(
                    ApiKeyEvent(
                        key_id=key_id,
                        staff_id=state.get("api_key_staff_id") or 0,
                        method=request.method,
                        path=request.url.path[:200],
                        status_code=response.status_code,
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                )
                session.commit()
            finally:
                session.close()
        except Exception:  # telemetry must never break the request
            logger.warning("api_key_event_write_failed", exc_info=True)
    return response


def _start_background_jobs() -> None:
    # Tests pin BUNNY_MODE=fake, so this stays a no-op there. Real mode polls
    # Bunny because the library has no webhook and finished encodes would
    # otherwise sit on "transcoding" until someone clicks sync.
    from app.modules.ops.flow_router import start_transcode_reconciler
    from app.modules.social.moderation import start_moderation_worker

    start_transcode_reconciler()
    start_moderation_worker()


_start_background_jobs()
