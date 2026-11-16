/** Explicit local site configuration for the existing browser acceptance suites. */
export function siteBootstrapFixture(url, overrides = {}) {
  const origin = new URL(url).origin;
  const features = Object.fromEntries([
    "dramas", "users", "plans", "finance", "dashboard", "rewards", "comments",
    "support", "attribution", "coins", "membership", "library",
  ].map((key) => [key, true]));
  return {
    tenant: { id: "jjtv", code: "jjtv", name: "JJTV", target_markets: [],
      default_locale: "en", locales: ["en", "de", "fr", "es", "pt", "it", "ja", "ru", "zh"], currency: "USD", timezone: "UTC",
      web_url: origin, h5_url: origin, version: 1 },
    brand: { name: "JJTV", logo_url: "", favicon_url: "", primary_color: "#ff3347",
      background_color: "#07070a", font_family: "Inter", button_radius: 12,
      customer_email: "", social_links: {}, seo_title: "JJTV", seo_description: "" },
    brand_version: 1, features, company: { name: "Browser acceptance fixture" },
    ...overrides,
    features: { ...features, ...overrides.features },
  };
}

/** Accept a Playwright Route or Puppeteer HTTPRequest without real API traffic. */
export async function respondSiteBootstrap(target, overrides = {}) {
  const request = typeof target.request === "function" ? target.request() : target;
  if (new URL(request.url()).pathname !== "/v1/site/bootstrap") return false;
  const response = { status: 200, contentType: "application/json",
    headers: { "access-control-allow-origin": "*" },
    body: JSON.stringify(siteBootstrapFixture(request.url(), overrides)) };
  if (typeof target.fulfill === "function") await target.fulfill(response);
  else await request.respond(response);
  return true;
}
