export type TenantRole = { id: string; name: string; permissions: string[]; version: number; builtin_key: string | null };
export type PermissionEntry = { key: string; label: string; group: string; requires: string[]; menu_key?: string };
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
