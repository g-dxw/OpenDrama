"""Exercise the same application against independently configured site domains."""
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.modules.ops import service as auth
from app.modules.tenancy.models import Tenant, TenantDomain
from app.modules.tenancy.permissions import seed_tenant_defaults
from app.modules.sites.service import provision_site


def test_trusted_geography_uses_same_context_as_playback_and_sales(sites, monkeypatch):
    from starlette.requests import Request
    from app.modules.sites.service import resolve_public_site
    db, _, _, _ = sites
    monkeypatch.setenv('SITE_TRUSTED_PROXIES', '10.20.30.40')
    request = Request(dict(type='http', method='GET', path='/v1/site/bootstrap', scheme='https',
        server=('library.example.test', 443), client=('10.20.30.40', 1234), query_string=b'',
        headers=[(b'host', b'alpha.example.test'), (b'x-site-country', b'US')]))
    resolve_public_site(db, request)
    assert db.info.get('viewer_country') == 'US'


def test_duplicate_domain_returns_conflict_instead_of_server_error(sites, make):
    _, _, _, _ = sites
    client = TestClient(app, raise_server_exceptions=False)
    platform = headers(make.platform_admin())
    path = '/platform/v1/tenants/alpha/domains'
    first = client.post(path, json=dict(hostname='duplicate.example.test', kind='site'), headers=platform)
    assert first.status_code == 201, first.text
    second = client.post(path, json=dict(hostname='duplicate.example.test', kind='site'), headers=platform)
    assert second.status_code == 409, second.text


def test_brand_preview_rechecks_owner_permission_after_role_demotion(sites, make):
    from app.modules.tenancy.models import TenantMember
    from app.modules.tenancy.permissions import preset_role_id
    db, client, _, owners = sites
    preview = client.post('/tenant/v1/brand/preview', headers=headers(owners[0])).json()
    member = db.get(TenantMember, ('alpha', owners[0].id))
    member.role_id = preset_role_id('alpha', 'ops')
    member.role = 'ops'
    db.commit()
    response = client.get('/v1/site/bootstrap', params=dict(preview_token=preview['token']), headers={'Host':'alpha.example.test'})
    assert response.status_code == 403, response.text


def test_malformed_origin_fails_closed_without_server_error(sites):
    client = TestClient(app)
    response = client.get('/v1/site/bootstrap', headers={'Host':'alpha.example.test', 'Origin':'https://['})
    assert response.status_code in {400, 403, 404}, response.text


def headers(person):
    return {"Authorization": "Bearer " + auth.create_session(person)}


@pytest.fixture()
def sites(session, make):
    tenants = []
    for code in ("alpha", "bravo"):
        tenant = Tenant(id=code, code=code, name=code.title(), kind="native", status="active")
        session.add(tenant)
        session.flush()
        seed_tenant_defaults(session, tenant)
        provision_site(session, tenant)
        session.add(TenantDomain(tenant_id=code, hostname=code + ".example.test", kind="site", status="active", verified_at=datetime.now(timezone.utc)))
        session.commit()
        tenants.append(tenant)
    owners = [make.admin(role="admin", tenant_id=t.id) for t in tenants]
    return session, TestClient(app), tenants, owners


def test_host_is_authority_and_unknown_host_never_falls_back(sites):
    _, client, _, _ = sites
    for code in ("alpha", "bravo"):
        response = client.get("/v1/site/bootstrap", headers={"Host": code + ".example.test"})
        assert response.status_code == 200, response.text
        assert response.json()["tenant"]["id"] == code
    assert client.get("/v1/site/bootstrap", headers={"Host": "unknown.example.test"}).status_code == 404
    assert client.get("/v1/site/bootstrap", headers={"Host": "unknown.example.test", "X-Forwarded-Host": "alpha.example.test"}).status_code == 404
    response = client.get("/v1/site/bootstrap", headers={"Host": "alpha.example.test", "X-JJTV-Tenant": "bravo"})
    assert response.json()["tenant"]["id"] == "alpha"


def test_draft_preview_publish_concurrency_and_rollback(sites):
    _, client, _, owners = sites
    owner = headers(owners[0])
    initial = client.get("/tenant/v1/brand", headers=owner).json()
    draft = dict(initial["draft"], name="Alpha New", primary_color="#123456")
    saved = client.put("/tenant/v1/brand", json={"draft": draft, "version": initial["version"]}, headers=owner)
    assert saved.status_code == 200, saved.text
    assert client.put("/tenant/v1/brand", json={"draft": draft, "version": initial["version"]}, headers=owner).status_code == 409
    published = client.get("/v1/site/bootstrap", headers={"Host": "alpha.example.test"}).json()
    assert published["brand"]["name"] == "Alpha"
    preview = client.post("/tenant/v1/brand/preview", headers=owner).json()
    shown = client.get("/v1/site/bootstrap", params={"preview_token": preview["token"]}, headers={"Host": "alpha.example.test"})
    assert shown.status_code == 200 and shown.json()["brand"]["name"] == "Alpha New"
    assert "no-store" in shown.headers["cache-control"]
    assert client.get("/v1/site/bootstrap", params={"preview_token": preview["token"]}, headers={"Host": "bravo.example.test"}).status_code == 403
    result = client.post("/tenant/v1/brand/publish", json={"version": saved.json()["version"]}, headers=owner)
    assert result.status_code == 200, result.text
    assert client.get("/v1/site/bootstrap", headers={"Host": "alpha.example.test"}).json()["brand"]["name"] == "Alpha New"
    assert client.get("/v1/site/bootstrap", headers={"Host": "bravo.example.test"}).json()["brand"]["name"] == "Bravo"
    assert client.post("/tenant/v1/brand/rollback", json={"version": 1}, headers=owner).status_code == 200
    assert client.get("/v1/site/bootstrap", headers={"Host": "alpha.example.test"}).json()["brand"]["name"] == "Alpha"


def test_site_admin_only_and_safe_brand_values(sites, make):
    _, client, _, owners = sites
    editor = make.admin(role="ops", tenant_id="alpha")
    assert client.get("/tenant/v1/brand", headers=headers(editor)).status_code == 403
    owner = headers(owners[0])
    draft = client.get("/tenant/v1/brand", headers=owner).json()["draft"]
    for bad in ({"logo_url": "javascript:alert(1)"}, {"primary_color": "red;display:none"}, {"font_family": "arbitrary-font"}):
        assert client.put("/tenant/v1/brand", json={"draft": dict(draft, **bad)}, headers=owner).status_code == 422


def test_fresh_selfhost_console_hides_only_unused_compatibility_realms(session, make, monkeypatch):
    monkeypatch.setenv("MINITV_DISTRIBUTION", "selfhost")
    client = TestClient(app)
    platform = headers(make.platform_admin())
    assert client.get("/platform/v1/tenants", headers=platform).json() == []
    placeholder = make.admin(role="admin", tenant_id="jjtv")
    placeholder.must_change_password = True
    placeholder.last_login_at = None
    session.commit()
    assert client.get("/platform/v1/tenants", headers=platform).json() == []
    make.user("existing-business@example.test")
    assert [row["id"] for row in client.get("/platform/v1/tenants", headers=platform).json()] == ["jjtv"]
    make.admin(role="ops", tenant_id="india-short-drama")
    assert {row["id"] for row in client.get("/platform/v1/tenants", headers=platform).json()} == {"jjtv", "india-short-drama"}


def test_site_settings_validate_languages_domains_and_features(sites):
    _, client, _, owners = sites
    owner = headers(owners[0])
    for body in ({"default_locale": "fr"}, {"timezone": "invalid/zone"}, {"web_url": "https://bravo.example.test"}, {"features": {"unknown": True}}):
        assert client.patch("/tenant/v1/site", json=body, headers=owner).status_code == 422
    changed = client.patch("/tenant/v1/site", json={"locales": ["en", "zh"], "default_locale": "zh", "timezone": "Asia/Shanghai", "currency": "EUR", "target_markets": ["CN"], "web_url": "https://alpha.example.test"}, headers=owner)
    assert changed.status_code == 200, changed.text
    boot = client.get("/v1/site/bootstrap", headers={"Host": "alpha.example.test"}).json()
    assert boot["tenant"]["currency"] == "EUR" and boot["tenant"]["default_locale"] == "zh"


def test_domain_requires_real_challenge_and_default_domain_needs_no_rebuild(sites, make, monkeypatch):
    _, client, _, _ = sites
    platform = headers(make.platform_admin())
    created = client.post("/platform/v1/tenants/alpha/domains", json={"hostname": "custom.example.test", "kind": "site"}, headers=platform)
    assert created.status_code == 201, created.text
    domain = created.json()
    path = "/platform/v1/tenants/alpha/domains/" + domain["id"] + "/verify"
    monkeypatch.setattr("app.modules.tenancy.management.domain_txt_records", lambda host: [])
    assert client.post(path, headers=platform).status_code == 409
    monkeypatch.setattr("app.modules.tenancy.management.domain_txt_records", lambda host: [domain["verification_token"]])
    assert client.post(path, headers=platform).status_code == 200
    assert client.get("/v1/site/bootstrap", headers={"Host": "custom.example.test"}).json()["tenant"]["id"] == "alpha"
    monkeypatch.setenv("SITE_BASE_DOMAIN", "streams.example.test")
    new = client.post("/platform/v1/tenants", json={"code": "charlie", "name": "Charlie", "currency": "EUR"}, headers=platform)
    assert new.status_code == 201, new.text
    assert new.json()["site"]["web_url"] == "https://charlie.streams.example.test"
    from sqlalchemy import select
    from app.modules.billing.models import PlanVersion
    currencies = list(sites[0].scalars(select(PlanVersion.currency).where(PlanVersion.tenant_id == new.json()["tenant"]["id"])))
    assert currencies and set(currencies) == {"EUR"}
    assert client.get("/v1/site/bootstrap", headers={"Host": "charlie.streams.example.test"}).json()["tenant"]["code"] == "charlie"
    monkeypatch.setenv("PLATFORM_ADMIN_HOSTS", "control.streams.example.test")
    reserved = client.post("/platform/v1/tenants", json={"code": "control", "name": "Reserved"}, headers=platform)
    assert reserved.status_code == 422, reserved.text
