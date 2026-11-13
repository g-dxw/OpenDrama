"""`app.seed_admin` runs on every deploy, so it must never resurrect accounts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import seed_admin
from app.main import app
from app.modules.ops.models import AdminUser


def _bind(monkeypatch, engine) -> None:
    monkeypatch.setattr(seed_admin, "get_engine", lambda: engine)


def test_seed_only_bootstraps_an_empty_table(engine, session, monkeypatch, capsys):
    _bind(monkeypatch, engine)
    monkeypatch.setenv("SEED_ADMIN_USERNAME", "root")
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "seed-pass-1234")

    seed_admin.seed()
    out = capsys.readouterr().out
    assert "created: root" in out
    assert "seed-pass-1234" not in out  # env-provided secrets are not echoed
    assert session.query(AdminUser).count() == 1
    created = session.query(AdminUser).one()
    assert created.account_scope == "platform" and created.tenant_id is None

    seed_admin.seed()
    assert "skip" in capsys.readouterr().out
    assert session.query(AdminUser).count() == 1

    r = TestClient(app).post(
        "/platform/v1/auth/login", json={"username": "root", "password": "seed-pass-1234"}
    )
    assert r.status_code == 200


def test_seed_generates_a_password_when_none_is_configured(engine, session, monkeypatch, capsys):
    _bind(monkeypatch, engine)
    monkeypatch.delenv("SEED_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("SEED_ADMIN_PASSWORD", raising=False)

    seed_admin.seed()
    out = capsys.readouterr().out
    assert "created: admin" in out
    marker = "initial password (shown once):"
    assert marker in out
    password = out.split(marker, 1)[1].strip()

    r = TestClient(app).post(
        "/platform/v1/auth/login", json={"username": "admin", "password": password}
    )
    assert r.status_code == 200


def test_existing_tenant_accounts_do_not_prevent_platform_bootstrap(engine, session, make, monkeypatch, capsys):
    tenant_user = make.admin(role='ops')
    _bind(monkeypatch, engine)
    monkeypatch.setenv('SEED_ADMIN_USERNAME', 'platform-root')
    monkeypatch.setenv('SEED_ADMIN_PASSWORD', 'seed-pass-1234')
    seed_admin.seed()
    session.expire_all()
    assert session.get(AdminUser, tenant_user.id).account_scope == 'tenant'
    platform = session.query(AdminUser).filter(AdminUser.account_scope == 'platform').all()
    assert len(platform) == 1 and platform[0].username == 'platform-root' and platform[0].tenant_id is None
    assert 'seed-pass-1234' not in capsys.readouterr().out


def test_seed_never_reenables_an_existing_disabled_platform_account(engine, session, make, monkeypatch):
    account = make.platform_admin(username='root')
    saved = account.password_hash
    account.status = 'disabled'; session.commit()
    _bind(monkeypatch, engine)
    monkeypatch.setenv('SEED_ADMIN_USERNAME', 'root')
    monkeypatch.setenv('SEED_ADMIN_PASSWORD', 'replacement-pass-1234')
    seed_admin.seed()
    session.expire_all()
    current = session.get(AdminUser, account.id)
    assert current.status == 'disabled' and current.password_hash == saved
    assert session.query(AdminUser).filter(AdminUser.account_scope == 'platform').count() == 1
