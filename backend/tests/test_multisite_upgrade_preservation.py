"""First multi-site migration preserves public branding and purchase routing."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
from uuid import uuid4

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
import pytest
import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[2]
CASES = [
    ("env-default", [("env", "active", True, "")], "sk_test_environment", set(), "env"),
    ("custom-default", [("env", "active", False, ""), ("z_custom", "active", True, "sk_test_custom")], "sk_test_environment", set(), "z_custom"),
    ("disabled-default", [("env", "disabled", False, ""), ("a_disabled", "disabled", True, "sk_test_disabled"), ("b_active", "active", False, "sk_test_first"), ("z_active", "active", False, "sk_test_last")], "", set(), "b_active"),
    ("unconfigured-default", [("env", "active", False, ""), ("a_default", "active", True, ""), ("z_active", "active", False, "sk_test_active")], "", set(), "z_active"),
    ("explicitly-cleared-env", [("env", "active", True, ""), ("z_active", "active", False, "sk_test_active")], "sk_test_environment", {"env"}, "z_active"),
    ("no-configured-merchant", [("env", "active", True, ""), ("z_draft", "draft", False, "sk_test_draft")], "", set(), "env"),
    ("virtual-env", [("z_active", "active", False, "sk_test_active")], "sk_test_environment", set(), "env"),
]


@pytest.mark.parametrize("name,accounts,environment_secret,cleared,expected", CASES, ids=[case[0] for case in CASES])
def test_first_multisite_upgrade_keeps_brand_merchant_and_historical_business(
    engine, monkeypatch, name, accounts, environment_secret, cleared, expected,
):
    source = engine.url.database
    assert source and re.fullmatch(r"[a-z][a-z0-9_]{0,40}", source)
    database = source + "_ms_" + uuid4().hex[:8]
    assert len(database) <= 63 and database.startswith(source + "_ms_")
    admin = sa.create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    target_url = engine.url.set(database=database)
    target = None
    created = False
    try:
        with admin.connect() as db:
            db.execute(sa.text('CREATE DATABASE "' + database + '"'))
            created = True
        config = Config(str(ROOT / "backend/alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "backend/alembic"))
        config.set_main_option("sqlalchemy.url", target_url.render_as_string(hide_password=False).replace("%", "%%"))
        with monkeypatch.context() as local:
            local.setenv("ALEMBIC_DATABASE_URL", target_url.render_as_string(hide_password=False))
            local.setenv("STRIPE_SECRET_KEY", environment_secret)
            local.setenv("STRIPE_WEBHOOK_SECRET", "")
            key = Fernet.generate_key()
            local.setenv("SETTINGS_ENCRYPTION_KEY", key.decode())
            cipher = Fernet(key)
            command.upgrade(config, "a1114cancel_intent")
            target = sa.create_engine(target_url)
            # The legacy projection deliberately precedes consumer tenant_id
            # fields. Reuse the SQL-only upgrade samples, not current ORM writes.
            spec = importlib.util.spec_from_file_location("multisite_upgrade_samples", ROOT / "scripts/verify_multisite_upgrade.py")
            samples = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(samples)
            samples.sample_data(target)
            with target.begin() as db:
                db.execute(sa.text("UPDATE tenants SET name='JJTV 自营' WHERE id='jjtv'"))
                db.execute(sa.text("INSERT INTO tenants(id,code,name,kind,status) VALUES ('brand-other','brand-other','Other public brand','native','active')"))
                db.execute(sa.text("DELETE FROM stripe_accounts"))
                db.execute(sa.text("DELETE FROM managed_settings WHERE namespace LIKE 'stripe:%'"))
                for code, status, default, secret in accounts:
                    db.execute(sa.text("INSERT INTO stripe_accounts(code,name,status,is_default,livemode) VALUES (:code,:code,:status,:default,false)"), dict(code=code, status=status, default=default))
                    if secret or code in cleared:
                        values = {"secret_key": None if code in cleared else cipher.encrypt(secret.encode()).decode()}
                        db.execute(sa.text("INSERT INTO managed_settings(namespace,tenant_id,\"values\",secrets) VALUES (:namespace,'jjtv','{}'::jsonb,CAST(:secrets AS jsonb))"), dict(namespace="stripe:" + code, secrets=json.dumps(values)))
                merchant_rows = [dict(row) for row in db.execute(sa.text("SELECT * FROM stripe_accounts ORDER BY code")).mappings()]
                credential_rows = [dict(row) for row in db.execute(sa.text("SELECT * FROM managed_settings WHERE namespace LIKE 'stripe:%' ORDER BY namespace")).mappings()]
            before, columns, tables = samples.capture(target)
            command.upgrade(config, "head")
            after, _, _ = samples.capture(target, column_map=columns, table_names=tables)
            assert before == after  # IDs, prices, wallet balance, rights and sequences.
            with target.connect() as db:
                binding = db.execute(sa.text("SELECT account_code,explicit_reuse FROM site_merchant_bindings WHERE tenant_id='jjtv'")).mappings().one()
                assert dict(binding) == {"account_code": expected, "explicit_reuse": False}
                assert [dict(row) for row in db.execute(sa.text("SELECT * FROM stripe_accounts ORDER BY code")).mappings()] == merchant_rows
                assert [dict(row) for row in db.execute(sa.text("SELECT * FROM managed_settings WHERE namespace LIKE 'stripe:%' ORDER BY namespace")).mappings()] == credential_rows
                for tenant, public_name in (("jjtv", "JJTV"), ("brand-other", "Other public brand")):
                    row = db.execute(sa.text("SELECT draft,published FROM site_brands WHERE tenant_id=:tenant"), dict(tenant=tenant)).mappings().one()
                    assert row["draft"] == row["published"]
                    assert row["published"]["name"] == public_name
                    assert row["published"]["seo_title"] == public_name
                    assert db.scalar(sa.text("SELECT settings FROM site_brand_revisions WHERE tenant_id=:tenant AND version=1"), dict(tenant=tenant)) == row["published"]
                assert db.scalar(sa.text("SELECT balance FROM wallet_accounts WHERE user_id=4101")) == 127
                assert db.scalar(sa.text("SELECT id FROM entitlements WHERE user_id=4101 AND episode_id=4501")) == 4701
                assert db.scalar(sa.text("SELECT amount_cents FROM orders WHERE id=4901")) == 1999
    finally:
        if target is not None:
            target.dispose()
        if created:
            with admin.connect() as db:
                owner = db.execute(sa.text("SELECT pg_get_userbyid(datdba),current_user FROM pg_database WHERE datname=:name"), dict(name=database)).first()
                assert owner is not None and owner[0] == owner[1] and database.startswith(source + "_ms_")
                db.execute(sa.text('DROP DATABASE "' + database + '" WITH (FORCE)'))
        admin.dispose()
