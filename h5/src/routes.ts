export type MobileTab = "home" | "foryou" | "rewards" | "mylist" | "me";
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
  const section = parts[1] || (parts[0] === "mylist" ? "favorites" : "history");
  const library =
    section === "favorites" || section === "likes"
      ? section
      : params.get("library") || (params.get("return") || "").split("/")[1];
  const meTab: MeTab =
    library === "favorites" || library === "likes" ? library : "history";
  const sheets: Record<string, MeSheet> = {
    profile: "edit",
    ledger: "ledger",
    orders: "orders",
    messages: "messages",
    settings: "settings",
  };
  const premium = path === "premium" || path === "price";
  const tab: MobileTab =
    parts[0] === "mylist" ? "mylist" : parts[0] === "account" || parts[0] === "me" || premium
      ? "me"
      : path === "foryou" || path === "rewards"
        ? path
        : "home";
  const supported =
    ["", "home", "foryou", "rewards", "search", "premium"].includes(path) ||
    (path === "price" && params.get("tab") !== "coins") ||
    dramaId != null ||
    /^mylist(\/(history|favorites|likes))?$/.test(path) ||
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
    meSheet: parts[0] === "mylist" ? null : premium
      ? "premium"
      : params.get("sheet") === "signout" && section === "settings"
        ? "signout"
        : sheets[section] || null,
  };
}

/** Replace only the hash, preserving landing/checkout query parameters and router listeners. */
export function replaceMobileHash(hash: string) {
  const next = "#" + hash.replace(/^#/, "");
  if (window.location.hash === next) return;
  const oldURL = window.location.href;
  window.history.replaceState(
    window.history.state,
    "",
    window.location.pathname + window.location.search + next,
  );
  window.dispatchEvent(
    new HashChangeEvent("hashchange", { oldURL, newURL: window.location.href }),
  );
}
