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
python3 scripts/build_frontends.py --node \
  /mnt/c/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe
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

Certificate renewal uses the existing certbot timer and the dedicated
`jjtv-video` deploy hook. The original certificate lineage is untouched.

To remove JJTV's public entry points, back up and remove only
`/opt/dastaan/shared/edge/jjtv.conf`, test the edge configuration, and reload the
edge through a repository release command. Database migrations are not
automatically reversed; keep database backups before subsequent upgrades.
