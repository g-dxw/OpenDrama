#!/usr/bin/env bash
# Invoked by deploy_video.py inside the repository release lock.
set -euo pipefail
umask 077

RELEASE_DIR=${1:?release directory required}
SOURCE_SHA=${2:?source SHA required}
PHASE=${3:?prepare/http/tls required}
ROOT=/opt/jjtv
EDGE=dastaan-staging-edge-1
EDGE_DIR=/opt/dastaan/shared/edge
TLS_DIR=/opt/dastaan/shared/tls/jjtv
ACME_DIR=/opt/dastaan/shared/acme
CERT_NAME=jjtv-8jj-net
HOSTS=(jjtv.8jj.net jjtv-h5.8jj.net jjtv-admin.8jj.net jjtv-api.8jj.net)

# Nginx reload and container starts are asynchronous; wait for actual responses.
wait_for_routes() {
  local scheme=$1 port=$2 mode=${3:-content} attempt host ready code index expected actual
  local frontends=(web h5 admin)
  for attempt in {1..20}; do
    ready=yes
    if [[ $mode == redirect ]]; then
      for host in "${HOSTS[@]}"; do
        code=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' --resolve "$host:$port:127.0.0.1" "$scheme://$host:$port/" || true)
        if [[ $code != 308 ]]; then ready=no; break; fi
      done
    else
      for index in "${!frontends[@]}"; do
        host=${HOSTS[$index]}
        expected=$(sha256sum "$RELEASE_DIR/${frontends[$index]}/dist/index.html" | cut -d ' ' -f 1)
        actual=$(curl -fsS --max-time 5 --resolve "$host:$port:127.0.0.1" "$scheme://$host:$port/" | sha256sum | cut -d ' ' -f 1) || true
        if [[ $actual != "$expected" ]]; then ready=no; break; fi
        if (( index < 2 )) && ! curl -fsS --max-time 5 --resolve "$host:$port:127.0.0.1" "$scheme://$host:$port/v1/site/bootstrap" |
          python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("tenant",{}).get("code")=="jjtv" else 1)'; then
          ready=no
          break
        fi
      done
      if [[ $ready == yes ]]; then
        curl -fsS --max-time 5 --resolve "jjtv-api.8jj.net:$port:127.0.0.1" "$scheme://jjtv-api.8jj.net:$port/health" >/dev/null || ready=no
      fi
    fi
    if [[ $ready == yes ]]; then
      echo "JJTV routes ready: $scheme port=$port mode=$mode attempt=$attempt"
      return 0
    fi
    sleep 1
  done
  echo "JJTV routes did not become ready: $scheme port=$port mode=$mode" >&2
  return 1
}

[[ $RELEASE_DIR =~ ^/opt/jjtv/releases/[0-9a-f]{12}-[0-9a-f]{12}$ ]] || exit 2
[[ $SOURCE_SHA =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ $PHASE == prepare || $PHASE == http || $PHASE == tls ]] || exit 2
command -v docker >/dev/null
command -v python3 >/dev/null
command -v flock >/dev/null
docker compose version
docker inspect "$EDGE" >/dev/null
docker network inspect dastaan-staging_default >/dev/null
test -d "$EDGE_DIR"
test -d "$ACME_DIR"
install -d -m 755 "$ROOT/shared"
exec 9>"$ROOT/deploy.lock"
flock -n 9 || { echo 'Another JJTV deployment is active' >&2; exit 3; }

# Keep the existing project's vhosts and certificate completely untouched.
python3 - "$EDGE_DIR" <<'PY'
from pathlib import Path
import re
import sys
hosts = {"jjtv.8jj.net", "jjtv-h5.8jj.net", "jjtv-admin.8jj.net", "jjtv-api.8jj.net"}
for file in Path(sys.argv[1]).glob("*.conf"):
    if file.name == "jjtv.conf":
        continue
    names = re.findall(r"\bserver_name\s+([^;]+);", file.read_text())
    conflicts = hosts.intersection(name for group in names for name in group.split())
    if conflicts:
        raise SystemExit(f"Refusing to take over existing vhost {file.name}: {sorted(conflicts)}")
PY

if [[ ! -f "$ROOT/shared/.env" ]]; then
  python3 - "$ROOT/shared/.env" <<'PY'
import base64
import os
from pathlib import Path
import secrets
import sys
target = Path(sys.argv[1])
pg, redis = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
values = {
    "APP_ENV": "production", "API_HOST": "0.0.0.0", "API_PORT": "18110",
    "POSTGRES_USER": "jjtv", "POSTGRES_DB": "jjtv", "POSTGRES_PASSWORD": pg,
    "DATABASE_URL": f"postgresql://jjtv:{pg}@postgres:5432/jjtv",
    "REDIS_PASSWORD": redis, "REDIS_URL": f"redis://:{redis}@redis:6379/0",
    "SITE_DOMAIN": "jjtv.8jj.net", "SITE_URL": "https://jjtv.8jj.net",
    "API_BASE_URL": "https://jjtv-api.8jj.net", "ADMIN_URL": "https://jjtv-admin.8jj.net",
    "H5_PUBLIC_BASE_URL": "https://jjtv-h5.8jj.net",
    "CORS_ORIGINS": "https://jjtv.8jj.net,https://jjtv-h5.8jj.net,https://jjtv-admin.8jj.net",
    "BUNNY_MODE": "fake", "SEED_ADMIN_USERNAME": "admin",
    "SEED_ADMIN_PASSWORD": secrets.token_urlsafe(24),
    "PAY_WEBHOOK_SECRET": secrets.token_urlsafe(48),
    "MODERATION_KEY_SECRET": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "BUNNY_CALLBACK_SECRET": secrets.token_urlsafe(48),
    "TENANT_CONNECTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "INDIA_API_BASE_URL": "https://8jj.ai/v1",
}
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
print("Generated isolated JJTV runtime secrets on the server; no third-party credentials configured.")
PY
fi
chmod 600 "$ROOT/shared/.env"
# Upgrade existing installations without changing any existing secret.
python3 - "$ROOT/shared/.env" <<'PY'
import base64
import os
from pathlib import Path
import secrets
import sys
import tempfile

target = Path(sys.argv[1])
original = target.read_text()
values = {}
for line in original.splitlines():
    if line.strip() and not line.lstrip().startswith("#") and "=" in line:
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip("\"'")
additions = {}
# This deployment owns exactly these public JJTV hosts. Make the legacy
# compatibility boundary explicit before the first domain-aware migration.
site_defaults = {
    "SITE_LEGACY_HOSTS": "jjtv.8jj.net,jjtv-h5.8jj.net,jjtv-api.8jj.net",
    "SITE_LEGACY_TENANT": "jjtv",
    "SITE_API_HOSTS": "jjtv-api.8jj.net",
}
for name, expected in site_defaults.items():
    if name not in values:
        additions[name] = expected
        continue
    current = values[name]
    valid = current == expected if name == "SITE_LEGACY_TENANT" else (
        {host.strip().lower() for host in current.split(",") if host.strip()}
        == set(expected.split(","))
    )
    if not valid:
        raise SystemExit(f"Existing {name} conflicts with the dedicated JJTV deployment; refusing to overwrite it.")
if not values.get("BUNNY_CALLBACK_SECRET"):
    additions["BUNNY_CALLBACK_SECRET"] = secrets.token_urlsafe(48)
key = values.get("TENANT_CONNECTION_KEY", "")
if key:
    try:
        valid_key = len(base64.b64decode(key, altchars=b"-_", validate=True)) == 32
    except (ValueError, TypeError):
        valid_key = False
    if not valid_key:
        raise SystemExit("Existing TENANT_CONNECTION_KEY is invalid; refusing automatic key rotation.")
else:
    additions["TENANT_CONNECTION_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
base = values.get("INDIA_API_BASE_URL", "")
if base and base.rstrip("/") not in {"https://8jj.ai", "https://8jj.ai/v1"}:
    raise SystemExit("Existing INDIA_API_BASE_URL does not match the fixed India origin.")
if not base:
    additions["INDIA_API_BASE_URL"] = "https://8jj.ai/v1"
if additions:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=target.parent, prefix=".env-tenancy-", delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(original.rstrip("\n") + "\n")
            handle.write("\n".join(f"{name}={value}" for name, value in additions.items()) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
print("JJTV tenancy connection configuration ready; existing secrets preserved.")
PY
install -d -m 700 "$ROOT/shared/india-client"
export JJTV_INDIA_CLIENT_DIR="$ROOT/shared/india-client"
export JJTV_ENV_FILE="$ROOT/shared/.env"
export JJTV_SOURCE_SHA="$SOURCE_SHA"
export JJTV_EDGE_NETWORK=dastaan-staging_default
COMPOSE=(docker compose --env-file "$JJTV_ENV_FILE" -p jjtv -f "$RELEASE_DIR/deploy/video/compose.yml")
"${COMPOSE[@]}" build api
"${COMPOSE[@]}" up -d --wait postgres redis
# Stop every old JJTV API writer BEFORE taking the consistent backup or changing
# its account model. Failure must keep it stopped: old global identities cannot
# safely run against the independently owned tenant schema.
install -d -m 700 "$ROOT/backups"
BACKUP_DIR="$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)-$SOURCE_SHA"
mkdir -m 700 "$BACKUP_DIR"
"${COMPOSE[@]}" stop --timeout 45 api
if [[ -n $(docker ps --filter label=com.docker.compose.project=jjtv --filter label=com.docker.compose.service=api --format '{{.ID}}') ]]; then
  echo 'JJTV old API is still running; refusing account migration.' >&2
  exit 1
fi
printf 'JJTV API stopped for guarded schema upgrade. Failure leaves it stopped; no old-code rollback.\n'

# Provision the managed-settings key only after all JJTV API writers stop.
# Missing keys are safe to create only before any persistent commercial config;
# never guess a replacement for encrypted settings restored without their key.
python3 - "$ROOT/shared/.env" <<'PY'
import base64
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

target = Path(sys.argv[1])
os.chmod(target, 0o600)
original = target.read_text()
values = {}
for line in original.splitlines():
    if line.strip() and not line.lstrip().startswith("#") and "=" in line:
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip("\"'")
key = values.get("SETTINGS_ENCRYPTION_KEY", "")
if key:
    try:
        valid = len(base64.b64decode(key.encode("ascii"), altchars=b"-_", validate=True)) == 32
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise SystemExit("Existing SETTINGS_ENCRYPTION_KEY is invalid; refusing automatic key rotation.")
    print("JJTV managed-settings encryption key ready; existing key retained.")
else:
    def database_flag(sql):
        try:
            result = subprocess.run([
                "docker", "exec", "jjtv-postgres-1", "psql", "-U", "jjtv", "-d", "jjtv",
                "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", sql,
            ], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            raise SystemExit("Cannot verify existing commercial configuration; settings key was not generated.") from None
        value = (result.stdout or "").strip()
        if value not in {"t", "f"}:
            raise SystemExit("Cannot verify existing commercial configuration; settings key was not generated.")
        return value == "t"

    for table in ("managed_settings", "stripe_accounts", "attribution_pixels"):
        if database_flag("SELECT to_regclass('public." + table + "') IS NOT NULL"):
            # A nonempty older schema may keep ciphertext in a different field.
            # Prove the configuration empty rather than guessing credential names.
            if database_flag("SELECT EXISTS (SELECT 1 FROM public." + table + " LIMIT 1)"):
                raise SystemExit("Missing SETTINGS_ENCRYPTION_KEY with existing commercial configuration; restore the original settings key before deployment.")
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=target.parent, prefix=".env-settings-", delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(original.rstrip("\n") + "\nSETTINGS_ENCRYPTION_KEY=" + key + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        if os.name == "posix":
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError:
        raise SystemExit("Cannot persist the private settings key; deployment stopped.") from None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    print("JJTV managed-settings encryption key generated; existing commercial configuration is empty.")
PY
cp -p "$ROOT/shared/.env" "$BACKUP_DIR/runtime.env"
chmod 600 "$BACKUP_DIR/runtime.env"
if [[ -d $ROOT/shared/india-client ]]; then
  cp -a "$ROOT/shared/india-client" "$BACKUP_DIR/india-client"
  find "$BACKUP_DIR/india-client" -type d -exec chmod 700 {} +
  find "$BACKUP_DIR/india-client" -type f -exec chmod 600 {} +
fi
docker exec jjtv-postgres-1 pg_dump -U jjtv -d jjtv --format=custom --no-owner >"$BACKUP_DIR/database.dump"
chmod 600 "$BACKUP_DIR/database.dump"
# Read/decompress the complete archive, rather than checking only its header.
docker exec -i jjtv-postgres-1 pg_restore --file=/dev/null <"$BACKUP_DIR/database.dump"
(cd "$BACKUP_DIR" && sha256sum database.dump runtime.env > SHA256SUMS)
chmod 600 "$BACKUP_DIR/SHA256SUMS"
printf 'Verified private JJTV backup: %s\n' "$BACKUP_DIR"
"${COMPOSE[@]}" run --rm --no-deps --interactive=false \
  -v "$BACKUP_DIR:/srv/jjtv-upgrade:rw" api \
  python -m app.deploy_tenant_upgrade --backup-dir /srv/jjtv-upgrade
# First platform administrator password is supplied by stable server .env.
# Existing platform users and every original staff password are left unchanged.
"${COMPOSE[@]}" run --rm --no-deps --interactive=false api sh -ec 'python -m app.seed_admin && python -m app.seed_rewards'
"${COMPOSE[@]}" up -d --wait api web
curl -fsS http://127.0.0.1:18110/health
printf '\n'
wait_for_routes http 18111
[[ $(curl -s -o /dev/null -w '%{http_code}' -H 'Host: jjtv-api.8jj.net' -X POST http://127.0.0.1:18111/v1/billing/webhook) == 404 ]]
ln -sfn "$RELEASE_DIR" "$ROOT/current"
echo "JJTV isolated stack healthy: source=$SOURCE_SHA (empty installation; media/payment providers unconfigured)."

if [[ $PHASE == prepare ]]; then
  echo 'Prepared only: existing edge routes and all existing sites are unchanged.'
  exit 0
fi

write_edge() {
  local tls=$1
  cat >"$ROOT/shared/edge-candidate.conf" <<'NGINX'
# JJTV only. The existing 8jj.ai / 8jj.net / Logto vhosts are unchanged.
server {
    listen 8081;
    server_name jjtv.8jj.net jjtv-h5.8jj.net jjtv-admin.8jj.net jjtv-api.8jj.net;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/acme;
        try_files $uri =404;
    }
NGINX
  if [[ $tls == yes ]]; then
    cat >>"$ROOT/shared/edge-candidate.conf" <<'NGINX'
    location / { return 308 https://$host$request_uri; }
}
server {
    listen 8443 ssl;
    server_name jjtv.8jj.net jjtv-h5.8jj.net jjtv-admin.8jj.net jjtv-api.8jj.net;
    ssl_certificate /etc/nginx/tls/jjtv/fullchain.pem;
    ssl_certificate_key /etc/nginx/tls/jjtv/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_tickets off;
NGINX
  fi
  cat >>"$ROOT/shared/edge-candidate.conf" <<'NGINX'
    client_max_body_size 32m;
    resolver 127.0.0.11 valid=10s ipv6=off;
    location ~ "^/admin/v1/tenants/india-short-drama/india/admin/(uploads|covers|rights/evidence)/[A-Za-z0-9_-]{1,160}/content$" {
        if ($host != jjtv-api.8jj.net) { return 404; }
        # PUT carries bytes; OPTIONS reaches the application's CORS middleware.
        limit_except PUT OPTIONS { deny all; }
        client_max_body_size 100m;
        client_body_timeout 300s;
        proxy_request_buffering off;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        set $jjtv_upload_upstream http://jjtv-web:8080;
        proxy_pass $jjtv_upload_upstream;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 15s;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_next_upstream off;
    }
    location / {
        set $jjtv_upstream http://jjtv-web:8080;
        proxy_pass $jjtv_upstream;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        proxy_next_upstream off;
    }
}
NGINX
}

install_edge() {
  local dest="$EDGE_DIR/jjtv.conf"
  local backup="$ROOT/shared/edge-previous.conf"
  local existed=no
  if [[ -f $dest ]]; then cp -p "$dest" "$backup"; existed=yes; fi
  install -m 644 "$ROOT/shared/edge-candidate.conf" "$dest"
  if docker exec "$EDGE" nginx -t && docker exec "$EDGE" nginx -s reload; then
    return 0
  fi
  echo 'JJTV edge validation failed; restoring the previous JJTV vhost.' >&2
  if [[ $existed == yes ]]; then cp -p "$backup" "$dest"; else rm -f -- "$dest"; fi
  docker exec "$EDGE" nginx -t
  docker exec "$EDGE" nginx -s reload
  return 1
}

# Establish only the four new hosts and preserve working TLS on repeat deploys.
initial_tls=no
if [[ -s "$TLS_DIR/fullchain.pem" && -s "$TLS_DIR/privkey.pem" ]]; then initial_tls=yes; fi
write_edge "$initial_tls"
install_edge
if [[ $initial_tls == yes ]]; then
  wait_for_routes http 80 redirect
else
  wait_for_routes http 80
fi
if [[ $PHASE == http ]]; then
  echo "JJTV HTTP routes installed; existing TLS preserved=$initial_tls."
  exit 0
fi

command -v certbot >/dev/null
certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --webroot -w "$ACME_DIR" --cert-name "$CERT_NAME" --keep-until-expiring \
  -d jjtv.8jj.net -d jjtv-h5.8jj.net -d jjtv-admin.8jj.net -d jjtv-api.8jj.net
install -d -m 750 -o root -g 101 "$TLS_DIR"
install -m 644 "/etc/letsencrypt/live/$CERT_NAME/fullchain.pem" "$TLS_DIR/fullchain.pem"
install -m 600 -o 101 -g 101 "/etc/letsencrypt/live/$CERT_NAME/privkey.pem" "$TLS_DIR/privkey.pem"
write_edge yes
install_edge

# The existing certbot timer renews this separate lineage and runs this hook.
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
cat >/etc/letsencrypt/renewal-hooks/deploy/jjtv-video <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
[[ ${RENEWED_LINEAGE:-} == /etc/letsencrypt/live/jjtv-8jj-net ]] || exit 0
install -m 644 "$RENEWED_LINEAGE/fullchain.pem" /opt/dastaan/shared/tls/jjtv/fullchain.pem
install -m 600 -o 101 -g 101 "$RENEWED_LINEAGE/privkey.pem" /opt/dastaan/shared/tls/jjtv/privkey.pem
docker exec dastaan-staging-edge-1 nginx -t
docker exec dastaan-staging-edge-1 nginx -s reload
HOOK
chmod 750 /etc/letsencrypt/renewal-hooks/deploy/jjtv-video
wait_for_routes https 443
printf '\nJJTV HTTPS routes and independent certificate verified.\n'
