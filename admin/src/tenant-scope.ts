import { CURRENT_ENTRY, supportSession } from './entry';
export const DEFAULT_TENANT_ID = 'jjtv';
export const INDIA_TENANT_ID = 'india-short-drama';
let boundTenantId = CURRENT_ENTRY.kind === 'support' ? supportSession()?.tenant_id || '' : '';
export function getCurrentTenantId(): string { return boundTenantId; }
export function setCurrentTenantId(id: string): void { boundTenantId = id; }
export function tenantHeaders(path: string): Record<string, string> {
  if (!['tenant','support'].includes(CURRENT_ENTRY.kind) || /\/auth\//.test(path) || path.endsWith('/context')) return {};
  return boundTenantId ? { 'X-JJTV-Tenant': boundTenantId } : {};
}
