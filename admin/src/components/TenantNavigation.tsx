import { useEffect, useState } from "react";
import { Menu } from "antd";
import type { MenuProps } from "antd";
import { isNavigationGroup, navigationLeaves, navigationPath, type NavigationNode } from "../navigation";

export default function TenantNavigation({
  items, selected, route, scope, onNavigate,
}: { items: NavigationNode[]; selected?: string; route: string; scope: string; onNavigate: (key: string) => void }) {
  const parent = selected ? navigationPath(selected, items)?.parent?.key : undefined;
  const groups = items.filter(item => isNavigationGroup(item)).map(item => item.key).join(",");
  const [openKeys, setOpenKeys] = useState<string[]>(parent ? [parent] : []);
  // A new route or tenant reveals its current location. Between route changes,
  // onOpenChange lets the user freely expand or collapse any visible folder.
  useEffect(() => { setOpenKeys(parent ? [parent] : []); }, [parent, route, scope, groups]);
  const menuItems: MenuProps["items"] = items.map(item => isNavigationGroup(item)
    ? { key: item.key, label: item.label, children: item.children.map(leaf => ({ key: leaf.key, label: leaf.label })) }
    : { key: item.key, label: item.label });
  return <Menu
    mode="inline"
    aria-label="后台导航"
    style={{ borderInlineEnd: "none" }}
    selectedKeys={selected ? [selected] : []}
    openKeys={openKeys}
    onOpenChange={setOpenKeys}
    items={menuItems}
    onClick={({ key }) => {
      if (navigationLeaves(items).some(leaf => leaf.key === key)) onNavigate(key);
    }}
  />;
}
