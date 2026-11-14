/** Independent surfaces at the measured 1024px boundary; all APIs and assets are fixtures. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";
import { createServer } from "vite";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const out =
  process.env.SHOTS_DIR || path.join(os.tmpdir(), "jjtv-surface-acceptance");
await fs.mkdir(out, { recursive: true });
const port = Number(process.env.SURFACE_ACCEPT_PORT || 5194),
  origin = "http://127.0.0.1:" + port;
const server = await createServer({
  root,
  server: { host: "127.0.0.1", port, strictPort: true },
  logLevel: "error",
});
await server.listen();
const chrome =
  process.env.CHROME_PATH ||
  (process.platform === "win32"
    ? "C:/Program Files/Google/Chrome/Application/chrome.exe"
    : process.platform === "darwin"
      ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      : "/usr/bin/google-chrome");
const browser = await puppeteer.launch({
  executablePath: chrome,
  headless: true,
  args: ["--no-first-run", "--no-default-browser-check"],
});
const page = await browser.newPage(),
  errors = [],
  unknown = [],
  calls = [],
  checks = [];
page.on("pageerror", (e) => errors.push(e.message));
const drama = {
  id: 1,
  title: "Surface fixture story",
  synopsis: "A browser-only fixture.",
  tags: [],
  episode_count: 2,
  published_episode_count: 2,
  free_episode_count: 0,
  cover_url: null,
  liked: false,
  favorited: false,
  like_count: 0,
  comment_count: 0,
};
const profile = (token) => ({
  id: token === "Bearer mobile-token" ? 2 : 1,
  public_id:
    token === "Bearer mobile-token" ? "mobile-viewer" : "desktop-viewer",
  email: "fixture@example.com",
  display_name:
    token === "Bearer mobile-token" ? "Mobile viewer" : "Desktop viewer",
  avatar_url: "",
  bio: "",
  coins: 100,
  following_count: 0,
  follower_count: 0,
  received_likes: 0,
  reminder_opt_in: false,
  membership: { active: false, plan_code: null, until: null, canceling: false },
});
await page.setRequestInterception(true);
page.on("request", async (req) => {
  const url = new URL(req.url());
  if (url.pathname.startsWith("/v1/")) {
    let body = {};
    try {
      body = JSON.parse(req.postData() || "{}");
    } catch {}
    const p = url.pathname;
    calls.push({
      path: p,
      query: url.search,
      headers: req.headers(),
      method: req.method(),
      body,
    });
    let result,
      status = 200;
    if (p === "/v1/home/groups")
      result = {
        groups: [
          {
            id: 1,
            name: "Fixture stories",
            style: "poster3",
            badge: "",
            position: 0,
            dramas: [drama],
          },
        ],
        banners: [],
      };
    else if (p === "/v1/config") result = { tabs: [], copies: {} };
    else if (p === "/v1/auth/providers")
      result = {
        email: { enabled: true },
        google: { enabled: false, client_id: null },
        facebook: { enabled: false, app_id: null },
      };
    else if (p === "/v1/me") {
      if (
        !["Bearer desktop-token", "Bearer mobile-token"].includes(
          req.headers().authorization,
        )
      ) {
        status = 401;
        result = { detail: "Sign in required" };
      } else result = profile(req.headers().authorization);
    } else if (p === "/v1/me/tasks" || p === "/v1/tasks")
      result = {
        today: "2026-11-14",
        balance: 100,
        checkin: { checked_today: false, streak: 0, amount: 10 },
        items: [],
      };
    else if (p === "/v1/me/messages") result = { items: [], unread: 0 };
    else if (
      p === "/v1/me/history" ||
      p === "/v1/me/favorites" ||
      p === "/v1/me/lists/like" ||
      p === "/v1/me/ledger"
    )
      result = { items: [], total: 0, limit: 20, offset: 0 };
    else if (p === "/v1/commerce/orders" || p === "/v1/me/orders")
      result = { items: [], has_more: false };
    else if (p === "/v1/search/trending") result = { items: ["Orange"] };
    else if (p === "/v1/search") result = { items: [drama] };
    else if (p === "/v1/catalog")
      result = { items: [drama], total: 1, limit: 24, offset: 0 };
    else if (p === "/v1/feed")
      result = {
        session_id: "fixture",
        items: [],
        has_more: false,
        next_cursor: null,
        page_size: 5,
        preload: { fetch_when_remaining: 2 },
      };
    else if (p === "/v1/dramas/1/play")
      result = {
        drama,
        items: [10, 11].map((id, i) => ({
          id,
          episode_number: i + 1,
          title: "Episode " + (i + 1),
          duration_seconds: 60,
          free: false,
          locked: true,
          progress_seconds: 0,
          comment_count: 0,
          unlock: { coin_price: 20 },
        })),
        resume: { episode_id: 10 },
        entitlement: { member: false, unlocked_episode_ids: [] },
      };
    else if (p === "/v1/plans")
      result = {
        billing_mode: "one_time",
        items: [
          {
            code: "weekly",
            name_en: "Weekly fixture",
            name_zh: "每周",
            price_cents: 499,
            currency: "USD",
            version: 1,
            version_id: 1,
            benefits: { items: ["Configured benefit"] },
            duration_days: 7,
            recurring_interval: null,
            billing_mode: "one_time",
          },
        ],
      };
    else if (p === "/v1/coin-packs")
      result = {
        items: [
          {
            code: "starter",
            name_en: "Starter fixture",
            name_zh: "金币",
            price_cents: 499,
            currency: "USD",
            base_coins: 500,
            bonus_coins: 50,
            coins: 550,
            version_id: 1,
          },
        ],
      };
    else if (p === "/v1/billing/channels")
      result = {
        items: [
          {
            code: "fixture",
            name: "Stripe fixture",
            provider: "stripe",
            enabled: true,
          },
        ],
      };
    else if (p === "/v1/attr/visit") result = { pid: body.pid, active: false };
    else {
      unknown.push(req.method() + " " + p);
      status = 404;
      result = { detail: "Unknown fixture route " + p };
    }
    await req.respond({
      status,
      contentType: "application/json",
      body: JSON.stringify(result),
    });
    return;
  }
  if (
    url.origin === origin ||
    url.protocol === "data:" ||
    url.protocol === "about:"
  )
    await req.continue();
  else {
    unknown.push("External " + url.origin);
    await req.abort();
  }
});
const pass = (label) => {
  checks.push(label);
  console.log("PASS " + label);
};
const surface = async (value) => {
  await page.waitForFunction(
    (v) =>
      document.documentElement.dataset.jjtvSurface === v &&
      document.querySelectorAll("style[data-jjtv-surface]").length === 1,
    {},
    value,
  );
  await page.waitForSelector(
    value === "mobile" ? "#app .stage" : ".consumer-header",
  );
};
const resize = async (width) => {
  await page.setViewport({ width, height: 900 });
};
const goto = async (hash) => {
  await page.goto(origin + "/" + hash, { waitUntil: "networkidle0" });
};
try {
  await resize(390);
  await goto("#home");
  await surface("mobile");
  assert.equal(
    await page.$eval("#app", (e) =>
      Math.round(e.getBoundingClientRect().width),
    ),
    390,
  );
  assert.equal(await page.$$(".consumer-header").then((x) => x.length), 0);
  assert.equal(await page.$$(".tabs [data-tab]").then((x) => x.length), 4);
  pass("390px loads the independent H5 stage and four bottom tabs");
  for (const width of [1000, 1023]) {
    await resize(width);
    await surface("mobile");
    assert.equal(
      await page.$eval("#app", (e) =>
        Math.round(e.getBoundingClientRect().width),
      ),
      480,
    );
  }
  pass("1000px and 1023px keep the centred 480px H5 surface");
  await resize(1024);
  await surface("desktop");
  assert.equal(await page.$$("#app").then((x) => x.length), 0);
  assert.equal(
    await page.$eval("body", (e) => getComputedStyle(e).minWidth),
    "1024px",
  );
  assert.equal(
    await page.$eval(
      ".consumer-header nav",
      (e) => getComputedStyle(e).display,
    ),
    "flex",
  );
  pass("1024px switches to the desktop root with desktop navigation");
  await resize(390);
  await surface("mobile");
  assert.equal(
    await page.$eval("body", (e) => getComputedStyle(e).minWidth),
    "0px",
  );
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  await resize(1440);
  await surface("desktop");
  assert.equal(
    (await page.$eval("body", (e) => getComputedStyle(e).overflowX)) !==
      "hidden",
    true,
  );
  pass(
    "repeated resize unloads the other surface stylesheet and restores document sizing",
  );
  await page.setUserAgent(
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
  );
  await goto("#home");
  await surface("desktop");
  await resize(390);
  await surface("mobile");
  pass("entry selection follows viewport width rather than user agent");
  await goto("#watch/1?episode=11");
  await surface("mobile");
  await page.waitForFunction(() =>
    document.querySelector(".back-title")?.textContent.includes("2"),
  );
  assert.equal(new URL(page.url()).hash, "#watch/1?episode=11");
  pass(
    "legacy watch links preserve an explicit episode instead of using the resume episode",
  );
  await goto("#search?q=Orange");
  await surface("mobile");
  await page.waitForSelector(".search .result");
  assert.equal(await page.$eval(".search input", (e) => e.value), "Orange");
  assert(
    calls.some(
      (c) =>
        c.path === "/v1/search" &&
        new URLSearchParams(c.query).get("q") === "Orange",
    ),
  );
  pass("search deep links populate the H5 input and issue the actual search");
  await resize(1440);
  await goto("#account");
  await page.evaluate(() => {
    localStorage.setItem("jjtv_pc_token", "desktop-token");
    localStorage.removeItem("minitv_h5_token");
  });
  await page.reload({ waitUntil: "networkidle0" });
  await surface("desktop");
  await page.waitForFunction(() =>
    document.body.innerText.includes("Desktop viewer"),
  );
  await resize(390);
  await surface("mobile");
  await page.waitForFunction(() =>
    document.querySelector(".me-hero h2")?.textContent.includes("Sign in"),
  );
  assert.equal(
    await page.evaluate(() => localStorage.getItem("jjtv_pc_token")),
    "desktop-token",
  );
  pass("desktop login does not silently sign into the independent H5 account");
  await page.evaluate(() =>
    localStorage.setItem("minitv_h5_token", "mobile-token"),
  );
  await page.reload({ waitUntil: "networkidle0" });
  await surface("mobile");
  await page.waitForFunction(
    () =>
      document.querySelector(".me-hero h2")?.textContent === "Mobile viewer",
  );
  await resize(1440);
  await surface("desktop");
  await page.waitForFunction(() =>
    document.body.innerText.includes("Desktop viewer"),
  );
  assert.equal(
    await page.evaluate(() => localStorage.getItem("minitv_h5_token")),
    "mobile-token",
  );
  pass(
    "switching back restores each surface own login without copying either token",
  );
  await resize(390);
  await goto("#account/favorites");
  await surface("mobile");
  await page.waitForFunction(
    () => document.querySelector(".me-tab.on")?.textContent.trim() === "Saved",
  );
  pass("account section deep links open the matching H5 library tab");
  await goto("#coins");
  await surface("desktop");
  await page.waitForSelector(".desktop-route-notice a");
  assert.equal(
    await page.$eval(
      ".consumer-header nav",
      (e) => getComputedStyle(e).display,
    ),
    "flex",
  );
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth >= 1024),
    true,
  );
  assert.equal(
    (await page.$eval(
      ".header-language",
      (e) => getComputedStyle(e).display,
    )) !== "none",
    true,
  );
  await page.click(".desktop-route-notice a");
  await surface("mobile");
  pass(
    "unsupported deep links keep the desktop canvas and expose an explicit mobile-home handoff",
  );
  await goto("?checkout=cancel#account/orders");
  await surface("desktop");
  await page.waitForFunction(
    () => !new URLSearchParams(location.search).has("checkout"),
  );
  assert.equal(new URL(page.url()).hash, "#account/orders");
  await resize(1440);
  await surface("desktop");
  await resize(390);
  await surface("desktop");
  pass(
    "desktop checkout return stays with its owning session through resize and query cleanup",
  );
  await resize(1440);
  await goto("?checkout=cancel&surface=h5#watch/1?episode=11");
  await surface("mobile");
  await page.waitForFunction(
    () => !new URLSearchParams(location.search).has("checkout"),
  );
  assert.equal(new URL(page.url()).hash, "#watch/1?episode=11");
  await page.waitForFunction(() =>
    document.querySelector(".back-title")?.textContent.includes("2"),
  );
  await resize(390);
  await surface("mobile");
  pass(
    "H5 checkout return keeps the mobile session and watch episode even above the desktop breakpoint",
  );
  await page.screenshot({ path: path.join(out, "independent-h5-390.png") });
  assert(
    calls.every(
      (c) =>
        !c.headers["x-device-id"]?.startsWith("h5-") ||
        c.headers.authorization !== "Bearer desktop-token",
    ),
  );
  assert(
    calls.every(
      (c) =>
        !c.headers["x-device-id"]?.startsWith("pc-") ||
        c.headers.authorization !== "Bearer mobile-token",
    ),
  );
  assert.deepEqual(errors, []);
  assert.deepEqual(unknown, []);
  pass(
    "all fixture requests keep device/token ownership; no unexpected APIs, providers or browser errors",
  );
  console.log(JSON.stringify({ passed: checks.length, screenshots: out }));
} finally {
  await browser.close();
  await server.close();
}
