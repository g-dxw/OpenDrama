export type Entry = { kind: 'choose' | 'platform' | 'tenant' | 'support'; base: string; code?: string };
export function readEntry(path = window.location.pathname): Entry {
  if (/^\/admin\/platform(?:\/|$)/.test(path)) return { kind: 'platform', base: '/admin/platform' };
  const match = path.match(/^\/admin\/(t|s)\/([^/]+)(?:\/|$)/);
  if (match) {
    try {
      const code = decodeURIComponent(match[2]);
      if (/^[a-z0-9][a-z0-9-]{1,63}$/.test(code)) return { kind: match[1] === 't' ? 'tenant' : 'support', code, base: '/admin/' + match[1] + '/' + encodeURIComponent(code) };
    } catch { /* Invalid path returns to the entry picker. */ }
  }
  return { kind: 'choose', base: path.startsWith('/admin') ? '/admin' : '' };
}
export const CURRENT_ENTRY = readEntry();
export function tenantEntryUrl(code: string, support = false): string { return '/admin/' + (support ? 's' : 't') + '/' + encodeURIComponent(code); }
export function loginUrl(): string {
  if (CURRENT_ENTRY.kind === 'support') return '/admin/platform/tenants';
  return CURRENT_ENTRY.kind === 'choose' ? '/admin/login' : CURRENT_ENTRY.base + '/login';
}
export function contextApi(): string { return CURRENT_ENTRY.kind === 'platform' ? '/platform/v1' : '/tenant/v1'; }
export const PLATFORM_TOKEN_KEY = 'jjtv_platform_token';
const key = () => CURRENT_ENTRY.kind === 'platform' ? PLATFORM_TOKEN_KEY : 'jjtv_' + CURRENT_ENTRY.kind + '_token:' + CURRENT_ENTRY.code;
export type SupportSession = { token: string; expires_at?: string; tenant_id?: string; operator?: string };
export function supportSession(): SupportSession | null {
  if (CURRENT_ENTRY.kind !== 'support') return null;
  try { return JSON.parse(sessionStorage.getItem(key()) || 'null'); } catch { return null; }
}
export function entryToken(): string | null {
  if (CURRENT_ENTRY.kind === 'choose') return null;
  return CURRENT_ENTRY.kind === 'support' ? supportSession()?.token || null : localStorage.getItem(key());
}
export function storeToken(token: string): void {
  if (CURRENT_ENTRY.kind === 'choose') return;
  if (CURRENT_ENTRY.kind === 'support') sessionStorage.setItem(key(), JSON.stringify({ ...supportSession(), token }));
  else localStorage.setItem(key(), token);
}
export function removeToken(): void {
  if (CURRENT_ENTRY.kind === 'choose') return;
  (CURRENT_ENTRY.kind === 'support' ? sessionStorage : localStorage).removeItem(key());
}
export function storeSupport(code: string, session: SupportSession): void { sessionStorage.setItem('jjtv_support_token:' + code, JSON.stringify(session)); }
