"""Server-only settings key provisioning must preserve encrypted configuration."""
import base64
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from cryptography.fernet import Fernet
import pytest

ROOT = Path(__file__).resolve().parents[2]
MARKER = "# Provision the managed-settings key only after all JJTV API writers stop."
TABLES = ("managed_settings", "stripe_accounts", "attribution_pixels")


def run_provision(monkeypatch, target, *, present=(), populated=(), query_error=False, output=None):
    script = (ROOT / "deploy/video/remote-deploy.sh").read_text(encoding="utf-8")
    assert MARKER in script, "remote deployment is missing the settings-key preflight"
    source = script.split(MARKER, 1)[1].split("<<'PY'", 1)[1].split("\nPY\n", 1)[0]
    calls = []
    def query(command, **kwargs):
        calls.append(command)
        assert command[:6] == ["docker", "exec", "jjtv-postgres-1", "psql", "-U", "jjtv"]
        assert "-X" in command and "ON_ERROR_STOP=1" in command
        assert kwargs["check"] is True and kwargs["capture_output"] is True
        sql = command[-1]
        if query_error:
            raise subprocess.CalledProcessError(1, command, stderr="private failure")
        if output is not None:
            return SimpleNamespace(stdout=output)
        for table in TABLES:
            if "to_regclass('public." + table + "')" in sql:
                return SimpleNamespace(stdout="t\n" if table in present else "f\n")
            if "FROM public." + table + " " in sql:
                return SimpleNamespace(stdout="t\n" if table in populated else "f\n")
        pytest.fail("Unexpected settings-key query")
    monkeypatch.setattr(subprocess, "run", query)
    monkeypatch.setattr(sys, "argv", ["provision-settings", str(target)])
    exec(compile(source, str(ROOT / "deploy/video/remote-deploy.sh"), "exec"), {"__name__": "__main__"})
    return calls


def key_in(path):
    values = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
    return values["SETTINGS_ENCRYPTION_KEY"].strip().strip("\"'")


def assert_private(path):
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_missing_key_is_generated_once_in_private_runtime(monkeypatch, tmp_path, capsys):
    target = tmp_path / ".env"
    original = "APP_ENV=production\nTENANT_CONNECTION_KEY=untouched-example\nOTHER_SETTING=preserve\n"
    target.write_text(original)
    calls = run_provision(monkeypatch, target)
    created = key_in(target)
    cipher = Fernet(created.encode())
    assert cipher.decrypt(cipher.encrypt(b"roundtrip")) == b"roundtrip"
    assert len(base64.urlsafe_b64decode(created)) == 32
    assert target.read_text().startswith(original)
    assert_private(target)
    before = target.read_bytes()
    assert run_provision(monkeypatch, target) == []  # Existing key needs no database probe.
    assert target.read_bytes() == before
    assert len(calls) == 3
    assert created not in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == [target]


def test_valid_existing_key_and_other_values_are_preserved(monkeypatch, tmp_path, capsys):
    target = tmp_path / ".env"
    existing = Fernet.generate_key().decode()
    original = '# private runtime\nSETTINGS_ENCRYPTION_KEY="' + existing + '"\nUNCHANGED=keep\n'
    target.write_text(original)
    assert run_provision(monkeypatch, target, query_error=True) == []
    assert target.read_text() == original
    assert key_in(target) == existing
    assert existing not in capsys.readouterr().out
    assert_private(target)


@pytest.mark.parametrize("invalid", ["invalid", "a" * 44, base64.urlsafe_b64encode(b"too-short").decode()])
def test_invalid_existing_key_is_never_rotated(monkeypatch, tmp_path, invalid):
    target = tmp_path / ".env"
    original = "SETTINGS_ENCRYPTION_KEY=" + invalid + "\nKEEP=value\n"
    target.write_text(original)
    with pytest.raises(SystemExit, match="invalid.*refusing.*rotation"):
        run_provision(monkeypatch, target)
    assert target.read_text() == original
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("table", TABLES)
def test_missing_key_cannot_replace_existing_configuration_key(monkeypatch, tmp_path, table):
    target = tmp_path / ".env"
    original = "APP_ENV=production\n"
    target.write_text(original)
    with pytest.raises(SystemExit, match="existing commercial configuration.*restore"):
        run_provision(monkeypatch, target, present=(table,), populated=(table,))
    assert target.read_text() == original
    assert list(tmp_path.iterdir()) == [target]


def test_existing_empty_configuration_tables_allow_first_key(monkeypatch, tmp_path):
    target = tmp_path / ".env"
    target.write_text("SETTINGS_ENCRYPTION_KEY=\nKEEP=value\n")
    calls = run_provision(monkeypatch, target, present=TABLES)
    Fernet(key_in(target).encode())
    assert len(calls) == 6
    assert target.read_text().count("KEEP=value") == 1
    assert_private(target)


@pytest.mark.parametrize("output", ["", "f\nunexpected\n"])
def test_unproven_database_state_never_generates_a_key(monkeypatch, tmp_path, output):
    target = tmp_path / ".env"
    target.write_text("APP_ENV=production\n")
    before = target.read_bytes()
    with pytest.raises(SystemExit, match="Cannot verify.*configuration"):
        run_provision(monkeypatch, target, output=output)
    assert target.read_bytes() == before


def test_database_failure_never_generates_a_key_or_prints_query_details(monkeypatch, tmp_path, capsys):
    target = tmp_path / ".env"
    target.write_text("APP_ENV=production\n")
    before = target.read_bytes()
    with pytest.raises(SystemExit, match="Cannot verify.*configuration") as error:
        run_provision(monkeypatch, target, query_error=True)
    assert target.read_bytes() == before
    assert "private failure" not in str(error.value)
    assert "private failure" not in capsys.readouterr().out


def test_atomic_write_failure_retains_original_and_removes_temporary(monkeypatch, tmp_path):
    target = tmp_path / ".env"
    target.write_text("APP_ENV=production\n")
    before = target.read_bytes()
    def fail_replace(source, destination):
        assert Path(source).parent == target.parent and Path(destination) == target
        raise OSError("simulated atomic replacement failure")
    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(SystemExit, match="Cannot persist.*settings key"):
        run_provision(monkeypatch, target)
    assert target.read_bytes() == before
    assert list(tmp_path.iterdir()) == [target]


def test_database_guard_runs_after_api_stop_and_before_runtime_backup():
    script = (ROOT / "deploy/video/remote-deploy.sh").read_text(encoding="utf-8")
    assert MARKER in script
    assert script.index('stop --timeout 45 api') < script.index(MARKER)
    assert script.index(MARKER) < script.index('cp -p "$ROOT/shared/.env" "$BACKUP_DIR/runtime.env"')
    assert script.index(MARKER) < script.index('python -m app.deploy_tenant_upgrade')
