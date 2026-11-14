# JJTV on video

This deployment owns only `/opt/jjtv`, Docker Compose project `jjtv`, the four
`jjtv*.8jj.net` hosts, `/opt/dastaan/shared/edge/jjtv.conf`, and an independent
`jjtv-8jj-net` certificate. Existing `8jj.ai`, `8jj.net`, Logto, and Admin services
and their vhosts remain unchanged. Only the JJTV web container joins the existing
edge network; its database, Redis, and API stay on JJTV's private network. API and
web readiness ports are bound to loopback (`18110` and `18111`).

Commit the source changes first, then build `web`, `h5`, and `admin` through
`scripts/build_frontends.py`. It fixes the API origin to
`https://jjtv-api.8jj.net` and records each build's source SHA and file hashes in
the private Git common directory (`jjtv-builds/<HEAD>/<frontend>.json`). The
deployment verifies that evidence before uploading. The script archives
committed backend/deployment/CLI source and the three verified frontend builds,
excluding local environment files and credentials.

Use Linux or WSL for the repository's `fcntl` release lock. For Windows Git SSH,
`JJTV_SSH_BIN` may select the working SSH binary. Native WSL SSH requires a
private key with Linux `0600` permissions and the existing trusted known_hosts.
Do not disable host-key verification.

```bash
# Run after committing the desired source, from this same working tree.
# Windows Node must use Windows Python so VITE_API_BASE reaches Vite.
/mnt/d/workspace/new/minitv/.venv/Scripts/python.exe scripts/build_frontends.py --node \
  C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe
python3 scripts/deploy_video.py --check
python3 scripts/release.py status
python3 scripts/release.py run --target video --note "JJTV isolated stack" -- \
  python3 scripts/deploy_video.py --phase prepare
python3 scripts/release.py status
```

`prepare` creates and checks the isolated stack without changing public routes.
`http` also installs the four dedicated HTTP vhosts, preserving an existing JJTV
HTTPS configuration on repeat deployments. `tls` additionally requests
and verifies a separate Let's Encrypt certificate; all four DNS records must
already point to video. These phases are idempotent and all mutations require
the `video` release lock. Edge configuration validation or reload failure restores
the previous JJTV vhost; it never replaces an existing project's configuration.

Fresh installations are empty and use `BUNNY_MODE=fake` with no payment, email,
OAuth, or media-provider credentials. This verifies the application deployment,
not commercial payment or real-video acceptance. Runtime secrets and the first
administrator password are generated only in `/opt/jjtv/shared/.env`
(`0600`). Do not print or commit that file. The legacy development payment
webhook is blocked at the JJTV web proxy, with a random independent secret as an
additional boundary. Configure actual providers separately before enabling them.

The India tenant connector uses the fixed server-side
`INDIA_API_BASE_URL=https://8jj.ai/v1`. Each installation has its own Fernet
`TENANT_CONNECTION_KEY` for encrypted upstream staff sessions. Deployments append
missing connector settings to the existing private environment file while
preserving existing secrets. An invalid existing key fails deployment instead
of being rotated and making stored sessions unreadable. Back up this key with
the database; rotating it requires an explicit credential migration.
The optional [role-delegated India mode](india-jjtv-role-auth.md) uses a separate
protected key file in the existing read-only India client directory. Deploy the
matching India provider before enabling both sides; deployment does not generate
this key or activate delegation. In this mode each operator uses their JJTV tenant
role directly instead of connecting a legacy staff session.
The native Bunny callback verifier also receives an independent, stable
`BUNNY_CALLBACK_SECRET` when the setting is missing. Existing callback secrets
are retained; a development fallback is not provisioned for production.

Both JJTV Nginx layers stream only the fixed India tenant binary-upload paths
(`/admin/v1/tenants/india-short-drama/india/admin/uploads/<id>/content`,
`covers/<id>/content`, and `rights/evidence/<id>/content`) with a `100m` ingress
limit, HTTP/1.1 request forwarding, disabled request buffering, and 300-second
body/send/read idle timeouts. IDs match the adapter's 1–160 ASCII safe-character
allowlist. Upload bytes use PUT; OPTIONS remains available for browser CORS
preflight. The application separately enforces 100 MiB video, 5 MiB cover, and
10 MiB rights-evidence limits. Other paths retain the existing `32m` limit.

The old runtime was independently checked through its public
`https://8jj.ai/v1/health` and configuration endpoints: it reports production
with PostgreSQL and `production-welfare` adapters, and staff administration
requires authentication. Its separately running local/test Worker image has
FFmpeg/FFprobe and a real 100 MiB media pipeline; that source label alone does
not prove production upload/transcode acceptance. Verify the operator's actual
upload, job, review, and publication results through the existing India APIs.
The JJTV connector does not change the old service, media credentials, or worker.

Certificate renewal uses the existing certbot timer and the dedicated
`jjtv-video` deploy hook. The original certificate lineage is untouched.

To remove JJTV's public entry points, back up and remove only
`/opt/dastaan/shared/edge/jjtv.conf`, test the edge configuration, and reload the
edge through a repository release command. Database migrations are not
automatically reversed; keep database backups before subsequent upgrades.

HTML entry points use Cache-Control: no-store and disable ETag/If-Modified-Since validation. Exact index locations also handle SPA fallback redirects, ensuring an older browser reload receives current HTML even when archived entry files have the same size and epoch mtime. Existing hashed-asset caching rules are retained.

## Independent tenant account cutover

Every video upgrade now stops the JJTV API before schema changes and writes a private backup under `/opt/jjtv/backups/<UTC>-<source SHA>/`. It verifies a full PostgreSQL custom archive, preserves the runtime environment and India client key directory, and exports account/permission snapshots before and after migration. The new-image helper checks original account IDs, password hashes, statuses, historical audit payloads and unaffected business table counts without exporting password hashes.

Only migration-created tenant administrators and cloned tenant employees receive new one-time credentials, recorded in `tenant-credentials.json` with mode 0600 and initialization audit events. Existing platform accounts and original JJTV staff keep their passwords. New tenant accounts must change their temporary password on first login. India users connect their own old-site operator only in password compatibility mode; paired role-delegated mode needs no personal legacy connection. JJTV content defaults are backfilled once, without replacing existing configuration; future native tenants initialize their own defaults on creation.

A failed upgrade keeps the old API stopped and writes private diagnostics. After the independent identity migration, repair forward using the new account model; never restart the old global-account image against the migrated database. Database restore is a separately controlled recovery operation. Public DNS and old India services are unchanged.

When using Linux Python to build, select a Linux Node binary. WSL does not automatically forward a newly set `VITE_API_BASE` to a Windows Node child; Windows Python with Windows Node preserves the intended JJTV API origin. Verify the JJTV API literal in all three JavaScript builds before deployment.
