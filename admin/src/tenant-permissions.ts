import { NAVIGATION, navigationLeaves, navigationPath } from "./navigation";

export type TenantRole = { id: string; name: string; permissions: string[]; version: number; builtin_key: string | null; kind?: 'business' | 'tenant_admin' };
export type PermissionEntry = { key: string; label: string; group: string; requires: string[]; enabled?: boolean; menu_key?: string };
export type TenantRolesPayload = { roles: TenantRole[]; catalog: PermissionEntry[] };
export function changePermission(current: string[], key: string, checked: boolean, catalog: PermissionEntry[]): string[] {
  const entries = new Map(catalog.map(entry => [entry.key, entry]));
  const selected = new Set(current);
  if (checked) {
    const visit = (value: string) => {
      if (selected.has(value)) return;
      selected.add(value); (entries.get(value)?.requires ?? []).forEach(visit);
    };
    visit(key);
  } else {
    selected.delete(key);
    let changed = true;
    while (changed) {
      changed = false;
      for (const value of selected) if ((entries.get(value)?.requires ?? []).some(required => !selected.has(required))) {
        selected.delete(value); changed = true;
      }
    }
  }
  return [...selected].sort();
}

/** Render catalog modules under the same folders and route labels as navigation. */
export type PermissionSection = {
  key: string; title: string;
  modules: { key: string; title: string; entries: PermissionEntry[] }[];
};

export function permissionSections(catalog: PermissionEntry[]): PermissionSection[] {
  const entries = new Map(catalog.map(entry => [entry.key, entry]));
  const routes: Record<string, string> = {
    dashboard: "/dashboard", dramas: "/dramas", episodes: "/dramas", reviews: "/reviews",
    groups: "/groups", home_ops: "/home-ops", tags: "/tags", comments: "/comments",
    moderation: "/moderation", users: "/users", rewards: "/rewards", plans: "/plans",
    finance: "/finance", suppliers: "/suppliers", settlements: "/settlements",
    staff: "/staff", api_keys: "/api-keys",
  };
  const routeFor = (key: string, visited = new Set<string>()): string | undefined => {
    if (visited.has(key)) return;
    visited.add(key);
    const entry = entries.get(key);
    if (entry?.menu_key) return entry.menu_key;
    for (const required of entry?.requires ?? []) {
      const inherited = routeFor(required, visited);
      if (inherited) return inherited;
    }
    const prefix = key.split(".")[0];
    return routes[prefix];
  };
  const result = new Map<string, PermissionSection>();
  for (const entry of catalog) {
    const route = routeFor(entry.key);
    const location = route ? navigationPath(route) : undefined;
    const sectionKey = location?.parent?.key ?? location?.leaf.key ?? "catalog:other";
    const sectionTitle = location?.parent?.label ?? location?.leaf.label ?? "其它权限";
    let section = result.get(sectionKey);
    if (!section) { section = { key: sectionKey, title: sectionTitle, modules: [] }; result.set(sectionKey, section); }
    const moduleKey = (route ?? "unmapped") + ":" + (entry.group || "未分类");
    let module = section.modules.find(item => item.key === moduleKey);
    if (!module) {
      const title = location?.leaf.label
        ? location.leaf.label
        : entry.group || "未分类权限";
      module = { key: moduleKey, title, entries: [] }; section.modules.push(module);
    }
    module.entries.push(entry);
  }
  const order = NAVIGATION.map(node => node.key);
  const leaves = navigationLeaves().map(leaf => leaf.key);
  const rank = (values: string[], key: string) => { const index = values.indexOf(key); return index < 0 ? values.length : index; };
  return [...result.values()].sort((a, b) => rank(order, a.key) - rank(order, b.key)).map(section => ({
    ...section,
    modules: section.modules.sort((a, b) => rank(leaves, a.key.split(":")[0]) - rank(leaves, b.key.split(":")[0])),
  }));
}
