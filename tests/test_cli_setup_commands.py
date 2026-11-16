"""Run the admin's copied commands against an isolated instance and fresh config."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEY = "mk_live_fixture_setup_command_only"


def generated_commands(origin, api_base=""):
    node = os.environ.get("NODE_BIN") or shutil.which("node")
    if not node:
        pytest.fail("Node is required to execute the admin CLI command generator")
    # The helper is valid JavaScript as well as TypeScript; execute the same source.
    source = base64.b64encode((ROOT / "admin/src/cli-commands.ts").read_bytes()).decode()
    script = f"""
const {{cliCommands}} = await import('data:text/javascript;base64,{source}');
const commands = cliCommands({json.dumps(origin)}, {json.dumps(api_base)});
console.log(JSON.stringify({{...commands, oneLiner: commands.oneLiner({json.dumps(KEY)})}}));
"""
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.fixture()
def instance():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            if self.path in {"/cli/install.sh", "/cli/minitv"}:
                payload = (ROOT / "cli" / self.path.rsplit("/", 1)[1]).read_bytes()
                status = 200
            elif self.path == "/health":
                payload, status = b'{"status":"ok","postgres":"ok","redis":"ok"}', 200
            elif self.headers.get("Authorization") != "Bearer " + KEY:
                payload, status = b'{"detail":{"code":"invalid_key"}}', 401
            elif self.path == "/admin/v1/auth/me":
                payload, status = b'{"username":"fixture","role":"editor"}', 200
            elif self.path == "/admin/v1/auth/keys":
                payload = json.dumps({"items": [{"key_prefix": KEY[:16], "scopes": ["read"]}]}).encode()
                status = 200
            else:
                payload, status = b'{}', 404
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def isolated_env(tmp_path):
    environment = dict(os.environ)
    for name in ("MINITV_API_KEY", "MINITV_API_BASE", "MINITV_CLI_BASE", "MINITV_CONFIG_DIR"):
        environment.pop(name, None)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    environment.update(HOME=str(tmp_path), MINITV_CONFIG_DIR=str(tmp_path / "config"),
                       MINITV_BIN_DIR=str(binaries), PATH=str(binaries) + os.pathsep + environment.get("PATH", ""))
    return environment


def run_command(command, environment, *, key=None):
    return subprocess.run(["sh", "-c", command], env=environment, input=key,
                          capture_output=True, text=True, timeout=30)


def test_three_copied_steps_work_from_empty_configuration(tmp_path, instance):
    origin, requests = instance
    commands = generated_commands(origin)
    environment = isolated_env(tmp_path)
    assert not Path(environment["MINITV_CONFIG_DIR"]).exists()
    installed = run_command(commands["install"], environment)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert not Path(environment["MINITV_CONFIG_DIR"]).exists(), "Installer without a key must not modify credentials"
    configured = run_command(commands["setKey"] + " --stdin --json", environment, key=KEY)
    assert configured.returncode == 0, configured.stdout + configured.stderr
    assert json.loads(configured.stdout)["data"]["base_url"] == origin
    config = json.loads((Path(environment["MINITV_CONFIG_DIR"]) / "config.json").read_text())
    assert config["profiles"]["prod"]["base_url"] == origin
    credentials = Path(environment["MINITV_CONFIG_DIR"]) / "credentials"
    assert credentials.read_text() == "prod=" + KEY + "\n"
    assert credentials.stat().st_mode & 0o777 == 0o600
    # No endpoint/key env survives the installation step: doctor must use saved config.
    checked = run_command(commands["doctor"] + " --json", environment)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert json.loads(checked.stdout)["ok"] is True
    assert ("/health", None) in requests
    assert ("/admin/v1/auth/me", "Bearer " + KEY) in requests
    assert KEY not in installed.stdout + configured.stdout + checked.stdout


def test_one_line_setup_uses_same_instance_and_persists_endpoint(tmp_path, instance):
    origin, _ = instance
    environment = isolated_env(tmp_path)
    result = run_command(generated_commands(origin)["oneLiner"], environment)
    assert result.returncode == 0, result.stdout + result.stderr
    config = json.loads((Path(environment["MINITV_CONFIG_DIR"]) / "config.json").read_text())
    assert config["profiles"]["prod"]["base_url"] == origin
    assert KEY not in result.stdout + result.stderr


def test_explicit_api_origin_is_in_every_setup_path():
    commands = generated_commands("https://admin.example.test", "https://api.example.test")
    assert "https://admin.example.test/cli/install.sh" in commands["install"]
    assert "MINITV_CLI_BASE='https://admin.example.test/cli'" in commands["oneLiner"]
    assert "MINITV_API_BASE='https://api.example.test'" in commands["install"]
    assert commands["setKey"] == "minitv config set-key --base-url 'https://api.example.test'"
    for page in ("ApiKeys", "PartnerApiKeys"):
        assert "cliCommands(window.location.origin, API_BASE)" in (ROOT / f"admin/src/pages/{page}.tsx").read_text()
