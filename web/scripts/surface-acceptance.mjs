import { respondSiteBootstrap } from "../../tests/frontend-site-fixture.mjs";
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
const delayedResponses = new Map();
const server = await createServer({
  root,
  plugins: [
    {
      name: "surface-held-http",
      configureServer(server) {
        server.middlewares.use((request, response, next) => {
          if (
            ![
              "/v1/commerce/checkout",
              "/v1/billing/cancel",
              "/v1/dramas/1/play",
              "/v1/playback",
            ].includes(new URL(request.url, "http://fixture").pathname) &&
            !/^\/v1\/commerce\/orders\/[^/]+$/.test(
              new URL(request.url, "http://fixture").pathname,
            )
          ) {
            next();
            return;
          }
          const key =
              request.headers["x-surface-document"] +
              ":" +
              request.headers["x-surface-request"],
            held = delayedResponses.get(key);
          if (!held) {
            response.statusCode = 500;
            response.end("Unregistered fixture request");
            return;
          }
          request.resume();
          held.notify();
          response.on("finish", () =>
            held.completeNetwork("finished", "server_response_finished"),
          );
          response.on("close", () => {
            if (!response.writableEnded) {
              held.completeNetwork("failed", "client_connection_closed");
              held.handled();
            }
          });
          void held.gate.then(() => {
            try {
              if (response.destroyed) return;
              if (held.delivery === "abort") {
                response.destroy();
                return;
              }
              response.setHeader("Content-Type", "application/json");
              response.end(JSON.stringify(held.result));
            } finally {
              held.handled();
              delayedResponses.delete(key);
            }
          });
        });
      },
    },
  ],
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
  ignoreDefaultArgs: ["--hide-scrollbars"],
  args: [
    "--no-first-run",
    "--no-default-browser-check",
    "--autoplay-policy=no-user-gesture-required",
  ],
});
const page = await browser.newPage(),
  errors = [],
  unknown = [],
  calls = [],
  checks = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.evaluateOnNewDocument(() => {
  // Keep the parallel patch counter scoped to its own document across reloads.
  const tracker = { pending: 0 };
  window.__jjtvFetchTracker = tracker;
  window.__jjtvPendingFetches = 0;
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    tracker.pending++;
    if (window.__jjtvFetchTracker === tracker)
      window.__jjtvPendingFetches = tracker.pending;
    try {
      const response = await originalFetch(...args);
      await response.clone().arrayBuffer();
      return response;
    } finally {
      await new Promise((resolve) => setTimeout(resolve, 0));
      tracker.pending--;
      if (window.__jjtvFetchTracker === tracker)
        window.__jjtvPendingFetches = tracker.pending;
    }
  };
});
let activeMember = false,
  membershipEnabled = true,
  playableFirst = false,
  media = Buffer.alloc(0),
  checkoutHold = null,
  cancelHold = null,
  memberAccess = false,
  memberHold = null,
  memberProfileBarrier = null,
  orderHold = null,
  epayAvailable = false,
  creemAvailable = false;
const recoveryReplies = [];
const checkoutFacts = new Map();
const pending = [],
  inFlight = new Set();
page.on("requestfinished", (request) => inFlight.delete(request));
page.on("requestfailed", (request) => inFlight.delete(request));
// Observe the real application fetch and its JSON consumption without replacing its result.
// A document id distinguishes a settled old request from an optimizer-triggered reload.
await page.evaluateOnNewDocument(() => {
  const nativeFetch = window.fetch.bind(window);
  const trace = { documentId: crypto.randomUUID(), requests: {} };
  let sequence = 0;
  window.__surfaceFetch = trace;
  window.fetch = async (input, init) => {
    const url = new URL(
      input instanceof Request ? input.url : String(input),
      location.href,
    );
    if (
      ![
        "/v1/plans",
        "/v1/commerce/checkout",
        "/v1/billing/cancel",
        "/v1/dramas/1/play",
        "/v1/playback",
      ].includes(url.pathname) &&
      !/^\/v1\/commerce\/orders\/[^/]+$/.test(url.pathname)
    )
      return nativeFetch(input, init);
    const id = String(++sequence),
      headers = new Headers(init?.headers);
    headers.set("X-Surface-Request", id);
    headers.set("X-Surface-Document", trace.documentId);
    const record = (trace.requests[id] = { phase: "pending" });
    try {
      const response = await nativeFetch(input, { ...init, headers }),
        readJson = response.json.bind(response);
      response.json = async () => {
        try {
          const result = await readJson();
          record.phase = "body_finished";
          return result;
        } catch (error) {
          record.phase = "body_failed";
          throw error;
        }
      };
      return response;
    } catch (error) {
      record.phase = "failed";
      throw error;
    }
  };
});
function hold(kind, order = "H5-ORDER", delivery = "respond") {
  let release, notify, handled;
  const gate = new Promise((resolve) => {
      release = resolve;
    }),
    started = new Promise((resolve) => {
      notify = resolve;
    }),
    fixtureDone = new Promise((resolve) => {
      handled = resolve;
    });
  const item = {
    release,
    notify,
    gate,
    started,
    order,
    delivery,
    kind,
    fixtureDone,
    handled,
  };
  pending.push(item);
  if (kind === "checkout") checkoutHold = item;
  else if (kind === "cancel") cancelHold = item;
  else if (kind === "order") orderHold = item;
  else memberHold = item;
  return item;
}
async function fulfillHeld(request, item, result) {
  assert.equal(
    new URL(request.url()).origin,
    origin,
    "held HTTP fixtures must stay local",
  );
  item.request = request;
  item.documentId = request.headers()["x-surface-document"];
  item.requestId = request.headers()["x-surface-request"];
  assert(
    item.documentId && item.requestId,
    "held call must be the observed application fetch",
  );
  item.state = "pending";
  item.networkDone = new Promise((resolve) => {
    const finish = (completed) => {
      if (completed === request) done("finished", "browser_requestfinished");
    };
    const fail = (failed) => {
      if (failed === request) done("failed", "browser_requestfailed");
    };
    const done = (state, evidence) => {
      if (item.state !== "pending") return;
      item.state = state;
      item.evidence = evidence;
      item.failure = request.failure()?.errorText;
      page.off("requestfinished", finish);
      page.off("requestfailed", fail);
      resolve();
    };
    item.completeNetwork = done;
    page.on("requestfinished", finish);
    page.on("requestfailed", fail);
  });
  // Continue into a real delayed HTTP response. Keeping CDP interception paused
  // across document disposal can orphan the request without any terminal event.
  item.result = result;
  delayedResponses.set(item.documentId + ":" + item.requestId, item);
  await request.continue();
}
async function settleHeld(item) {
  // Subscribe when the request is first intercepted, before the sheet can unmount.
  // Chromium may finish OR cancel it; neither terminal event requires a response event.
  item.release();
  let timer;
  try {
    await Promise.race([
      Promise.all([item.networkDone, item.fixtureDone]),
      new Promise((_, reject) => {
        timer = setTimeout(
          () =>
            reject(
              new Error(
                "No terminal network event for held " +
                  item.order +
                  " (" +
                  item.state +
                  ")",
              ),
            ),
          10000,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
  await page.waitForFunction(
    async ({ documentId, requestId }) => {
      const trace = window.__surfaceFetch;
      if (!trace) return false;
      if (
        trace.documentId === documentId &&
        !["body_finished", "body_failed", "failed"].includes(
          trace.requests[requestId]?.phase,
        )
      )
        return false;
      const currentDocument = trace.documentId;
      await new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      );
      return window.__surfaceFetch?.documentId === currentDocument;
    },
    { timeout: 10000 },
    { documentId: item.documentId, requestId: item.requestId },
  );
  const client = await page.evaluate(
    ({ documentId, requestId }) => ({
      documentChanged: window.__surfaceFetch.documentId !== documentId,
      phase:
        window.__surfaceFetch.documentId === documentId
          ? window.__surfaceFetch.requests[requestId]?.phase
          : "old_document_disposed",
      surface: document.documentElement.dataset.jjtvSurface,
    }),
    { documentId: item.documentId, requestId: item.requestId },
  );
  console.log(
    "TRACE held request " +
      JSON.stringify({
        order: item.order,
        network: item.state,
        evidence: item.evidence,
        failure: item.failure,
        ...client,
        pending: [...inFlight].map(
          (request) => new URL(request.url()).pathname,
        ),
      }),
  );
}
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
  membership: {
    active: activeMember,
    plan_code: activeMember ? "weekly" : null,
    until: activeMember ? "2026-12-14T00:00:00Z" : null,
    canceling: false,
    billing_mode: activeMember ? "subscription" : null,
  },
});
await page.setRequestInterception(true);
page.on("request", async (req) => {
  if (await respondSiteBootstrap(req, { features: { membership: membershipEnabled } })) return;
  inFlight.add(req);
  const url = new URL(req.url());
  if (url.pathname === "/fixtures/clip.webm") {
    await req.respond({ status: 200, contentType: "video/webm", body: media });
    return;
  }
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
    else if (p === "/v1/auth/email/code") {
      assert.equal(body.email, "member@example.com");
      result = { sent: true };
    } else if (p === "/v1/auth/email/authenticate") {
      assert.equal(body.email, "member@example.com");
      assert.equal(body.code, "123456");
      result = {
        token: "mobile-member-token",
        user_id: 3,
        public_id: "member-viewer",
      };
    } else if (p === "/v1/me") {
      if (
        req.headers().authorization === "Bearer mobile-member-token" &&
        memberProfileBarrier
      ) {
        const barrier = memberProfileBarrier;
        memberProfileBarrier = null;
        await barrier.started;
        status = 401;
        result = { detail: "Expired member session" };
        await req.respond({
          status,
          contentType: "application/json",
          body: JSON.stringify(result),
        });
        return;
      }
      if (
        ![
          "Bearer desktop-token",
          "Bearer mobile-token",
          "Bearer mobile-token-b",
          "Bearer mobile-member-token",
        ].includes(req.headers().authorization)
      ) {
        status = 401;
        result = { detail: "Sign in required" };
      } else {
        result = profile(req.headers().authorization);
        if (req.headers().authorization === "Bearer mobile-member-token") {
          result.id = 3;
          result.display_name = "Member viewer";
          result.membership = {
            active: true,
            plan_code: "weekly",
            until: "2026-12-14T00:00:00Z",
            canceling: false,
            billing_mode: "one_time",
          };
        }
      }
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
    else if (p === "/v1/dramas/1/play") {
      const member =
        memberAccess &&
        req.headers().authorization === "Bearer mobile-member-token";
      result = {
        drama,
        items: [10, 11].map((id, i) => ({
          id,
          episode_number: i + 1,
          title: "Episode " + (i + 1),
          duration_seconds: 60,
          free: playableFirst && i === 0,
          locked: !member && !(playableFirst && i === 0),
          membership_required: memberAccess && !member,
          progress_seconds: 0,
          comment_count: 0,
          unlock: { coin_price: 20 },
        })),
        resume: { episode_id: 10 },
        entitlement: { member, unlocked_episode_ids: [] },
      };
      if (member && memberHold?.kind === "access") {
        const held = memberHold;
        memberHold = null;
        await fulfillHeld(req, held, result);
        return;
      }
    } else if (p === "/v1/playback") {
      const allowed =
        !memberAccess ||
        req.headers().authorization === "Bearer mobile-member-token" ||
        (playableFirst && body.episode_id === 10);
      result = {
        allowed,
        url: allowed ? origin + "/fixtures/clip.webm" : null,
        episode_id: body.episode_id,
      };
      if (allowed && memberHold?.kind === "media") {
        const held = memberHold;
        memberHold = null;
        await fulfillHeld(req, held, result);
        return;
      }
    } else if (/^\/v1\/playback\/(start|progress|complete)$/.test(p))
      result = { ok: true };
    else if (
      /^\/v1\/commerce\/orders\/[^/]+$/.test(p) &&
      req.method() === "GET"
    ) {
      const fact = checkoutFacts.get(decodeURIComponent(p.split("/").at(-1)));
      status = fact ? 200 : 404;
      result = fact
        ? {
            order: {
              order_no: fact.order_no,
              product_type: fact.product_type,
              product_code: fact.product_code,
              status: fact.status,
            },
            settled: fact.settled,
          }
        : { detail: "Order not found" };
      if (orderHold && fact && orderHold.order === fact.order_no) {
        const held = orderHold;
        orderHold = null;
        await fulfillHeld(req, held, result);
        return;
      }
    } else if (["EPAY-H5-ORDER", "EPAY-H5-SECOND", "CREEM-H5-ORDER"].some((id) => p === "/v1/commerce/orders/" + id + "/reconcile") && req.method() === "POST") {
      const order = checkoutFacts.get(p.split("/")[4]);
      if (order) {
        order.status = "paid";
        order.settled = true;
      }
      result = { order, settled: order?.settled };
    } else if (p === "/v1/commerce/checkout") {
      if (creemAvailable && body.provider === "creem") {
        const order = {
          order_no: "CREEM-H5-ORDER", product_type: "membership",
          product_code: body.code, status: "created", settled: false,
        };
        checkoutFacts.set(order.order_no, order);
        result = {
          url: origin + "/?checkout=success&surface=h5&payment_provider=creem&order_no=CREEM-H5-ORDER&subscription_id=sub_fixture&product_id=prod_fixture&signature=fixture#account/orders",
          order_no: order.order_no, session_id: "ch_fixture",
        };
      } else if (epayAvailable && body.provider === "epay") {
        const previous = checkoutFacts.get("EPAY-H5-ORDER");
        if (previous?.intent === body.idempotency_key && previous.status === "paid") {
          status = 409;
          result = { detail: {
            code: "checkout_already_paid", action: "view_orders",
            order_no: previous.order_no, message: "This order has already been paid.",
          } };
        } else {
          const order = {
            order_no: previous ? "EPAY-H5-SECOND" : "EPAY-H5-ORDER",
            product_type: "membership", product_code: body.code,
            status: "created", settled: false, intent: body.idempotency_key,
          };
          checkoutFacts.set(order.order_no, order);
          result = {
            url: origin + "/?checkout=success&surface=h5&payment_provider=epay&order_no=" + order.order_no + "#account/orders",
            order_no: order.order_no, session_id: "",
          };
        }
      } else if (recoveryReplies.length) {
        const reply = recoveryReplies.shift();
        status = reply.status;
        result = reply.body;
      } else {
        const held = checkoutHold;
        checkoutHold = null;
        if (!held) {
          unknown.push("Unplanned checkout");
          status = 409;
          result = { detail: "Unexpected purchase" };
        } else {
          checkoutFacts.set(held.order, {
            order_no: held.order,
            product_type: "membership",
            product_code: "weekly",
            status: "created",
            settled: false,
          });
          await fulfillHeld(req, held, {
            url: origin + "/?checkout=cancel&surface=h5#account/orders",
            session_id: "cs_fixture",
            order_no: held.order,
          });
          return;
        }
      }
    } else if (p === "/v1/commerce/orders/CREEM-H5-LOCAL/cancel") {
      result = {order_no: "CREEM-H5-LOCAL", status: "failed", canceled: true};
    } else if (p === "/v1/billing/cancel") {
      const held = cancelHold;
      cancelHold = null;
      if (!held) {
        unknown.push("Unplanned cancellation");
        status = 409;
        result = { detail: "Unexpected cancellation" };
      } else {
        await fulfillHeld(req, held, { ok: true });
        return;
      }
    } else if (p === "/v1/plans")
      result = {
        billing_mode: creemAvailable ? "subscription" : "one_time",
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
            duration_days: creemAvailable ? null : 7,
            recurring_interval: creemAvailable ? "week" : null,
            billing_mode: creemAvailable ? "subscription" : "one_time",
          },
          ...(creemAvailable ? [{
            code: "monthly", name_en: "Monthly unmapped", name_zh: "月卡",
            price_cents: 799, currency: "USD", version: 1, version_id: 2,
            benefits: {}, recurring_interval: "month", billing_mode: "subscription",
          }] : []),
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
          ...(epayAvailable ? [{
            code: "epay-fixture",
            name: "Epay Alipay",
            provider: "epay",
            enabled: true,
            method: "alipay",
            billing_modes: ["one_time"],
          }] : []),
          ...(creemAvailable ? [{
            code: "creem-fixture", name: "Creem fixture", provider: "creem", enabled: true,
            billing_modes: ["subscription"],
            products: [{product_type: "membership", product_code: "weekly", billing_mode: "subscription"}],
          }] : []),
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
  assert.deepEqual(await page.$$eval(".tabs [data-tab]", tabs => tabs.map(tab => tab.dataset.tab)), ["home", "foryou", "rewards", "mylist", "me"]);
  pass("390px loads the independent H5 stage and five distinct bottom tabs");
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
    "0px",
  );
  assert.equal(
    await page.$eval(
      ".consumer-header nav",
      (e) => getComputedStyle(e).display,
    ),
    "flex",
  );
  pass("1024px switches to the desktop root with desktop navigation");
  const gutter = await page.addStyleTag({
    content:
      "html{overflow-y:scroll!important;scrollbar-gutter:stable!important}html::-webkit-scrollbar{width:15px}",
  });
  const geometry = await page.evaluate(() => ({
    inner: innerWidth,
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
    header: document.querySelector(".consumer-header").getBoundingClientRect()
      .width,
  }));
  assert.equal(geometry.inner, 1024);
  assert(
    geometry.client < geometry.inner,
    "fixture must reserve a real vertical scrollbar gutter",
  );
  assert(geometry.scroll <= geometry.client);
  assert(geometry.header <= geometry.client + 0.5);
  await gutter.dispose();
  await page.evaluate(() => {
    for (const style of [...document.head.querySelectorAll("style")])
      if (style.textContent.includes("scrollbar-gutter:stable!important"))
        style.remove();
  });
  pass(
    "1024px desktop follows client width when a classic 15px scrollbar is reserved",
  );
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
  await goto("?pid=landing#watch/1?episode=10&return=price");
  await surface("mobile");
  await page.keyboard.press("Tab");
  await page.waitForSelector('[data-player-action="episodes"]', { visible: true });
  await page.$eval(
    ".play",
    (element) => (element.dataset.surfaceSentinel = "kept"),
  );
  const beforePlayReads = calls.filter(
    (call) => call.path === "/v1/dramas/1/play",
  ).length;
  await page.click('[data-player-action="episodes"]');
  await page.waitForSelector(".ep-grid button");
  await page.click('.ep-grid [data-episode-id="11"]');
  await page.waitForFunction(
    () =>
      new URLSearchParams(location.hash.split("?")[1]).get("episode") ===
        "11" &&
      document.querySelector(".back-title")?.textContent.includes("2"),
  );
  assert.equal(
    await page.$eval(".play", (element) => element.dataset.surfaceSentinel),
    "kept",
  );
  assert.equal(
    calls.filter((call) => call.path === "/v1/dramas/1/play").length,
    beforePlayReads,
  );
  assert.equal(new URL(page.url()).searchParams.get("pid"), "landing");
  assert.equal(
    new URLSearchParams(new URL(page.url()).hash.split("?")[1]).get("return"),
    "price",
  );
  await page.reload({ waitUntil: "networkidle0" });
  await surface("mobile");
  await page.waitForFunction(() =>
    document.querySelector(".back-title")?.textContent.includes("2"),
  );
  await resize(1440);
  await surface("desktop");
  await page.waitForFunction(() =>
    document
      .querySelector('.episode-button[aria-current="true"]')
      ?.textContent.includes("2"),
  );
  await resize(390);
  await surface("mobile");
  await page.waitForFunction(() =>
    document.querySelector(".back-title")?.textContent.includes("2"),
  );
  pass(
    "H5 selection updates its URL without remounting; reload and resize retain the chosen episode and query",
  );
  media = Buffer.from(
    await page.evaluate(async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 160;
      canvas.height = 240;
      const context = canvas.getContext("2d"),
        stream = canvas.captureStream(12),
        recorder = new MediaRecorder(stream, { mimeType: "video/webm" }),
        chunks = [];
      recorder.ondataavailable = (event) => chunks.push(event.data);
      const stopped = new Promise((resolve) => {
        recorder.onstop = resolve;
      });
      recorder.start();
      const started = performance.now();
      await new Promise((resolve) => {
        const draw = () => {
          context.fillStyle =
            "rgb(" +
            (Math.floor((performance.now() - started) / 7) % 255) +
            ",40,70)";
          context.fillRect(0, 0, 160, 240);
          if (performance.now() - started < 1400) requestAnimationFrame(draw);
          else resolve();
        };
        draw();
      });
      recorder.stop();
      await stopped;
      stream.getTracks().forEach((track) => track.stop());
      return Array.from(new Uint8Array(await new Blob(chunks).arrayBuffer()));
    }),
  );
  playableFirst = true;
  const completedFirstEpisode = page.waitForResponse((response) => {
    const request = response.request();
    if (new URL(response.url()).pathname !== "/v1/playback/complete" || request.method() !== "POST") return false;
    try {
      return JSON.parse(request.postData() || "{}").episode_id === 10;
    } catch {
      return false;
    }
  });
  await goto("#watch/1?episode=10");
  await surface("mobile");
  await page.waitForFunction(
    () =>
      new URLSearchParams(location.hash.split("?")[1]).get("episode") ===
        "11" &&
      document.querySelector(".back-title")?.textContent.includes("2"),
  );
  const completedResponse = await completedFirstEpisode;
  assert.equal(completedResponse.status(), 200);
  await completedResponse.json();
  assert(
    calls.some(
      (call) =>
        call.path === "/v1/playback/complete" && call.body.episode_id === 10,
    ),
  );
  playableFirst = false;
  pass("real H5 media completion advances the selected episode URL");

  memberAccess = true;
  const memberLogin = async () => {
    await page.locator(".lock-cta").click();
    await page.waitForSelector('[data-testid="login-sheet"]');
    await page.type("#auth-email", "member@example.com");
    await page.locator(".auth-primary").click();
    await page.waitForSelector(".auth-code");
    await page.type(".auth-box", "123456", { delay: 20 });
    await page.locator(".auth-primary").click();
  };
  await goto("#watch/1?episode=11");
  await page.reload({ waitUntil: "networkidle0" });
  await surface("mobile");
  await page.waitForSelector(".locked-view");
  const beforeMember = calls.length;
  await memberLogin();
  await page.waitForFunction(
    () =>
      document.querySelector(".play:not(.locked-view) video")?.readyState >= 2,
  );
  assert(
    calls
      .slice(beforeMember)
      .some(
        (c) =>
          c.path === "/v1/dramas/1/play" &&
          c.headers.authorization === "Bearer mobile-member-token",
      ),
  );
  assert(
    calls
      .slice(beforeMember)
      .some(
        (c) =>
          c.path === "/v1/playback" &&
          c.body.episode_id === 11 &&
          c.headers.authorization === "Bearer mobile-member-token",
      ),
  );
  assert.equal(await page.$('[data-testid="premium-sheet"]'), null);
  await goto("#account/settings");
  await page.locator('[data-testid="settings-sign-out"]').click();
  await page.locator('[data-testid="signout-confirm"]').click();
  await page.waitForFunction(() => !localStorage.getItem("minitv_h5_token"));
  const afterLogout = calls.length;
  await goto("#watch/1?episode=11");
  await page.waitForSelector(".locked-view");
  await page.waitForFunction(() => window.__jjtvPendingFetches === 0);
  assert.equal(
    calls
      .slice(afterLogout)
      .filter((c) => c.path === "/v1/playback" && c.body.episode_id === 11)
      .length,
    0,
  );
  pass(
    "H5 member login refreshes server access and resumes paid playback; signing out restores the guest lock without minting paid media",
  );
  for (const kind of ["access", "media"]) {
    await goto("#watch/1?episode=11");
    await page.reload({ waitUntil: "networkidle0" });
    await surface("mobile");
    await page.waitForSelector(".locked-view");
    const held = hold(kind, "STALE-MEMBER-" + kind);
    memberProfileBarrier = held;
    const beforeLate = calls.length;
    await memberLogin();
    await held.started;
    await page.waitForFunction(
      () =>
        !localStorage.getItem("minitv_h5_token") &&
        !!document.querySelector(".locked-view") &&
        !document.querySelector(".play video"),
    );
    const afterIdentity = calls.length;
    await settleHeld(held);
    await page.waitForFunction(() => window.__jjtvPendingFetches === 0);
    assert.equal(await page.$(".play video"), null);
    assert(await page.$(".locked-view"));
    assert.equal(
      calls
        .slice(afterIdentity)
        .filter(
          (c) =>
            c.path === "/v1/playback" ||
            /^\/v1\/playback\/(start|progress|complete)$/.test(c.path),
        ).length,
      0,
    );
    assert.equal(
      calls
        .slice(beforeLate)
        .filter(
          (c) =>
            c.path === "/v1/playback" &&
            c.headers.authorization !== "Bearer mobile-member-token",
        ).length,
      0,
    );
    await page.locator('[data-testid="login-sheet"] .close-btn').click();
    pass(
      "a late member " +
        kind +
        " response cannot restore old access or report playback after the session becomes guest",
    );
  }
  memberAccess = false;
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
  await goto("#mylist");
  await surface("mobile");
  await page.waitForSelector(".mylist-page");
  assert.equal(new URL(page.url()).hash, "#mylist");
  assert.deepEqual(await page.$$eval('.tabs [role="tab"][aria-selected="true"]', tabs => tabs.map(tab => tab.dataset.tab)), ["mylist"]);
  assert.equal(await page.$eval('.tabs [data-tab="mylist"] .tab-label', element => element.textContent.trim()), "My List");
  assert.equal(await page.$eval('.tabs [data-tab="me"]', element => element.getAttribute("aria-selected")), "false");
  assert.equal(await page.$(".consumer-header"), null);
  pass("My List deep link keeps the independent mobile surface and selects only My List");
  await goto("#account");
  await surface("mobile");
  await page.waitForFunction(
    () =>
      document.querySelector(".me-hero h2")?.textContent === "Mobile viewer",
  );
  assert.deepEqual(await page.$$eval('.tabs [role="tab"][aria-selected="true"]', tabs => tabs.map(tab => tab.dataset.tab)), ["me"]);
  assert.equal(await page.$eval('.tabs [data-tab="me"] .tab-label', element => element.textContent.trim()), "Profile");
  assert.equal(await page.$eval('.tabs [data-tab="mylist"]', element => element.getAttribute("aria-selected")), "false");
  pass("Profile remains independent from My List and is the sole selected mobile tab");
  await page.$eval(
    ".me-page",
    (element) => (element.dataset.surfaceSentinel = "kept"),
  );
  await page.click('[data-testid="me-tab-favorites"]');
  await page.waitForFunction(() =>
    location.hash.startsWith("#account/favorites"),
  );
  assert.equal(
    await page.$eval(".me-page", (element) => element.dataset.surfaceSentinel),
    "kept",
  );
  await page.click('[data-testid="me-orders"]');
  await page.waitForSelector('[data-testid="orders-sheet"]');
  assert(new URL(page.url()).hash.startsWith("#account/orders"));
  await resize(1440);
  await surface("desktop");
  await resize(390);
  await surface("mobile");
  await page.waitForSelector('[data-testid="orders-sheet"]');
  assert.equal(
    await page.$eval(".me-tab.on", (element) => element.textContent.trim()),
    "Saved",
  );
  // The remounted sheet slides in; wait for stable geometry before real pointer input.
  await page.locator('[data-testid="orders-sheet"] .close-btn').click();
  await page.waitForFunction(() => location.hash === "#account/favorites");
  pass(
    "H5 saved and order actions synchronize routes and survive desktop round trips without losing the library tab",
  );
  await page.evaluate(() => localStorage.removeItem("minitv_h5_token"));
  await goto("#price");
  await page.reload({ waitUntil: "networkidle0" });
  await surface("mobile");
  await page.waitForSelector('[data-testid="premium-buy-weekly"]');
  const guestPurchases = calls.filter(
    (call) => call.path === "/v1/commerce/checkout",
  ).length;
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await page.waitForSelector('[data-testid="login-sheet"]');
  await resize(1920);
  await surface("mobile");
  await page.locator('[data-testid="login-sheet"] .close-btn').click();
  await surface("desktop");
  assert.equal(
    calls.filter((call) => call.path === "/v1/commerce/checkout").length,
    guestPurchases,
  );
  pass(
    "queued guest subscription login pins mobile and releases it when sign-in is canceled",
  );
  await page.evaluate(() =>
    localStorage.setItem("minitv_h5_token", "mobile-token"),
  );
  await resize(390);
  await goto("#price");
  await page.reload({ waitUntil: "networkidle0" });
  await surface("mobile");
  await page.waitForSelector('[data-testid="premium-buy-weekly"]');
  epayAvailable = true;
  await page.reload({ waitUntil: "networkidle0" });
  await surface("mobile");
  const h5PaymentSelector = ".premium-payment-channel select";
  await page.waitForSelector(h5PaymentSelector);
  const epayOption = await page.$eval(h5PaymentSelector, (select) =>
    [...select.options].find((item) => item.textContent === "Epay Alipay").value,
  );
  await page.select(h5PaymentSelector, epayOption);
  const epayCalls = calls.length;
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await page.waitForFunction(() =>
    document.querySelector(".checkout-return-notice p")?.textContent.includes("Payment confirmed."),
  );
  const epayRequests = calls.slice(epayCalls);
  assert.equal(epayRequests.find((call) => call.path === "/v1/commerce/checkout")?.body.payment_method, "alipay");
  assert.equal(epayRequests.filter((call) => call.path === "/v1/commerce/orders/EPAY-H5-ORDER/reconcile" && call.method === "POST").length, 1);
  assert.equal(new URL(page.url()).searchParams.has("order_no"), false);
  assert.equal(new URL(page.url()).searchParams.has("payment_provider"), false);
  pass("the H5 one-time membership selects Epay and shows its server-confirmed return");
  await goto("#price");
  await surface("mobile");
  await page.waitForSelector(h5PaymentSelector);
  await page.select(h5PaymentSelector, epayOption);
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await page.waitForSelector('[data-testid="premium-paid-checkout"]');
  const originalEpay = epayRequests.find((call) => call.path === "/v1/commerce/checkout").body;
  assert.equal(calls.filter((call) => call.path === "/v1/commerce/checkout").at(-1).body.idempotency_key, originalEpay.idempotency_key);
  await page.locator('[data-testid="premium-paid-start-new-checkout"]').click();
  await page.waitForFunction(() =>
    document.querySelector(".checkout-return-notice p")?.textContent.includes("Payment confirmed."),
  );
  const renewedEpay = calls.filter((call) => call.path === "/v1/commerce/checkout").at(-1).body;
  assert.equal(renewedEpay.payment_method, originalEpay.payment_method);
  assert.notEqual(renewedEpay.idempotency_key, originalEpay.idempotency_key);
  epayAvailable = false;
  pass("H5 keeps the Epay intent through a paid return and only rotates it after explicit purchase");
  creemAvailable = true;
  await goto("#price");
  await surface("mobile");
  const creemPicker = ".premium-plan:first-child .premium-payment-channel select";
  await page.waitForSelector(creemPicker);
  assert.equal(await page.$(".premium-plan:nth-child(2) select"), null);
  const creemChoice = await page.$eval(creemPicker, (select) =>
    [...select.options].find((item) => item.textContent === "Creem fixture").value,
  );
  await page.select(creemPicker, creemChoice);
  const creemCalls = calls.length;
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await page.waitForFunction(() =>
    document.querySelector(".checkout-return-notice p")?.textContent.includes("Payment confirmed."),
  );
  const creemRequests = calls.slice(creemCalls);
  const creemCheckout = creemRequests.find((call) => call.path === "/v1/commerce/checkout")?.body;
  assert.equal(creemCheckout.provider, "creem");
  assert.equal(creemCheckout.code, "weekly");
  assert.equal(creemCheckout.payment_method, undefined);
  assert.equal(creemRequests.filter((call) => call.path === "/v1/commerce/orders/CREEM-H5-ORDER/reconcile" && call.method === "POST").length, 1);
  assert.deepEqual([...new URL(page.url()).searchParams.keys()], ["surface"]);
  pass("H5 uses the Creem subscription only when its specific plan and mode are both advertised");
  await goto("#price");
  await surface("mobile");
  await page.waitForSelector('[data-testid="premium-buy-monthly"]');
  recoveryReplies.push({status: 409, body: {detail: {
    code: "pending_subscription_checkout", action: "resume_only", provider: "creem",
    order_no: "CREEM-H5-ORDER", message: "Continue your existing Creem checkout.",
    checkout_url: origin + "/?checkout=success&surface=h5&payment_provider=creem&order_no=CREEM-H5-ORDER#account/orders",
  }}});
  await page.locator('[data-testid="premium-buy-monthly"]').click();
  await page.waitForSelector('[data-testid="premium-resume-checkout"]');
  assert.equal(await page.$('[data-testid="premium-cancel-pending-checkout"]'), null);
  assert.equal(await page.$('[data-testid="premium-start-new-checkout"]'), null);
  const beforeResume = calls.filter((call) => call.path === "/v1/commerce/checkout").length;
  await page.locator('[data-testid="premium-resume-checkout"]').click();
  await page.waitForFunction(() =>
    document.querySelector(".checkout-return-notice p")?.textContent.includes("Payment confirmed."),
  );
  assert.equal(calls.filter((call) => call.path === "/v1/commerce/checkout").length, beforeResume);
  assert.equal(await page.evaluate(() => sessionStorage.getItem("jjtv_h5_checkout_order:reconcile")), "CREEM-H5-ORDER");
  pass("H5 resumes a conflicting Creem checkout without offering cancellation or creating another order");
  await goto("#price");
  await surface("mobile");
  await page.waitForSelector('[data-testid="premium-buy-monthly"]');
  recoveryReplies.push({status: 409, body: {detail: {
    code: "pending_subscription_checkout", action: "resume_or_cancel", provider: "creem",
    order_no: "CREEM-H5-LOCAL", message: "Cancel locally before starting another checkout.",
  }}});
  await page.locator('[data-testid="premium-buy-monthly"]').click();
  await page.waitForSelector('[data-testid="premium-cancel-pending-checkout"]');
  assert.equal(await page.$('[data-testid="premium-resume-checkout"]'), null);
  await page.locator('[data-testid="premium-cancel-pending-checkout"]').click();
  await page.waitForSelector('[data-testid="premium-pending-checkout"]', {hidden: true});
  assert(calls.some((call) => call.path === "/v1/commerce/orders/CREEM-H5-LOCAL/cancel" && call.method === "POST"));
  creemAvailable = false;
  pass("H5 cancels a Creem order only when the backend permits local cancellation");
  await goto("#price");
  await page.reload({waitUntil: "networkidle0"});
  await surface("mobile");
  await page.waitForSelector('[data-testid="premium-buy-weekly"]');
  const purchase = hold("checkout", "H5-PINNED-ORDER");
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await purchase.started;
  await resize(1440);
  await surface("mobile");
  assert.equal(
    await page.$$(".consumer-header").then((elements) => elements.length),
    0,
  );
  const navigated = page.waitForNavigation({ waitUntil: "networkidle0" });
  purchase.release();
  await navigated;
  await surface("mobile");
  assert.equal(
    await page.evaluate(() => sessionStorage.getItem("jjtv_h5_checkout_order")),
    "H5-PINNED-ORDER",
  );
  pass(
    "an in-flight H5 checkout remains mobile through resize until its confirmed redirect",
  );
  await resize(390);
  await goto("#price");
  await page.reload({ waitUntil: "networkidle0" });
  await surface("mobile");
  await page.waitForSelector('[data-testid="premium-buy-weekly"]');
  const abandoned = hold(
    "checkout",
    "ABANDONED-ORDER",
    process.env.SURFACE_LATE_DELIVERY || "respond",
  );
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await abandoned.started;
  await page.locator('[data-testid="premium-sheet"] .close-btn').click();
  await page.waitForSelector('[data-testid="premium-sheet"]', { hidden: true });
  await resize(1440);
  await surface("desktop");
  if (process.env.SURFACE_LATE_RELOAD === "1") {
    await page.reload({ waitUntil: "domcontentloaded" });
    await surface("desktop");
  }
  await page.evaluate(() =>
    sessionStorage.setItem("jjtv_h5_checkout_order", "NEW-OWNER-ORDER"),
  );
  await settleHeld(abandoned);
  await page.waitForFunction(() => window.__jjtvPendingFetches === 0);
  assert(new URL(page.url()).hash.startsWith("#account/"));
  assert.equal(
    await page.evaluate(() => sessionStorage.getItem("jjtv_h5_checkout_order")),
    "NEW-OWNER-ORDER",
  );
  pass(
    "unmounted H5 checkout responses cannot navigate the new desktop tree or overwrite its order context",
  );
  await resize(390);
  await goto("#price");
  await page.reload({ waitUntil: "networkidle0" });
  await surface("mobile");
  await page.waitForSelector('[data-testid="premium-buy-weekly"]');
  const changedOwner = hold("checkout", "OLD-TOKEN-ORDER");
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await changedOwner.started;
  await page.evaluate(() => {
    localStorage.setItem("minitv_h5_token", "mobile-token-b");
    sessionStorage.setItem("jjtv_h5_checkout_order", "OTHER-MOBILE-ORDER");
  });
  await settleHeld(changedOwner);
  await page.waitForFunction(() => window.__jjtvPendingFetches === 0);
  assert.equal(new URL(page.url()).hash, "#price");
  assert.equal(
    await page.evaluate(() => sessionStorage.getItem("jjtv_h5_checkout_order")),
    "OTHER-MOBILE-ORDER",
  );
  await resize(1440);
  await surface("desktop");
  pass(
    "a late H5 checkout response for another token releases its pin without redirecting or overwriting the new order",
  );
  activeMember = true;
  membershipEnabled = false;
  await page.evaluate(() =>
    localStorage.setItem("minitv_h5_token", "mobile-token"),
  );
  await resize(390);
  await goto("#price");
  await page.reload({ waitUntil: "networkidle0" });
  await surface("mobile");
  await page.waitForSelector('[data-testid="premium-cancel"]');
  // Requests from the departing enabled document can start during navigation.
  // Assert against this freshly bootstrapped disabled document, including reads
  // issued before the membership controls become visible.
  const disabledMobileDocument = await page.evaluate(() => window.__surfaceFetch.documentId);
  assert.equal(calls.filter(call => call.path === "/v1/plans" && call.headers["x-surface-document"] === disabledMobileDocument).length, 0);
  assert.equal(await page.$('[data-testid="premium-extend"], [data-testid="premium-buy-pass"], [data-testid^="premium-buy-"]'), null);
  const cancellation = hold("cancel");
  await page.locator('[data-testid="premium-cancel"]').click();
  await cancellation.started;
  await resize(1440);
  await surface("mobile");
  await page.locator('[data-testid="premium-sheet"] .close-btn').click();
  await surface("desktop");
  const profileReads = calls.filter(
    (call) =>
      call.path === "/v1/me" && call.headers["x-device-id"]?.startsWith("h5-"),
  ).length;
  await settleHeld(cancellation);
  await page.waitForFunction(() => window.__jjtvPendingFetches === 0);
  assert.equal(
    calls.filter(
      (call) =>
        call.path === "/v1/me" &&
        call.headers["x-device-id"]?.startsWith("h5-"),
    ).length,
    profileReads,
  );
  pass("disabled new membership sales preserve H5 renewal cancellation without loading plans; cancellation still ignores effects after unmount");
  await goto("#premium");
  await page.reload({ waitUntil: "networkidle0" });
  await surface("desktop");
  await page.waitForSelector('[data-testid="membership-cancel"]');
  assert.equal(await page.$("#membership-plans"), null);
  const disabledDesktopDocument = await page.evaluate(() => window.__surfaceFetch.documentId);
  assert.equal(calls.filter(call => call.path === "/v1/plans" && call.headers["x-surface-document"] === disabledDesktopDocument).length, 0);
  await page.locator('[data-testid="membership-cancel"]').click();
  const desktopCancellation = hold("cancel");
  await page.locator('[data-testid="membership-confirm-cancel"]').click();
  await desktopCancellation.started;
  await settleHeld(desktopCancellation);
  await page.waitForSelector('[data-testid="membership-confirm-cancel"]', { hidden: true });
  assert(calls.some(call => call.path === "/v1/billing/cancel" && call.headers.authorization === "Bearer desktop-token"));
  pass("existing desktop subscribers can confirm cancellation while membership purchases are disabled");
  activeMember = false;
  membershipEnabled = true;
  await resize(390);

  const checkoutCalls = () =>
    calls.filter((call) => call.path === "/v1/commerce/checkout");
  const expiredReply = (message) => ({
    status: 409,
    body: {
      detail: {
        code: "checkout_expired",
        action: "new_purchase",
        order_no: "EXPIRED-ORDER",
        message: message || "This checkout has expired. Start a new purchase.",
      },
    },
  });
  const unknownReply = {
    status: 503,
    body: { detail: "Fixture checkout result unknown" },
  };
  const freshPlans = async (intent) => {
    await resize(390);
    await page.evaluate((key) => {
      localStorage.setItem("minitv_h5_token", "mobile-token");
      sessionStorage.setItem("jjtv_checkout_membership_weekly", key);
    }, intent);
    await goto("#price");
    await page.reload({ waitUntil: "networkidle0" });
    await surface("mobile");
    await page.waitForSelector('[data-testid="premium-buy-weekly"]');
  };
  const buyFailed = async (reply) => {
    recoveryReplies.push(reply);
    await page.locator('[data-testid="premium-buy-weekly"]').click();
    await page.waitForFunction(
      () =>
        document.querySelector(".premium-error") &&
        !document.querySelector('[data-testid="premium-buy-weekly"]').disabled,
    );
  };
  await freshPlans("expired-fixture-intent");
  const beforeExpired = checkoutCalls().length;
  recoveryReplies.push(expiredReply());
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await page.waitForSelector('[data-testid="premium-start-new-checkout"]');
  assert.equal(checkoutCalls().length, beforeExpired + 1);
  assert.equal(
    await page.evaluate(() =>
      sessionStorage.getItem("jjtv_checkout_membership_weekly"),
    ),
    "expired-fixture-intent",
  );
  assert.equal(
    await page.$eval(
      '[data-testid="premium-buy-weekly"]',
      (button) => button.disabled,
    ),
    true,
  );
  await page.reload({ waitUntil: "networkidle0" });
  await surface("mobile");
  await page.waitForSelector('[data-testid="premium-buy-weekly"]');
  recoveryReplies.push(expiredReply());
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await page.waitForSelector('[data-testid="premium-start-new-checkout"]');
  assert(
    checkoutCalls()
      .slice(beforeExpired)
      .every((call) => call.body.idempotency_key === "expired-fixture-intent"),
  );
  await resize(1440);
  await surface("mobile");
  await resize(390);
  recoveryReplies.push(unknownReply);
  await page.locator('[data-testid="premium-start-new-checkout"]').click();
  await page.waitForFunction(
    () =>
      document.querySelector(".premium-error") &&
      !document.querySelector('[data-testid="premium-buy-weekly"]').disabled,
  );
  const freshIntent = checkoutCalls().at(-1).body.idempotency_key;
  assert.notEqual(freshIntent, "expired-fixture-intent");
  assert.equal(
    await page.evaluate(() =>
      sessionStorage.getItem("jjtv_checkout_membership_weekly"),
    ),
    freshIntent,
  );
  assert.equal(checkoutCalls().length, beforeExpired + 3);
  await buyFailed(unknownReply);
  assert.equal(checkoutCalls().at(-1).body.idempotency_key, freshIntent);
  assert.equal(checkoutCalls().length, beforeExpired + 4);
  pass(
    "explicit expiry survives reopening and rotates only on Start new checkout; unknown retries reuse that fresh intent",
  );
  await freshPlans("uncertain-fixture-intent");
  const beforeUnknown = checkoutCalls().length;
  await buyFailed(unknownReply);
  assert.equal(
    await page.$('[data-testid="premium-start-new-checkout"]'),
    null,
  );
  await buyFailed({
    status: 409,
    body: {
      detail: {
        code: "checkout_already_paid",
        action: "view_orders",
        order_no: "PAID-ORDER",
        message: "The order is already paid.",
      },
    },
  });
  assert.equal(checkoutCalls().length, beforeUnknown + 2);
  assert(
    checkoutCalls()
      .slice(beforeUnknown)
      .every(
        (call) => call.body.idempotency_key === "uncertain-fixture-intent",
      ),
  );
  assert.equal(
    await page.evaluate(() =>
      sessionStorage.getItem("jjtv_checkout_membership_weekly"),
    ),
    "uncertain-fixture-intent",
  );
  assert.equal(
    await page.$('[data-testid="premium-start-new-checkout"]'),
    null,
  );
  pass(
    "uncertain and already-paid conflicts never rotate keys or offer a fresh automatic purchase",
  );
  const paidReply = (order, subscription = false) =>
    subscription
      ? {
          status: 409,
          body: {
            detail: {
              code: "checkout_already_paid",
              action: "view_orders",
              order_no: order,
              message: "This order was already paid.",
            },
          },
        }
      : {
          status: 200,
          body: {
            url: origin + "/?old_paid_checkout=1#account/orders",
            session_id: "cs_paid",
            order_no: order,
            reused: true,
          },
        };
  for (const subscription of [false, true]) {
    const intent = subscription
        ? "paid-subscription-intent"
        : "paid-pass-intent",
      order = subscription ? "PAID-SUBSCRIPTION" : "PAID-PASS";
    await freshPlans(intent);
    checkoutFacts.set(order, {
      order_no: order,
      product_type: "membership",
      product_code: "weekly",
      status: "paid",
      settled: true,
    });
    const beforePaid = checkoutCalls().length;
    recoveryReplies.push(paidReply(order, subscription));
    await page.locator('[data-testid="premium-buy-weekly"]').click();
    await page.waitForSelector('[data-testid="premium-paid-checkout"]');
    assert.equal(new URL(page.url()).hash, "#price");
    assert(!new URL(page.url()).searchParams.has("old_paid_checkout"));
    assert.equal(
      await page.evaluate(() =>
        sessionStorage.getItem("jjtv_checkout_membership_weekly"),
      ),
      intent,
    );
    assert.equal(checkoutCalls().length, beforePaid + 1);
    assert(await page.$('[data-testid="premium-paid-view-orders"]'));
    if (!subscription) {
      await resize(1440);
      await surface("mobile");
      await resize(390);
      await page.locator('[data-testid="premium-paid-view-orders"]').click();
      await page.waitForFunction(() => location.hash === "#account/orders");
      await page.waitForSelector('[data-testid="orders-sheet"]');
      await goto("#price");
      await page.reload({ waitUntil: "networkidle0" });
      await surface("mobile");
      recoveryReplies.push(paidReply(order, subscription));
      await page.locator('[data-testid="premium-buy-weekly"]').click();
      await page.waitForSelector('[data-testid="premium-paid-checkout"]');
      assert.equal(checkoutCalls().at(-1).body.idempotency_key, intent);
    }
    recoveryReplies.push(unknownReply);
    await page
      .locator('[data-testid="premium-paid-start-new-checkout"]')
      .click();
    await page.waitForFunction(
      () =>
        document.querySelector(".premium-error") &&
        !document.querySelector('[data-testid="premium-buy-weekly"]').disabled,
    );
    const next = checkoutCalls().at(-1).body.idempotency_key;
    assert.notEqual(next, intent);
    assert.equal(
      await page.evaluate(() =>
        sessionStorage.getItem("jjtv_checkout_membership_weekly"),
      ),
      next,
    );
    await buyFailed(unknownReply);
    assert.equal(checkoutCalls().at(-1).body.idempotency_key, next);
    pass(
      (subscription ? "subscription" : "one-time") +
        " paid checkout without a success return offers explicit new intent; uncertain retries retain it",
    );
  }
  await freshPlans("fully-refunded-intent");
  checkoutFacts.set("FULLY-REFUNDED", {
    order_no: "FULLY-REFUNDED",
    product_type: "membership",
    product_code: "weekly",
    status: "refunded",
    settled: true,
  });
  const beforeRefunded = checkoutCalls().length;
  recoveryReplies.push(paidReply("FULLY-REFUNDED"));
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await page.waitForSelector('[data-testid="premium-paid-checkout"]');
  assert(
    (
      await page.$eval(
        '[data-testid="premium-paid-checkout"]',
        (e) => e.textContent,
      )
    ).includes("fully refunded"),
  );
  assert.equal(checkoutCalls().length, beforeRefunded + 1);
  assert.equal(
    await page.evaluate(() =>
      sessionStorage.getItem("jjtv_checkout_membership_weekly"),
    ),
    "fully-refunded-intent",
  );
  await page.evaluate(() =>
    sessionStorage.setItem(
      "jjtv_checkout_membership_weekly",
      "NEWER-REFUND-ATTEMPT",
    ),
  );
  await page.locator('[data-testid="premium-paid-start-new-checkout"]').click();
  await page.waitForSelector('[data-testid="premium-paid-checkout"]', {
    hidden: true,
  });
  assert.equal(checkoutCalls().length, beforeRefunded + 1);
  assert.equal(
    await page.evaluate(() =>
      sessionStorage.getItem("jjtv_checkout_membership_weekly"),
    ),
    "NEWER-REFUND-ATTEMPT",
  );
  await freshPlans("fully-refunded-intent");
  recoveryReplies.push(paidReply("FULLY-REFUNDED"));
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await page.waitForSelector('[data-testid="premium-paid-checkout"]');
  const refundedNewCount = checkoutCalls().length;
  const refundedHold = hold("checkout", "REFUNDED-NEW-PURCHASE");
  const refundedNavigation = page.waitForNavigation({
    waitUntil: "domcontentloaded",
  });
  await page.locator('[data-testid="premium-paid-start-new-checkout"]').click();
  await refundedHold.started;
  assert.equal(checkoutCalls().length, refundedNewCount + 1);
  assert.notEqual(
    checkoutCalls().at(-1).body.idempotency_key,
    "fully-refunded-intent",
  );
  assert.equal(
    await page.$('[data-testid="premium-paid-start-new-checkout"]'),
    null,
  );
  assert.equal(
    await page.$eval('[data-testid="premium-buy-weekly"]', (b) => b.disabled),
    true,
  );
  const disabledPurchase = await page.$eval(
    '[data-testid="premium-buy-weekly"]',
    (b) => {
      const rect = b.getBoundingClientRect();
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    },
  );
  await page.mouse.click(disabledPurchase.x, disabledPurchase.y);
  await page.mouse.click(disabledPurchase.x, disabledPurchase.y);
  await page.waitForFunction(
    () => document.querySelector('[data-testid="premium-buy-weekly"]').disabled,
  );
  assert.equal(checkoutCalls().length, refundedNewCount + 1);
  await settleHeld(refundedHold);
  await refundedNavigation;
  await surface("mobile");
  await page.waitForFunction(
    () => !new URLSearchParams(location.search).has("checkout"),
  );
  pass(
    "fully refunded settled orders permit only explicit new purchase, preserve newer CAS intent and cannot submit twice",
  );
  for (const fact of [
    { status: "paid", settled: false, product_code: "weekly" },
    { status: "refunded", settled: false, product_code: "weekly" },
    { status: "refund_pending", settled: true, product_code: "weekly" },
    { status: "refund_review", settled: true, product_code: "weekly" },
    { status: "paid", settled: true, product_code: "monthly" },
  ]) {
    await freshPlans("unconfirmed-paid-intent");
    checkoutFacts.set("UNCONFIRMED-PAID", {
      order_no: "UNCONFIRMED-PAID",
      product_type: "membership",
      ...fact,
    });
    await buyFailed(paidReply("UNCONFIRMED-PAID", true));
    assert.equal(
      await page.$('[data-testid="premium-paid-start-new-checkout"]'),
      null,
    );
    assert.equal(
      await page.evaluate(() =>
        sessionStorage.getItem("jjtv_checkout_membership_weekly"),
      ),
      "unconfirmed-paid-intent",
    );
  }
  pass(
    "unsettled, refund-pending, manual-review and product-mismatched orders cannot authorize a fresh membership intent",
  );
  for (const flag of ["success", "cancel", "unknown"]) {
    const intent = "return-proof-" + flag;
    await freshPlans(intent);
    await page.evaluate(() =>
      sessionStorage.setItem(
        "jjtv_checkout_membership_monthly",
        "OTHER-UNCERTAIN-PLAN",
      ),
    );
    await goto("?checkout=" + flag + "&surface=h5#price");
    await surface("mobile");
    await page.waitForFunction(
      () => !new URLSearchParams(location.search).has("checkout"),
    );
    assert.equal(
      await page.evaluate(() =>
        sessionStorage.getItem("jjtv_checkout_membership_weekly"),
      ),
      intent,
    );
    assert.equal(
      await page.evaluate(() =>
        sessionStorage.getItem("jjtv_checkout_membership_monthly"),
      ),
      "OTHER-UNCERTAIN-PLAN",
    );
  }
  pass(
    "success, canceled and unknown return queries never retire unconfirmed membership intents",
  );
  for (const changeOwner of [false, true]) {
    await freshPlans("late-paid-owner-intent");
    checkoutFacts.set("LATE-PAID", {
      order_no: "LATE-PAID",
      product_type: "membership",
      product_code: "weekly",
      status: "paid",
      settled: true,
    });
    const delayed = hold("order", "LATE-PAID");
    recoveryReplies.push(paidReply("LATE-PAID", true));
    await page.locator('[data-testid="premium-buy-weekly"]').click();
    await delayed.started;
    const beforePaidLate = checkoutCalls().length;
    await page.evaluate((other) => {
      if (other) localStorage.setItem("minitv_h5_token", "mobile-token-b");
      sessionStorage.setItem(
        "jjtv_checkout_membership_weekly",
        "NEWER-OWNER-OR-ATTEMPT",
      );
    }, changeOwner);
    await settleHeld(delayed);
    await page.waitForFunction(() => window.__jjtvPendingFetches === 0);
    assert.equal(new URL(page.url()).hash, "#price");
    assert.equal(checkoutCalls().length, beforePaidLate);
    assert.equal(
      await page.evaluate(() =>
        sessionStorage.getItem("jjtv_checkout_membership_weekly"),
      ),
      "NEWER-OWNER-OR-ATTEMPT",
    );
    assert.equal(await page.$('[data-testid="premium-paid-checkout"]'), null);
  }
  pass(
    "late paid-order proof cannot replace another account or a newer cached purchase intent",
  );
  await freshPlans("quota-paid-intent");
  checkoutFacts.set("QUOTA-PAID", {
    order_no: "QUOTA-PAID",
    product_type: "membership",
    product_code: "weekly",
    status: "paid",
    settled: true,
  });
  recoveryReplies.push(paidReply("QUOTA-PAID", true));
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await page.waitForSelector('[data-testid="premium-paid-start-new-checkout"]');
  const beforePaidQuota = checkoutCalls().length;
  await page.evaluate(() => {
    const proto = Object.getPrototypeOf(sessionStorage),
      native = proto.setItem;
    window.__restorePaidStorage = () => {
      proto.setItem = native;
    };
    proto.setItem = function (key, value) {
      if (this === sessionStorage && key === "jjtv_checkout_membership_weekly")
        throw new DOMException("Fixture quota", "QuotaExceededError");
      return native.call(this, key, value);
    };
  });
  await page.locator('[data-testid="premium-paid-start-new-checkout"]').click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".premium-error")].some((e) =>
      e.textContent.includes("Could not start checkout"),
    ),
  );
  assert.equal(checkoutCalls().length, beforePaidQuota);
  assert.deepEqual(errors, []);
  assert.equal(
    await page.evaluate(() =>
      sessionStorage.getItem("jjtv_checkout_membership_weekly"),
    ),
    "quota-paid-intent",
  );
  await page.evaluate(() => window.__restorePaidStorage());
  pass(
    "confirmed paid recovery reports storage failure without changing the old intent or creating a new checkout",
  );
  await freshPlans("quota-expired-intent");
  recoveryReplies.push(expiredReply());
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await page.waitForSelector('[data-testid="premium-start-new-checkout"]');
  const beforeQuota = checkoutCalls().length;
  await page.evaluate(() => {
    const prototype = Object.getPrototypeOf(sessionStorage),
      native = prototype.setItem;
    window.__restoreSurfaceStorage = () => {
      prototype.setItem = native;
      delete window.__restoreSurfaceStorage;
    };
    prototype.setItem = function (key, value) {
      if (this === sessionStorage && key === "jjtv_checkout_membership_weekly")
        throw new DOMException(
          "Fixture session storage quota exceeded",
          "QuotaExceededError",
        );
      return native.call(this, key, value);
    };
  });
  await page.locator('[data-testid="premium-start-new-checkout"]').click();
  await page.waitForFunction(
    () =>
      [...document.querySelectorAll(".premium-error")].some((element) =>
        element.textContent.includes("Could not start checkout"),
      ),
    { timeout: 5000 },
  );
  assert.equal(checkoutCalls().length, beforeQuota);
  assert.equal(
    await page.evaluate(() =>
      sessionStorage.getItem("jjtv_checkout_membership_weekly"),
    ),
    "quota-expired-intent",
  );
  assert.deepEqual(errors, []);
  assert.equal(
    await page.$eval(
      '[data-testid="premium-start-new-checkout"]',
      (button) => button.disabled,
    ),
    false,
  );
  await page.evaluate(() => window.__restoreSurfaceStorage());
  recoveryReplies.push(unknownReply);
  await page.locator('[data-testid="premium-start-new-checkout"]').click();
  await page.waitForFunction(
    () =>
      document.querySelector(".premium-error") &&
      !document.querySelector('[data-testid="premium-buy-weekly"]').disabled,
  );
  assert.equal(checkoutCalls().length, beforeQuota + 1);
  assert.notEqual(
    checkoutCalls().at(-1).body.idempotency_key,
    "quota-expired-intent",
  );
  pass(
    "expired checkout storage failure stays visible without changing intent or sending a request, and remains retryable",
  );
  await freshPlans("stale-expired-intent");
  recoveryReplies.push(expiredReply());
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await page.waitForSelector('[data-testid="premium-start-new-checkout"]');
  const beforeStale = checkoutCalls().length;
  await page.evaluate(() =>
    sessionStorage.setItem(
      "jjtv_checkout_membership_weekly",
      "newer-unconfirmed-intent",
    ),
  );
  await page.locator('[data-testid="premium-start-new-checkout"]').click();
  await page.waitForSelector('[data-testid="premium-start-new-checkout"]', {
    hidden: true,
  });
  assert.equal(checkoutCalls().length, beforeStale);
  assert.equal(
    await page.evaluate(() =>
      sessionStorage.getItem("jjtv_checkout_membership_weekly"),
    ),
    "newer-unconfirmed-intent",
  );
  await freshPlans("old-owner-expired-intent");
  recoveryReplies.push(expiredReply("PRIVATE OLD OWNER EXPIRED ORDER"));
  await page.locator('[data-testid="premium-buy-weekly"]').click();
  await page.waitForSelector('[data-testid="premium-start-new-checkout"]');
  await page.evaluate(() =>
    localStorage.setItem("minitv_h5_token", "mobile-token-b"),
  );
  await resize(400);
  await page.waitForSelector('[data-testid="premium-expired-checkout"]', {
    hidden: true,
  });
  assert(
    !(await page.$eval("body", (body) => body.innerText)).includes(
      "PRIVATE OLD OWNER EXPIRED ORDER",
    ),
  );
  assert.equal(
    await page.$('[data-testid="premium-start-new-checkout"]'),
    null,
  );
  assert.equal(
    await page.evaluate(() =>
      sessionStorage.getItem("jjtv_checkout_membership_weekly"),
    ),
    "old-owner-expired-intent",
  );
  await resize(1440);
  await surface("desktop");
  await resize(390);
  pass(
    "expired recovery cannot overwrite a newer cached intent or retain a previous account message and action",
  );
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
} catch (error) {
  await page
    .screenshot({ path: path.join(out, "surface-failure.png"), fullPage: true })
    .catch(() => {});
  console.error(JSON.stringify({ route: page.url(), errors, unknown }));
  throw error;
} finally {
  pending.forEach((item) => item.release());
  await browser.close();
  await server.close();
}
