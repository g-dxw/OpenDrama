const CURRENT_TENANT_KEY = "jjtv_admin_tenant";
export const DEFAULT_TENANT_ID = "jjtv";
export const INDIA_TENANT_ID = "india-short-drama";

export function getCurrentTenantId(): string {
  return localStorage.getItem(CURRENT_TENANT_KEY) || DEFAULT_TENANT_ID;
}
export function setCurrentTenantId(id: string): void {
  localStorage.setItem(CURRENT_TENANT_KEY, id);
}
export function tenantHeaders(path: string): Record<string, string> {
  if (/\/(?:auth|auth\/me)\//.test(path) || /\/auth\/me$/.test(path) ||
      /^\/admin\/v1\/tenants(?:\?|$)/.test(path)) return {};
  return { "X-JJTV-Tenant": getCurrentTenantId() };
}
