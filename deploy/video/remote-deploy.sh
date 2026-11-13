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
}
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
print("Generated isolated JJTV runtime secrets on the server; no third-party credentials configured.")
PY
fi
chmod 600 "$ROOT/shared/.env"
export JJTV_ENV_FILE="$ROOT/shared/.env"
export JJTV_SOURCE_SHA="$SOURCE_SHA"
export JJTV_EDGE_NETWORK=dastaan-staging_default
COMPOSE=(docker compose --env-file "$JJTV_ENV_FILE" -p jjtv -f "$RELEASE_DIR/deploy/video/compose.yml")
"${COMPOSE[@]}" build api
"${COMPOSE[@]}" up -d --wait postgres redis
# First administrator password is supplied by the server .env, never printed.
"${COMPOSE[@]}" run --rm --no-deps --interactive=false api sh -ec 'alembic upgrade head && python -m app.seed_admin && python -m app.seed_rewards'
"${COMPOSE[@]}" up -d --wait api web
curl -fsS http://127.0.0.1:18110/health
printf '\n'
for host in "${HOSTS[@]:0:3}"; do
  curl -fsS -H "Host: $host" http://127.0.0.1:18111/ | grep -q JJTV
done
curl -fsS -H 'Host: jjtv-api.8jj.net' http://127.0.0.1:18111/health >/dev/null
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
  for host in "${HOSTS[@]}"; do
    [[ $(curl -s -o /dev/null -w '%{http_code}' -H "Host: $host" http://127.0.0.1/) == 308 ]]
  done
else
  for host in "${HOSTS[@]:0:3}"; do
    curl -fsS -H "Host: $host" http://127.0.0.1/ | grep -q JJTV
  done
  curl -fsS -H 'Host: jjtv-api.8jj.net' http://127.0.0.1/health >/dev/null
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
for host in "${HOSTS[@]:0:3}"; do
  curl -fsS --resolve "$host:443:127.0.0.1" "https://$host/" | grep -q JJTV
done
curl -fsS --resolve jjtv-api.8jj.net:443:127.0.0.1 https://jjtv-api.8jj.net/health
printf '\nJJTV HTTPS routes and independent certificate verified.\n'
