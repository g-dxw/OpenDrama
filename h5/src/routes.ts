export type MobileTab = "home" | "foryou" | "rewards" | "me";
export type MeTab = "history" | "favorites" | "likes";
export type MeSheet =
  | "edit"
  | "ledger"
  | "messages"
  | "settings"
  | "premium"
  | "orders"
  | "signout";
export function readMobileRoute(hash = window.location.hash) {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?");
  const params = new URLSearchParams(query),
    parts = path.split("/");
  const candidate =
    parts[0] === "watch" && /^[1-9]\d*$/.test(parts[1] || "")
      ? Number(parts[1])
      : null;
  const dramaId =
    candidate != null && Number.isSafeInteger(candidate) ? candidate : null;
  const episode = Number(params.get("episode"));
  const section = parts[1] || "history";
  const meTab: MeTab =
    section === "favorites" || section === "likes" ? section : "history";
  const sheets: Record<string, MeSheet> = {
    profile: "edit",
    ledger: "ledger",
    orders: "orders",
    messages: "messages",
    settings: "settings",
  };
  const premium = path === "premium" || path === "price";
  const tab: MobileTab =
    parts[0] === "account" || parts[0] === "me" || premium
      ? "me"
      : path === "foryou" || path === "rewards"
        ? path
        : "home";
  const supported =
    ["", "home", "foryou", "rewards", "search", "premium"].includes(path) ||
    (path === "price" && params.get("tab") !== "coins") ||
    dramaId != null ||
    /^(account|me)(\/(history|favorites|likes|profile|ledger|orders|messages|settings))?$/.test(
      path,
    );
  return {
    supported,
    tab,
    dramaId,
    episodeId:
      Number.isSafeInteger(episode) && episode > 0 ? episode : undefined,
    searching: path === "search",
    query: params.get("q") || "",
    meTab,
    meSheet: premium ? "premium" : sheets[section] || null,
  };
}
