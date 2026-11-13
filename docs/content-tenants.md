# JJTV content tenants

This release adds content operations tenancy. JJTV owns the directory, staff memberships and routing; India owns its existing content, media jobs, review/publication and playback authorization. Consumer accounts, billing and wallets are outside this integration.

## Operators

Platform administrators manage tenants and members. Native tenants use the existing content pages; the fixed India tenant uses the dedicated workspace. A paused tenant stops JJTV operations only. Public JJTV consumers continue to use the default jjtv content scope; other native tenants currently have content administration, not automatically provisioned consumer sites.

Each membership selects a role defined inside that tenant. Roles carry explicit view/menu and action permissions; a staff member may upload in one tenant and review in another. Native content grants are independent of the employee's platform ops/finance label when explicitly assigned by role_id. The default templates preserve the previous operator and read-only workflows; legacy role input retains its old narrowing semantics. Platform administration still requires the existing platform identity in addition to configured permissions. Native content queries and writes remain scoped on the server, including episodes, uploads and associations. Existing rows retain default jjtv ownership. Suppliers remain a separate concept.

India authorization is per JJTV staff member and per JJTV login session. Password/TOTP inputs are transient; business tokens and pending challenges are encrypted using an independent TENANT_CONNECTION_KEY. Refresh and other authorization mutations are serialized. Local disconnect clears only this connection: upstream logout would revoke every upstream session and is deliberately not called. No automatic upstream MFA enrollment or business publication occurs during deployment.

## Integration

INDIA_API_BASE_URL is server-only and fixed to https://8jj.ai/v1 in the deployment template. The connector forwards only an explicit allowlist of content commands, keeps upstream version/reason/idempotency semantics, and never retries mutations automatically. JSON commands carry UUID Idempotency-Key headers. Video/cover/rights uploads stream through the BFF without an entire-file buffer; limits are 100/5/10 MiB respectively. Only these exact upload routes receive the larger proxy limit.

The narrow source contract is stored in backend/app/modules/tenancy/india-contract.json, bound to provider source 2d5d88a5b6b956f95b92b4bc18dab62e93d202ce and canonical JSON/YAML hashes. Actual deployed authority still comes from upstream responses and enabled capabilities. Contract presence does not certify live media configuration or a particular runtime SHA.

## Verification and recovery

Use the disposable PostgreSQL16/Redis7 services described in tenancy-test-services.md; never point destructive fixtures at business credentials. Integration tests cover native isolation, authorization ownership, MFA, refresh, streaming/error mapping and audit correlation. Browser tests distinguish mocked upstream flows from actual operator acceptance. A real India upload/publication acceptance needs an authorized old operator and approved test content.

The migration is additive and backfills existing content to jjtv. Downgrade refuses to discard another native tenant's content ownership. Do not roll back to pre-tenancy application code after creating other native tenant content: older readers do not enforce that ownership.

## Operating India from JJTV

Open 租户管理, enter India Short Drama, then connect an existing India operator with that operator's password and authenticator code. First-time authenticator enrollment is displayed only when requested by India. Platform administrators assign JJTV staff membership; the upstream operator role still controls editing, review and publication. No India password or TOTP code is saved.

Create or select a series, maintain its rights and evidence, add episodes, upload the original video, and wait for the upstream job result. Complete subtitles and cover as needed, then submit, review and publish using the upstream version and permission checks. A rejected or timed-out operation never displays a fabricated successful publication.

## Implementation verification (2026-11-13)

The 11-file PostgreSQL/Redis run against committed backend be676417 covered 198 tests: 196 passed, with two new tenancy tests failing due to an incomplete expiration mock and an overly exact cache-header assertion. After only those fixtures/assertions were corrected in 959e724e, all 18 tenancy tests passed. The other 180 connector/native-content/upload/API-key/partner tests required no source changes. Migrations were exercised against a freshly created disposable database. Owned test containers, network and temporary source were removed after the run.

Admin TypeScript and production build passed. Browser mock checks covered tenant switching/membership, India password/MFA/enrollment, original upload/job queries, subtitle/cover/evidence, failed and successful publication, transient secrets and finance read-only pages at desktop and mobile widths. These are implementation tests; an actual India operator upload/publication is not certified by them.

## Configurable tenant roles

In 租户管理, open 角色与权限 for the tenant, create a role and select its menu/view and operation permissions. Selecting an action also selects its required view permission; clearing the view removes dependent actions. Assign the role in 成员. Each member has one role per tenant in this version. The same employee can have different roles across tenants. Platform administrators always retain the tenant's available permissions.

The catalog covers viewing, metadata editing, media upload, pricing, review, publication, episode deletion and whole-series deletion separately. A view grant determines navigation; registered API operations also require their action grant. Shared read endpoints are deliberately authorized for their consumers, such as plan catalogs used by membership details and content selectors used by homepage groups. Default presets no longer imply that every staff account can read every administration API: an explicit view grant is required. Existing platform-only staff/key management and privileged financial writes cannot be granted by a content role.

India supports only its fixed content capabilities. JJTV action grants remain intersected with the connected upstream operator's authority. Both free_episode_count and member_window require pricing permission. PATCH requests send only authorized fields plus the upstream version, with no stale-snapshot exception. A role cannot create new upstream capabilities.

Role edits use a version check; a stale editor receives 409 and keeps its draft for refresh. Preset roles are editable but cannot be deleted. Assigned custom roles must be unassigned before deletion. Role/member changes and their audit record commit together. Effective permissions are read on every API request; switching tenants, focusing the window or using 刷新权限 refreshes the displayed menus. API keys require staff scope for tenant directory mutations and role/member administration; India connections remain interactive-session only.

Do not roll application code back to the fixed-role version after using custom roles or narrowing presets: that code ignores role_id and can broaden access. The migration refuses downgrade if it cannot preserve the configured authorization. Export and migrate permissions before such a rollback.

The local review environment uses a separate jjtv_tenancy_preview database and Redis DB 14. Destructive tests use jjtv_tenancy_test and Redis DB 15. They contain only preview/test data and do not deploy the feature to production.

Local role verification: the 31-file backend run covered 376 cases (373 passed; three legacy read-all/error-text assertions were updated to the new explicit-permission contract). The final nine-file regression run passed all 132 cases, including all 26 dedicated permission tests, API-key staff scope, scheduled supplier approval and the updated legacy cases. Admin TypeScript and Vite builds passed, as did six mocked workflow groups. Actual local Chrome verified role create/rename/delete, member role IDs/names, A uploader/B reviewer behavior and the disconnected India boundary; desktop and 390px views were inspected. This iteration was delivered as a local preview with no product deployment.

## Two-level admin navigation

The sidebar groups existing pages using the MiniTV reference navigation: 内容, 社区, 用户与金币, 商业化, 合作方 and 系统. 数据看板 remains the overview entry. Parent directories are display-only; existing route-based menu keys and action permissions remain authoritative. Unauthorized leaves and empty directories are hidden, and the current route opens its matching directory. Review-only drama details stay associated with the review menu.

India remains a content entry, platform tenant administration remains in 系统, and supplier accounts retain their independent pages under 合作方. The role editor displays the same parent/child menu grouping while preserving the permission catalog and its dependencies. Unsupported reference features do not receive placeholder routes. No permission migration or product deployment is required for this navigation change.

Navigation verification: TypeScript and Vite production build passed. The original 22 route leaves were preserved, including all 16 native server menu keys; native 35-item and India 7-item permission catalogs retained every key. Actual browser checks covered desktop expansion/collapse, breadcrumbs, the role editor grouping, the 390px drawer and leaf navigation, uploader/reviewer filtering, native review details and the India redirect boundary. No role permissions or business records were changed for this verification.
