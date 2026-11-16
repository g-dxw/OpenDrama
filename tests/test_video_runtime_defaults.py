"""The production compatibility allowlist must stay explicit and narrow."""
import base64
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "deploy/video/remote-deploy.sh").read_text()
UPGRADE_ENV = SCRIPT.split("# Upgrade existing installations without changing any existing secret.", 1)[1].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
SITE_DEFAULTS = {
    "SITE_LEGACY_HOSTS": "jjtv.8jj.net,jjtv-h5.8jj.net,jjtv-api.8jj.net",
    "SITE_LEGACY_TENANT": "jjtv",
    "SITE_API_HOSTS": "jjtv-api.8jj.net",
}
WAIT_FOR_ROUTES = "wait_for_routes() {" + SCRIPT.split("wait_for_routes() {", 1)[1].split("\n}\n", 1)[0] + "\n}"


def original_env(**extra):
    values = {
        "SITE_URL": "https://jjtv.8jj.net",
        "H5_PUBLIC_BASE_URL": "https://jjtv-h5.8jj.net",
        "BUNNY_CALLBACK_SECRET": "fixture-existing-value",
        "TENANT_CONNECTION_KEY": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "SETTINGS_ENCRYPTION_KEY": "fixture-existing-settings-key",
        "INDIA_API_BASE_URL": "https://8jj.ai/v1",
        **extra,
    }
    return "# Existing private configuration\n" + "".join(f"{key}={value}\n" for key, value in values.items())


def run_upgrade(path):
    return subprocess.run([sys.executable, "-c", UPGRADE_ENV, str(path)], capture_output=True, text=True)


def test_upgrade_adds_only_the_dedicated_public_hosts(tmp_path):
    path = tmp_path / "runtime.env"
    original = original_env()
    path.write_text(original)
    result = run_upgrade(path)
    assert result.returncode == 0, result.stderr
    upgraded = path.read_text()
    assert upgraded.startswith(original)
    assert upgraded[len(original):] == "".join(f"{key}={value}\n" for key, value in SITE_DEFAULTS.items())
    assert "jjtv-admin.8jj.net" not in upgraded


def test_existing_compatible_configuration_is_byte_preserved(tmp_path):
    path = tmp_path / "runtime.env"
    original = original_env(**SITE_DEFAULTS)
    path.write_text(original)
    assert run_upgrade(path).returncode == 0
    assert path.read_text() == original


@pytest.mark.parametrize("name,value", [
    ("SITE_LEGACY_HOSTS", ""),
    ("SITE_LEGACY_HOSTS", "other.example.test"),
    ("SITE_LEGACY_TENANT", "another-company"),
    ("SITE_API_HOSTS", "jjtv-admin.8jj.net"),
])
def test_conflicting_or_disabled_compatibility_is_never_overwritten(tmp_path, name, value):
    path = tmp_path / "runtime.env"
    original = original_env(**{name: value})
    path.write_text(original)
    result = run_upgrade(path)
    assert result.returncode != 0
    assert name in result.stderr
    assert path.read_text() == original


@pytest.mark.parametrize("mode,expected", [("correct", 0), ("wrong-build", 1), ("wrong-tenant", 1)])
def test_route_readiness_checks_build_identity_and_runtime_site(tmp_path, mode, expected):
    release = tmp_path / "release"
    for frontend in ("web", "h5", "admin"):
        target = release / frontend / "dist"
        target.mkdir(parents=True)
        (target / "index.html").write_text(f"<html><title>MiniTV</title><body>{frontend}</body></html>")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    curl = binaries / "curl"
    curl.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
from urllib.parse import urlsplit
url = urlsplit(sys.argv[-1])
mode = os.environ["FIXTURE_MODE"]
if url.path == "/v1/site/bootstrap":
    print(json.dumps({"tenant": {"code": "other" if mode == "wrong-tenant" else "jjtv"}}))
elif url.path == "/health":
    print('{"status":"ok"}')
else:
    frontend = {"jjtv.8jj.net":"web", "jjtv-h5.8jj.net":"h5", "jjtv-admin.8jj.net":"admin"}[url.hostname]
    if mode == "wrong-build":
        sys.stdout.write("<html>stale build</html>")
    else:
        sys.stdout.write((pathlib.Path(os.environ["FIXTURE_RELEASE_DIR"])/frontend/"dist/index.html").read_text())
''')
    curl.chmod(0o755)
    sleep = binaries / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    command = "set -euo pipefail\nRELEASE_DIR=" + shlex.quote(str(release)) + "\n"
    command += "HOSTS=(jjtv.8jj.net jjtv-h5.8jj.net jjtv-admin.8jj.net jjtv-api.8jj.net)\n"
    command += WAIT_FOR_ROUTES + "\nwait_for_routes http 18111\n"
    environment = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", ""),
                   "FIXTURE_MODE": mode, "FIXTURE_RELEASE_DIR": str(release)}
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, env=environment)
    assert result.returncode == expected, result.stdout + result.stderr
