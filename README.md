# OpenDrama

OpenDrama is a self-hosted, multi-site short-drama operations platform. One installation can run independent branded sites with separate staff, viewers, content, memberships, wallets, orders, and site settings.

## Included platform

- Independent site domains, branding, locales, currencies, and time zones.
- Platform administration plus tenant-owned staff accounts, roles, and permissions.
- Draft, preview, publish, and rollback workflows for dramas and episodes.
- Shared company library with explicit site authorization.
- Bunny video and media integration, Stripe merchant binding, memberships, wallets, and orders.
- FastAPI / PostgreSQL / Redis backend with React admin, desktop web, and mobile H5 clients.
- Docker self-host deployment under `deploy/selfhost`.

The standalone snapshot is based on the published platform commit `3e3956b` (release `v2026.11.20.1`). It contains only the new multi-site runtime and deployment path. The retired India connector, old tenant compatibility routes, JJTV-specific video deployment, legacy CLI surface, and historical compatibility documentation are excluded.

## Quick start

1. Copy `.env.example` to `.env` and set local PostgreSQL, Redis, media, email, and payment values.
2. Start local services with `docker compose up -d`.
3. Run backend migrations from `backend/` and start the API.
4. Build the web clients with the project scripts.

For a production installation, read [self-hosting](docs/self-hosting.md) and run:

```sh
cd deploy/selfhost
python3 init_env.py
docker compose up -d
```

The self-host package uses the deployer's own media, email, and payment accounts. Empty provider settings leave those optional capabilities disabled.

## Development

- Backend tests live in `backend/tests`.
- Admin, desktop, H5, and mobile clients keep their own package manifests and checks.
- The API exposes platform routes under `/platform/v1` and tenant routes under `/tenant/v1`.
- Tenant requests use the `X-OpenDrama-Tenant` header when an explicit tenant scope is required.

## License

MIT. Brand assets, video, artwork, and third-party catalog samples remain subject to their own rights.
