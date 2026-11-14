#!/bin/sh
set -eu
cd "$(dirname "$0")"
source="${1:?Usage: sh restore.sh /absolute/path/backup.dump}"
test -s "$source"
# Restore into a NEW database; never overwrite a running installation.
database="minitv_restore_$(date -u +%Y%m%d%H%M%S)"
docker compose --env-file .env -f compose.yml exec -T postgres createdb -U minitv "$database"
docker compose --env-file .env -f compose.yml exec -T postgres pg_restore -U minitv --exit-on-error --no-owner -d "$database" < "$source"
echo "Restored isolated database: $database. Verify it before selecting it for an installation."
