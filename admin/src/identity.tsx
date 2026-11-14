import { createContext, useContext } from 'react';
export type IdentityUser = { id: number; username: string; display_name: string; role: string; account_scope: string; tenant_id: string | null; must_change_password: boolean };
export type BusinessTenant = { id: string; code: string; name: string; kind: 'native' | 'india_legacy'; status: 'active' | 'paused'; version: number; target_markets?: string[]; default_locale?: string; locales?: string[]; currency?: string; timezone?: string; web_url?: string; h5_url?: string };
export type Identity = { user: IdentityUser; account_scope: string; session_kind: 'normal' | 'support' | 'api_key'; tenant: BusinessTenant | null; is_platform_admin: boolean; is_tenant_admin: boolean; enabled_modules: string[]; permissions: string[]; menu_keys: string[]; role_name: string };
export type LoginResult = { token: string; context: Identity };
export const IdentityContext = createContext<Identity | null>(null);
export const useIdentity = () => useContext(IdentityContext);
