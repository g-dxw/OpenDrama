/** Resolve the product once so rendering and feature checks agree. */
export function resolveStoreRoute(path: string, tab: string | null, features: { coins: boolean; membership: boolean }, hasMembership = false) {
  const kind = path === "coins" || tab === "coins" || (path === "price" && !features.membership && features.coins) ? "coin_pack" : "membership";
  return { kind, available: kind === "coin_pack" ? features.coins : features.membership || hasMembership } as const;
}
