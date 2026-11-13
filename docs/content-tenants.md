# JJTV content tenants

This release adds content operations tenancy. JJTV owns the directory, staff memberships and routing; India owns its existing content, media jobs, review/publication and playback authorization. Consumer accounts, billing and wallets are outside this integration.

## Operators

Platform administrators manage tenants and members. Native tenants use the existing content pages; the fixed India tenant uses the dedicated workspace. A paused tenant stops JJTV operations only. Public JJTV consumers continue to use the default jjtv content scope; other native tenants currently have content administration, not automatically provisioned consumer sites.

Memberships can only narrow an existing staff role. Native content queries and writes are scoped on the server, including episodes, uploads and associations. Existing rows retain default jjtv ownership. Suppliers remain a separate concept.

India authorization is per JJTV staff member and per JJTV login session. Password/TOTP inputs are transient; business tokens and pending challenges are encrypted using an independent TENANT_CONNECTION_KEY. Refresh and other authorization mutations are serialized. Local disconnect clears only this connection: upstream logout would revoke every upstream session and is deliberately not called. No automatic upstream MFA enrollment or business publication occurs during deployment.

## Integration

INDIA_API_BASE_URL is server-only and fixed to https://8jj.ai/v1 in the deployment template. The connector forwards only an explicit allowlist of content commands, keeps upstream version/reason/idempotency semantics, and never retries mutations automatically. JSON commands carry UUID Idempotency-Key headers. Video/cover/rights uploads stream through the BFF without an entire-file buffer; limits are 100/5/10 MiB respectively. Only these exact upload routes receive the larger proxy limit.

The narrow source contract is stored in backend/app/modules/tenancy/india-contract.json, bound to provider source 2d5d88a5b6b956f95b92b4bc18dab62e93d202ce and canonical JSON/YAML hashes. Actual deployed authority still comes from upstream responses and enabled capabilities. Contract presence does not certify live media configuration or a particular runtime SHA.

## Verification and recovery

Use the disposable PostgreSQL16/Redis7 services described in tenancy-test-services.md; never point destructive fixtures at business credentials. Integration tests cover native isolation, authorization ownership, MFA, refresh, streaming/error mapping and audit correlation. Browser tests distinguish mocked upstream flows from actual operator acceptance. A real India upload/publication acceptance needs an authorized old operator and approved test content.

The migration is additive and backfills existing content to jjtv. Downgrade refuses to discard another native tenant's content ownership. Do not roll back to pre-tenancy application code after creating other native tenant content: older readers do not enforce that ownership.
