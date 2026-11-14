export type SiteBrand = {
  name: string;
  logo_url: string;
  favicon_url: string;
  primary_color: string;
  background_color: string;
  font_family: string;
  button_radius: number;
  customer_email: string;
  social_links: Record<string, string>;
  seo_title: string;
  seo_description: string;
};
export type SiteConfig = {
  id: string;
  code: string;
  name: string;
  target_markets: string[];
  default_locale: string;
  locales: string[];
  currency: string;
  timezone: string;
  web_url: string;
  h5_url: string;
  version?: number;
  features?: Record<string, boolean>;
};
export type SiteBootstrap = {
  tenant: SiteConfig;
  brand: SiteBrand;
  brand_version: number;
  features: Record<string, boolean>;
  company: { name: string };
  preview?: boolean;
};
let current: SiteBootstrap | null = null;
export function getSite(): SiteBootstrap {
  if (!current) throw new Error("Site configuration is not loaded.");
  return current;
}
export function contrastText(hex: string) {
  const rgb = (hex.match(/[a-f0-9]{2}/gi) || ["00", "00", "00"])
    .map((v) => parseInt(v, 16) / 255)
    .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2] > 0.179
    ? "#111111"
    : "#ffffff";
}
export function siteName() {
  return current?.brand.name || current?.tenant.name || "MiniTV";
}
export function siteFeature(key: string) {
  return current?.features[key] === true;
}
export function siteStorageKey(legacy: string) {
  return current?.tenant.id === "jjtv"
    ? legacy
    : "site:" + current?.tenant.id + ":" + legacy;
}
export function siteLocale() {
  return current?.tenant.default_locale || "en";
}
export function siteMoney(
  cents: number,
  locale = siteLocale(),
  currency = current?.tenant.currency || "USD",
) {
  return new Intl.NumberFormat(locale, { style: "currency", currency }).format(
    cents /
      10 **
        (new Intl.NumberFormat(locale, {
          style: "currency",
          currency,
        }).resolvedOptions().maximumFractionDigits ?? 2),
  );
}
export function siteDate(
  value: string | number | Date,
  locale = siteLocale(),
  options: Intl.DateTimeFormatOptions = {
    dateStyle: "medium",
    timeStyle: "short",
  },
) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : new Intl.DateTimeFormat(locale, {
        ...options,
        timeZone: current?.tenant.timezone || "UTC",
      }).format(date);
}
export function siteLink(kind: "web" | "h5") {
  return (
    (kind === "web" ? current?.tenant.web_url : current?.tenant.h5_url) ||
    window.location.origin
  );
}
function safeImage(value: string) {
  try {
    const url = new URL(value, window.location.origin);
    return ["https:", "http:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}
export async function loadSite(apiBase = ""): Promise<SiteBootstrap> {
  const params = new URLSearchParams(window.location.search);
  const preview = params.get("preview_token");
  const res = await fetch(
    apiBase +
      "/v1/site/bootstrap" +
      (preview ? "?preview_token=" + encodeURIComponent(preview) : ""),
    { credentials: "same-origin", cache: "no-store" },
  );
  if (!res.ok)
    throw new Error(
      res.status === 404
        ? "This site is not configured."
        : "Could not load this site. Please retry.",
    );
  const payload = (await res.json()) as SiteBootstrap;
  if (!payload.tenant?.id || !payload.brand || !payload.features)
    throw new Error("Invalid site configuration.");
  current = payload;
  const root = document.documentElement,
    brand = payload.brand;
  root.dataset.site = payload.tenant.id;
  root.lang = payload.tenant.default_locale;
  for (const [key, on] of Object.entries(payload.features))
    root.setAttribute("data-feature-" + key, on ? "on" : "off");
  const style = root.style;
  style.setProperty("--site-primary-text", contrastText(brand.primary_color));
  for (const key of [
    "--pc-brand",
    "--pc-brand-hover",
    "--pc-cta",
    "--pc-cta-hover",
    "--red",
  ])
    style.setProperty(key, brand.primary_color);
  for (const key of ["--pc-bg", "--bg"])
    style.setProperty(key, brand.background_color);
  style.setProperty(
    "--site-font",
    `${brand.font_family === "System" ? "system-ui" : JSON.stringify(brand.font_family || "Inter")}, system-ui, sans-serif`,
  );
  style.setProperty(
    "--site-button-radius",
    String(brand.button_radius ?? 12) + "px",
  );
  document.title = brand.seo_title || siteName();
  let description = document.querySelector<HTMLMetaElement>(
    'meta[name="description"]',
  );
  if (!description) {
    description = document.createElement("meta");
    description.name = "description";
    document.head.appendChild(description);
  }
  description.content = brand.seo_description || "";
  if (brand.favicon_url) {
    let favicon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (!favicon) {
      favicon = document.createElement("link");
      favicon.rel = "icon";
      document.head.appendChild(favicon);
    }
    favicon.href = safeImage(brand.favicon_url);
  }
  return payload;
}
export function showSiteFailure(error: unknown, retry: () => void) {
  document.title = "Site unavailable";
  const root = document.getElementById("root");
  if (!root) return;
  root.replaceChildren();
  const box = document.createElement("div");
  box.setAttribute("role", "alert");
  box.style.cssText =
    "max-width:520px;margin:15vh auto;padding:28px;font:16px/1.6 system-ui;color:#eee;background:#171013;border-radius:16px";
  const title = document.createElement("h1");
  title.textContent = "Site unavailable / 站点暂不可用";
  title.style.fontSize = "24px";
  const detail = document.createElement("p");
  detail.textContent = error instanceof Error ? error.message : String(error);
  const button = document.createElement("button");
  button.textContent = "Retry / 重试";
  button.onclick = retry;
  button.style.cssText = "padding:12px 24px;cursor:pointer";
  box.append(title, detail, button);
  root.appendChild(box);
}
