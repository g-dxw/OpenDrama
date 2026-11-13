import { createContext, useContext } from "react";

export type Tenant = {
  id: string;
  name: string;
  kind: "native" | "india_legacy";
  status: "active" | "paused";
  role?: string;
  own_role?: string;
  ownrole?: string;
};
export type TenantState = {
  items: Tenant[];
  current: Tenant | undefined;
  refresh: () => Promise<void>;
  select: (id: string) => void;
};
export const TenantContext = createContext<TenantState>({
  items: [], current: undefined, refresh: async () => {}, select: () => {},
});
export function useTenant() { return useContext(TenantContext); }
