import { createContext, useContext } from "react";

export type Me = {
  id: number;
  username: string;
  display_name: string;
  /** Global staff role; tenant content permissions never replace it. */
  role: string;
  platform_role?: string;
  permissions?: string[];
  menu_keys?: string[];
  tenant_role_name?: string;
  supplier?: { id: number; name: string; status: string; revenue_share_bps: number };
};

export const ROLE_LABELS: Record<string, string> = {
  ops: "运营",
  finance: "财务",
  admin: "管理员",
  partner: "上剧方",
};

export const ROLE_OPTIONS = [
  { value: "ops", label: "运营" },
  { value: "finance", label: "财务" },
  { value: "admin", label: "管理员" },
];

export const MeContext = createContext<Me | null>(null);

export function useMe(): Me | null {
  return useContext(MeContext);
}

export function hasPermission(me: Me | null, key: string): boolean {
  return !!me && ((me.platform_role ?? me.role) === "admin" || (me.permissions ?? []).includes(key));
}

export function usePermission(key: string): boolean {
  return hasPermission(useMe(), key);
}

export function useAnyPermission(keys: string[]): boolean {
  const me = useMe();
  return keys.some(key => hasPermission(me, key));
}

/** Metadata editing only: uploads, publishing and pricing use separate grants. */
export function useCanWriteContent(): boolean {
  return usePermission("dramas.edit");
}

/** Platform administration never follows a tenant role name. */
export function useIsAdmin(): boolean {
  const me = useMe();
  return (me?.platform_role ?? me?.role) === "admin";
}

export function useIsPartner(): boolean {
  const me = useMe();
  return (me?.platform_role ?? me?.role) === "partner";
}
