# JJTV independent tenant production release — 2026-11-14

Product version: v2026.11.14.1. Deployed source: 17023b6813e21f0f5d8e3c029b21db3b0bbb75d1, merged through [PR 8](https://github.com/88jj-com/jjtv/pull/8). [Main CI 43458256654](https://github.com/88jj-com/jjtv/actions/runs/43458256654) passed all 607 backend tests, four release-counter tests and web/H5/admin builds. Main and the checked PR head 65cdfb4 have identical trees.

## Production cutover

The isolated JJTV video stack now runs the independent platform/tenant account model at schema e6a1c3d9b742. Before changing the schema, the release stopped old API writers and verified a full PostgreSQL custom backup under /opt/jjtv/backups/20261113T160133Z-17023b6813e21f0f5d8e3c029b21db3b0bbb75d1. Stable environment and India client keys, pre/post account and permission snapshots, migration receipt and one-time tenant credentials are private (directories 0700, files 0600).

All three original platform accounts retain IDs, passwords and statuses; 22 original audit records were verified unchanged. Three independent tenant administrators were initialized with distinct random credentials and mandatory first password change. No demonstration passwords were deployed. Credentials were securely copied to the operator workstation private directory, outside Git.

JJTV received 50 tags, 12 homepage groups and six tabs; zero artificial banners or published dramas were created. Existing configuration is preserved by stable default identities; future native tenants receive their own initial configuration. The original India service, database and domains were not migrated or rewritten. Its local JJTV operator connections were invalidated; each operator must reconnect after tenant login.

## Live evidence

The deployment receipt, running API image and receiver source agree. PostgreSQL/Redis health is good, and the release lock was free after completion. All 20 frontend receipt assets were verified: 19 have exact public hashes; privacy.html has the exact receipt hash at the file and direct origin, and the public Cloudflare email-obfuscation transform strictly normalizes to that hash (three matching encoded email anchors and one decoder script). Tamper checks reject unrelated changes. Public HTML is no-store and HTTP redirects to HTTPS. The public acceptance report contains 78 successful checks.

All three tenant-admin credentials successfully authenticate to their own tenant context; business calls are rejected until the required password change. Nineteen first-login checks passed, all three test sessions were logged out, and no passwords were changed by acceptance. Chrome verified searchable India/JJTV tenant selection and login screens at 1440 and 390 pixels without document overflow or page errors.

## Acceptance limits

The workstation platform access file contains the old initial password. Read-only verification confirmed it matches the server seed value, while a historical password-change event explains why it no longer matches the current preserved platform password. No original administrator password was reset. Authenticated live platform/support business-page rendering remains pending a current administrator login; the same permissions, support-actor audit and isolation paths passed automated tests.

The existing India scoped password connector remains configured. Real connected operator upload, review and publish actions were not exercised with production content. Those require the operator's own old-site credentials and approved material.

After this account-model migration, repair forward rather than starting old global-account code against the upgraded database.
