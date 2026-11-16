"""Exercise the actual generated edge and inner nginx routes, without production IO."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "deploy/video/remote-deploy.sh").read_text()
WRITE_EDGE = "write_edge() {" + DEPLOY.split("write_edge() {", 1)[1].split("\ninstall_edge() {", 1)[0]
HOSTS = ("jjtv.8jj.net", "jjtv-h5.8jj.net", "jjtv-admin.8jj.net", "jjtv-api.8jj.net")
UPLOAD = "/admin/v1/tenants/india-short-drama/india/admin/{kind}/fixture-id/content"


def edge_config(tmp_path, tls):
    root = tmp_path / "edge"
    (root / "shared").mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["bash", "-c", 'set -eu\nROOT="$1"\n' + WRITE_EDGE + '\nwrite_edge "$2"',
                             "fixture", str(root), "yes" if tls else "no"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return (root / "shared/edge-candidate.conf").read_text()


@pytest.mark.parametrize("tls", [False, True])
def test_generated_edge_upload_allowlist_is_exact_and_limits_are_preserved(tmp_path, tls):
    config = edge_config(tmp_path, tls)
    pattern = re.search(r'if \(\$host !~ "([^"]+)"\)', config).group(1)
    for host in HOSTS + ("8jj.net", "8jj.ai", "jjtv-admin.8jj.net.attacker.test", "other.8jj.net"):
        assert bool(re.fullmatch(pattern, host)) == (host in HOSTS[2:])
    server_names = re.findall(r"server_name ([^;]+);", config)
    assert all(tuple(names.split()) == HOSTS for names in server_names)
    assert "client_max_body_size 100m;" in config
    assert "limit_except PUT OPTIONS { deny all; }" in config
    assert "proxy_request_buffering off;" in config
    assert "proxy_set_header Host $host;" in config
    assert ("ssl_certificate " in config) is tls


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def upstream():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def serve(self):
            size = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(size)
            row = {"path": self.path, "method": self.command, "host": self.headers.get("Host"),
                   "authorization": self.headers.get("Authorization"), "size": len(body)}
            requests.append(row)
            if self.command == "OPTIONS":
                status, payload = 204, b""
            elif self.path == "/health" or row["authorization"] == "Bearer fixture-upload-token":
                status, payload = 200, json.dumps(row).encode()
            else:
                status, payload = 401, b'{"detail":"authentication required"}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_PUT = do_OPTIONS = do_POST = serve

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def request(port, host, path, *, method="GET", body=None, token=None, tls=False, content_length=None):
    connection = (http.client.HTTPSConnection("127.0.0.1", port, context=ssl._create_unverified_context(), timeout=15)
                  if tls else http.client.HTTPConnection("127.0.0.1", port, timeout=15))
    headers = {"Host": host}
    if token:
        headers["Authorization"] = "Bearer " + token
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


@contextmanager
def nginx_stack(tmp_path, upstream_port, tls):
    binary = os.environ.get("NGINX_BIN") or shutil.which("nginx")
    if not binary and Path("/usr/sbin/nginx").is_file():
        binary = "/usr/sbin/nginx"
    if not binary:
        pytest.skip("Install nginx or set NGINX_BIN for live proxy regression")
    inner_port, edge_port, tls_port = free_port(), free_port(), free_port()
    html = tmp_path / "html"
    for frontend in ("web", "h5", "admin"):
        target = html / frontend
        target.mkdir(parents=True)
        (target / "index.html").write_text(f"<html>{frontend}</html>")
    inner = (ROOT / "deploy/video/web.conf").read_text().replace("listen 8080", f"listen 127.0.0.1:{inner_port}")
    inner = inner.replace("http://jjtv-api-internal:18110", f"http://127.0.0.1:{upstream_port}")
    inner = inner.replace("/usr/share/nginx/html", str(html)).replace("/srv/cli/", str(ROOT / "cli") + "/")
    edge = edge_config(tmp_path, tls).replace("listen 8081;", f"listen 127.0.0.1:{edge_port};")
    edge = edge.replace("listen 8443 ssl;", f"listen 127.0.0.1:{tls_port} ssl;")
    edge = edge.replace("http://jjtv-web:8080", f"http://127.0.0.1:{inner_port}")
    if tls:
        cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=fixture.local", "-keyout", str(key), "-out", str(cert)],
                       check=True, capture_output=True)
        edge = edge.replace("/etc/nginx/tls/jjtv/fullchain.pem", str(cert)).replace("/etc/nginx/tls/jjtv/privkey.pem", str(key))
    config = tmp_path / "nginx.conf"
    config.write_text(f"daemon off;\nmaster_process off;\nerror_log stderr warn;\npid {tmp_path}/nginx.pid;\nevents {{}}\n"
                      f"http {{ access_log off; client_body_temp_path {tmp_path}/body; proxy_temp_path {tmp_path}/proxy;\n"
                      f"fastcgi_temp_path {tmp_path}/fastcgi; uwsgi_temp_path {tmp_path}/uwsgi; scgi_temp_path {tmp_path}/scgi;\n"
                      f"{inner}\n{edge}\n}}")
    command = [binary, "-p", str(tmp_path) + "/", "-c", str(config)]
    checked = subprocess.run([*command, "-t"], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    log = (tmp_path / "nginx.log").open("w+")
    process = subprocess.Popen(command, stdout=log, stderr=log)
    try:
        for _ in range(100):
            if process.poll() is not None:
                log.seek(0)
                pytest.fail(log.read())
            try:
                with socket.create_connection(("127.0.0.1", edge_port), timeout=0.05):
                    break
            except OSError:
                time.sleep(0.02)
        else:
            pytest.fail("Local nginx did not become ready")
        yield edge_port, tls_port if tls else edge_port
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()


@pytest.mark.parametrize("tls", [False, True])
def test_same_origin_upload_cli_and_health_through_both_nginx_layers(tmp_path, upstream, tls):
    upstream_port, requests = upstream
    with nginx_stack(tmp_path, upstream_port, tls) as (http_port, port):
        if tls:
            status, headers, _ = request(http_port, HOSTS[2], "/cli/install.sh")
            assert status == 308
            assert headers["Location"] == "https://jjtv-admin.8jj.net/cli/install.sh"
        # More than nginx's default 1 MiB: admin must select the streaming upload location.
        payload = b"f" * (2 * 1024 * 1024)
        for host in HOSTS[2:]:
            for kind in ("uploads", "covers", "rights/evidence"):
                path = UPLOAD.format(kind=kind)
                status, _, raw = request(port, host, path, method="PUT", body=payload,
                                         token="fixture-upload-token", tls=tls)
                assert status == 200, (host, kind, status, raw[:200])
                row = json.loads(raw)
                assert row == {"path": path, "method": "PUT", "host": host,
                               "authorization": "Bearer fixture-upload-token", "size": len(payload)}
            path = UPLOAD.format(kind="uploads")
            before = len(requests)
            assert request(port, host, path, method="GET", tls=tls)[0] == 403
            assert len(requests) == before
            assert request(port, host, path, method="OPTIONS", tls=tls)[0] == 204
            assert request(port, host, path, method="PUT", body=b"fixture", tls=tls)[0] == 401
            before = len(requests)
            assert request(port, host, path, method="PUT", content_length=100 * 1024 * 1024 + 1, tls=tls)[0] == 413
            assert len(requests) == before
        for host in HOSTS[:2]:
            for method in ("PUT", "OPTIONS"):
                before = len(requests)
                assert request(port, host, UPLOAD.format(kind="uploads"), method=method, tls=tls)[0] == 404
                assert len(requests) == before
        for filename in ("install.sh", "minitv"):
            status, headers, body = request(port, HOSTS[2], "/cli/" + filename, tls=tls)
            assert status == 200
            assert headers["Content-Type"] == "text/plain"
            assert body == (ROOT / "cli" / filename).read_bytes()
        assert request(port, HOSTS[2], "/cli/missing-file", tls=tls)[0] == 404
        assert request(port, HOSTS[2], "/health", tls=tls)[0] == 200
        status, _, body = request(port, HOSTS[2], "/admin/v1/auth/me", token="fixture-upload-token", tls=tls)
        assert status == 200 and json.loads(body)["path"] == "/admin/v1/auth/me"
