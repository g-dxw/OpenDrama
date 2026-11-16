"""Exercise a disposable installation through its real reverse proxy and API.

Requires MINITV_VERIFY_PASSWORD. Refuses production instances and prints no
credentials. It intentionally creates two new test sites on every execution.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import httpx


def verify(base_url, username, password):
    checks = []
    sites = []
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=30, trust_env=False) as client:
        def call(method, path, expected=200, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code != expected:
                raise AssertionError(f"{method} {path.split('?')[0]} returned {response.status_code}, expected {expected}")
            return response

        health = call("GET", "/health").json()
        if health.get("env") not in {"test", "local", "development"}:
            raise SystemExit("Refusing to create test data in a production installation")
        checks.append("real PostgreSQL and Redis health")
        token = call("POST", "/platform/v1/auth/login", json=dict(username=username, password=password)).json()["token"]
        platform = {"Authorization": "Bearer " + token}
        before = call("GET", "/platform/v1/tenants", headers=platform).json()
        assert not any(row["id"] in {"jjtv", "india-short-drama"} for row in before)
        checks.append("fresh console excludes unused compatibility realms")
        suffix = uuid4().hex[:8]
        for code, locale, currency, zone in (("en-" + suffix, "en", "USD", "America/New_York"), ("zh-" + suffix, "zh", "EUR", "Asia/Shanghai")):
            created = call("POST", "/platform/v1/tenants", expected=201, headers=platform,
                json=dict(code=code, name="Verify " + code, default_locale=locale,
                          locales=["en", "zh"], currency=currency, timezone=zone)).json()
            site = created["site"]
            host = urlsplit(site["web_url"]).hostname
            boot = call("GET", "/v1/site/bootstrap", headers={"Host": host}).json()
            assert boot["tenant"]["id"] == created["tenant"]["id"]
            assert boot["tenant"]["default_locale"] == locale
            assert boot["tenant"]["currency"] == currency
            assert boot["tenant"]["timezone"] == zone
            plans = call("GET", "/v1/plans", headers={"Host": host}).json()
            currencies = []
            def collect(value):
                if isinstance(value, dict):
                    if "currency" in value: currencies.append(value["currency"])
                    for item in value.values(): collect(item)
                elif isinstance(value, list):
                    for item in value: collect(item)
            collect(plans)
            assert currencies and set(currencies) == {currency}
            sites.append(dict(id=created["tenant"]["id"], code=code, host=host, currency=currency))
        checks.extend(["two sites from one static build", "site language/currency/timezone", "default plans use site currency"])
        call("GET", "/v1/site/bootstrap", expected=404, headers={"Host": "unbound.invalid"})
        checks.append("unknown host fails closed")
        entered = call("POST", "/platform/v1/tenants/" + sites[0]["id"] + "/enter", headers=platform).json()
        owner = {"Authorization": "Bearer " + entered["token"]}
        original = call("GET", "/tenant/v1/brand", headers=owner).json()
        draft = dict(original["draft"], name="Published verification brand", primary_color="#123456")
        saved = call("PUT", "/tenant/v1/brand", headers=owner, json=dict(draft=draft, version=original["version"])).json()
        public = {"Host": sites[0]["host"]}
        assert call("GET", "/v1/site/bootstrap", headers=public).json()["brand"]["name"] != draft["name"]
        preview = call("POST", "/tenant/v1/brand/preview", headers=owner).json()
        assert call("GET", "/v1/site/bootstrap", headers=public, params={"preview_token": preview["token"]}).json()["brand"]["name"] == draft["name"]
        call("GET", "/v1/site/bootstrap", expected=403, headers={"Host": sites[1]["host"]}, params={"preview_token": preview["token"]})
        call("POST", "/tenant/v1/brand/publish", headers=owner, json={"version": saved["version"]})
        assert call("GET", "/v1/site/bootstrap", headers=public).json()["brand"]["name"] == draft["name"]
        assert call("GET", "/v1/site/bootstrap", headers={"Host": sites[1]["host"]}).json()["brand"]["name"] != draft["name"]
        call("POST", "/tenant/v1/brand/rollback", headers=owner, json={"version": 1})
        assert call("GET", "/v1/site/bootstrap", headers=public).json()["brand"]["name"] == original["published"]["name"]
        checks.extend(["draft isolation", "authorized preview and cross-site refusal", "publish and rollback through reverse proxy"])
        for path in ("/", "/admin/", "/cli/install.sh", "/cli/minitv"):
            assert call("GET", path).content
        call("GET", "/admin/v1/auth/me", expected=401)
        call("POST", "/v1/billing/webhook", expected=404)
        checks.extend(["desktop/admin/CLI static delivery", "admin API is not a SPA fallback", "development payment callback blocked"])
    return dict(passed=len(checks), checks=checks, sites=sites, database_version=health["postgres"], redis_version=health["redis"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18880")
    parser.add_argument("--username", default="verifier")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    password = os.environ.get("MINITV_VERIFY_PASSWORD")
    if not password: raise SystemExit("Set MINITV_VERIFY_PASSWORD for the disposable installation")
    report = verify(args.base_url, args.username, password)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
