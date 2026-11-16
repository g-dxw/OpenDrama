#!/bin/sh
set -eu
cd "$(dirname "$0")"
target="${1:?Usage: sh backup.sh /absolute/path/backup.dump}"
umask 077
docker compose --env-file .env -f compose.yml exec -T postgres pg_dump -U minitv -d minitv -Fc > "$target"
test -s "$target"
echo "Database backup written. Also preserve the private .env and Bunny media separately."
