/** Navigation folders never grant access. Only route leaves match server menu_keys. */
export type NavigationLeaf = {
  children?: never;
  key: string;
  label: string;
  audience?: "partner";
  platformAdminOnly?: boolean;
};
export type NavigationGroup = { key: string; label: string; children: NavigationLeaf[] };
/** A group can contain leaves only, keeping navigation at a maximum of two levels. */
export type NavigationNode = NavigationLeaf | NavigationGroup;
export function isNavigationGroup(node: NavigationNode): node is NavigationGroup {
  return Array.isArray(node.children);
}

export const NAVIGATION: NavigationNode[] = [
  { key: "/dashboard", label: "数据看板" },
  {
    key: "nav:content", label: "内容", children: [
      { key: "/dramas", label: "剧集管理" },
      { key: "/library", label: "选剧与授权" },
      { key: "/reviews", label: "剧集审核" },
      { key: "/groups", label: "首页分组" },
      { key: "/home-ops", label: "首页运营" },
      { key: "/tags", label: "标签库" },
      { key: "/india-content", label: "India 内容运营" },
    ],
  },
  {
    key: "nav:community", label: "社区", children: [
      { key: "/comments", label: "评论审核" },
      { key: "/moderation", label: "自动审核规则" },
    ],
  },
  {
    key: "nav:users", label: "用户与金币", children: [
      { key: "/users", label: "用户管理" },
      { key: "/rewards", label: "任务奖励" },
      {key:"/coin-ledger",label:"金币消耗"},
    ],
  },
  {
    key: "nav:monetization", label: "商业化", children: [
      { key: "/plans", label: "会员套餐" },
      {key:"/coin-packs",label:"金币商城"},
      {key:"/stripe-accounts",label:"支付通道"},
      { key: "/finance", label: "财务订单" },
    ],
  },
  {
    key:"nav:support",label:"客服",children:[{key:"/support/workbench",label:"工单工作台"},{key:"/support/queue",label:"工单队列"},{key:"/support/reports",label:"服务质量"},{key:"/support/settings",label:"工单设置"}],
  },
  {key:"nav:growth",label:"增长",children:[{key:"/attribution",label:"投放归因"}]},
  {
    key: "nav:partners", label: "合作方", children: [
      { key: "/suppliers", label: "上剧方管理" },
      { key: "/settlements", label: "分成结算" },
      { key: "/partner/dramas", label: "我的剧集", audience: "partner" },
      { key: "/partner/stats", label: "数据中心", audience: "partner" },
      { key: "/partner/settlements", label: "结算单", audience: "partner" },
      { key: "/partner/api-keys", label: "API 密钥", audience: "partner" },
    ],
  },
  {
    key: "nav:system", label: "系统", children: [
      { key: "/tenants", label: "租户管理", platformAdminOnly: true },
      { key: "/site-settings", label: "站点设置" },
      { key: "/brand", label: "品牌与样式" },
      { key: "/merchant-binding", label: "本站收款账号" },
      { key: "/staff", label: "员工账号" },
      { key: "/roles", label: "角色与权限" },
      { key: "/api-keys", label: "API 密钥" },
      {key:"/login-settings",label:"登录配置"},
    ],
  },
];

export function navigationLeaves(nodes: NavigationNode[] = NAVIGATION): NavigationLeaf[] {
  return nodes.flatMap(node => isNavigationGroup(node) ? node.children : [node]);
}

export function navigationPath(key: string, nodes: NavigationNode[] = NAVIGATION): { leaf: NavigationLeaf; parent?: NavigationGroup } | undefined {
  for (const node of nodes) {
    if (isNavigationGroup(node)) {
      const leaf = node.children.find(item => item.key === key);
      if (leaf) return { leaf, parent: node };
    } else if (node.key === key) return { leaf: node };
  }
}

export function visibleNavigation({
  menuKeys, partner, platformAdmin, india, tenantAdmin = false,
}: { menuKeys: string[]; partner: boolean; platformAdmin: boolean; india: boolean; tenantAdmin?: boolean }): NavigationNode[] {
  const allowed = new Set(menuKeys);
  const visible = (leaf: NavigationLeaf) => {
    if (partner) return leaf.audience === "partner" && allowed.has(leaf.key);
    if (leaf.audience === "partner") return false;
    if (leaf.platformAdminOnly) return platformAdmin;
    if (["/staff", "/roles", "/api-keys", "/site-settings", "/brand", "/merchant-binding"].includes(leaf.key)) return tenantAdmin;
    if (india && leaf.key !== "/india-content") return false;
    if (!india && leaf.key === "/india-content") return false;
    return allowed.has(leaf.key);
  };
  return NAVIGATION.flatMap<NavigationNode>(node => {
    if (!(isNavigationGroup(node))) return visible(node) ? [node] : [];
    const children = node.children.filter(visible);
    return children.length ? [{ ...node, children }] : [];
  });
}

export function selectedNavigation(pathname: string, nodes: NavigationNode[], reviewDetail = false) {
  const leaves = navigationLeaves(nodes);
  if (reviewDetail && !leaves.some(leaf => leaf.key === "/dramas") && leaves.some(leaf => leaf.key === "/reviews")) {
    return navigationPath("/reviews", nodes);
  }
  const leaf = [...leaves].sort((a, b) => b.key.length - a.key.length)
    .find(item => pathname === item.key || pathname.startsWith(item.key + "/"));
  return leaf ? navigationPath(leaf.key, nodes) : undefined;
}
