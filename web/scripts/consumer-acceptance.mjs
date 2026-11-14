/** Browser contract and layout checks with explicit test fixtures; no provider calls. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";
import { createServer } from "vite";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUT =
  process.env.SHOTS_DIR || path.join(os.tmpdir(), "jjtv-pc-acceptance");
await fs.mkdir(OUT, { recursive: true });
const server = await createServer({
  root: ROOT,
  server: { host: "127.0.0.1", port: 5184, strictPort: true },
  logLevel: "error",
});
await server.listen();
const origin = "http://127.0.0.1:5184";
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
  args: [
    "--no-first-run",
    "--no-default-browser-check",
    "--autoplay-policy=no-user-gesture-required",
  ],
});
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
let media = Buffer.alloc(0);
const unknown = [];
const requests = [];
let tasksChecked = false,
  orderState = "paid";
let membersOnly = false,
  meFailures = 0,
  favoritesDelay = 0;
let unlocked = false,
  coins = 120,
  ticket = null,
  replyMessages = [];
const poster =
  "data:image/svg+xml," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#642840"/><stop offset="1" stop-color="#19233e"/></linearGradient></defs><rect width="400" height="600" fill="url(#g)"/><text x="40" y="420" fill="white" font-family="Arial" font-size="42">TEST STORY</text><text x="40" y="465" fill="#dca0b9" font-family="Arial" font-size="18">BROWSER FIXTURE</text></svg>',
  );
const dramas = Array.from({ length: 30 }, (_, i) => ({
  id: i + 1,
  title: i === 0 ? "Test Story" : "Fixture drama " + (i + 1),
  cover_url: poster,
  synopsis: "A story used only for browser contract tests.",
  tags: ["Romance"],
  episode_count: 3,
  published_episode_count: 3,
  free_episode_count: 1,
  coin_price_per_episode: 60,
  play_count: 12345,
  like_count: 1,
  comment_count: 0,
  liked: false,
  favorited: false,
}));
const profile = () => ({
  id: 42,
  public_id: "test-viewer",
  email: "viewer@example.com",
  display_name: "Test viewer",
  avatar_url: "",
  bio: "",
  coins,
  membership: { active: false, plan_code: null, until: null, canceling: false },
});
const play = () => ({
  drama: dramas[0],
  items: [
    {
      id: 10,
      episode_number: 1,
      title: "Episode 1",
      duration_seconds: 60,
      free: true,
      locked: false,
      progress_seconds: 0,
      comment_count: 0,
      unlock: null,
    },
    {
      id: 11,
      episode_number: 2,
      title: "Episode 2",
      duration_seconds: 60,
      free: false,
      locked: !unlocked || membersOnly,
      membership_required: membersOnly,
      progress_seconds: 0,
      comment_count: 0,
      unlock: { coin_price: 60 },
    },
    {
      id: 12,
      episode_number: 3,
      title: "Episode 3",
      duration_seconds: 60,
      free: false,
      locked: true,
      progress_seconds: 0,
      comment_count: 0,
      unlock: { coin_price: 60 },
    },
  ],
  resume: null,
  entitlement: { member: false, unlocked_episode_ids: unlocked ? [11] : [] },
});
const savedDramas = Array.from({ length: 49 }, (_, i) => ({
  ...dramas[0],
  id: 100 + i,
  title: "Saved drama " + (i + 1),
}));
const historyItems = Array.from({ length: 21 }, (_, i) => ({
  episode_id: 11 + i,
  episode_number: 2 + i,
  position_seconds: 15,
  duration_seconds: 60,
  updated_at: "2026-11-14T00:00:00Z",
  drama: dramas[i],
}));
const oldTicket = (number, subject) => ({
  id: 90,
  number,
  subject,
  category: "general",
  status: "open",
  order_no: null,
  updated_at: "2026-11-14T00:00:00Z",
  can_reply: true,
  unread_count: 0,
  csat_score: null,
});
const mergedTicket = {
  ...oldTicket("T-MERGED", "Merged playback question"),
  status: "merged",
  can_reply: false,
  merged_into: "T-MAIN",
};
const mainTicket = oldTicket("T-MAIN", "Combined conversation");
const archives = Array.from({ length: 26 }, (_, i) =>
  oldTicket("T-OLD-" + i, "Earlier conversation " + (i + 1)),
);
const supportDetail = () => ({ ticket, messages: replyMessages });
await page.setRequestInterception(true);
page.on("request", async (req) => {
  const url = new URL(req.url());
  if (url.pathname === "/fixtures/clip.webm") {
    await req.respond({ status: 200, contentType: "video/webm", body: media });
    return;
  }
  if (url.pathname === "/fixtures/caption.srt") {
    await req.respond({
      status: 200,
      contentType: "text/plain",
      body: "1\n00:00:00,000 --> 00:00:03,000\nFixture subtitle\n",
    });
    return;
  }
  if (url.pathname.startsWith("/v1/")) {
    let body = {};
    try {
      body = JSON.parse(req.postData() || "{}");
    } catch {}
    requests.push({
      path: url.pathname,
      query: url.search,
      method: req.method(),
      headers: req.headers(),
      body,
    });
    let result = { ok: true },
      status = 200;
    const p = url.pathname;
    if (p === "/v1/attr/visit") result = { pid: body.pid, active: true };
    else if (p === "/v1/home/groups")
      result = {
        groups: [
          {
            id: 1,
            name: "Trending stories",
            badge: "Popular now",
            dramas: dramas.slice(0, 9),
          },
        ],
        banners: [
          {
            id: 1,
            drama_id: 1,
            cover_url: poster,
            title: "Test Story",
            headline: "A story worth staying for.",
            kicker: "Featured drama",
          },
        ],
      };
    else if (p === "/v1/catalog") {
      const offset = Number(url.searchParams.get("offset") || 0);
      const q = url.searchParams.get("q");
      const all = q
        ? dramas.filter((d) => d.title.toLowerCase().includes(q.toLowerCase()))
        : dramas;
      result = {
        items: all.slice(offset, offset + 24),
        total: all.length,
        limit: 24,
        offset,
      };
    } else if (p === "/v1/feed")
      result = {
        items: dramas
          .slice(0, 12)
          .map((drama) => ({ drama, episode: {}, item_id: String(drama.id) })),
        next_cursor: null,
        has_more: false,
      };
    else if (p === "/v1/auth/providers")
      result = {
        email: { enabled: true },
        google: { enabled: false, client_id: null },
        facebook: { enabled: false, app_id: null },
      };
    else if (p === "/v1/auth/email/code") result = { sent: true };
    else if (p === "/v1/auth/email/authenticate")
      result = {
        token: "browser-fixture-token",
        user_id: 42,
        public_id: "test-viewer",
      };
    else if (p === "/v1/me") {
      if (req.method() === "GET" && meFailures > 0) {
        meFailures--;
        status = 503;
        result = { detail: "Temporary account outage" };
      } else result = profile();
    } else if (p === "/v1/dramas/1/play") result = play();
    else if (p === "/v1/playback")
      result = {
        allowed: body.episode_id !== 11 || unlocked,
        url: origin + "/fixtures/clip.webm",
        episode_id: body.episode_id,
        subtitles: [
          {
            language: "en",
            label: "English",
            url: origin + "/fixtures/caption.srt?token=fixture",
            is_default: true,
          },
        ],
      };
    else if (p === "/v1/unlock") {
      unlocked = true;
      coins -= 60;
      result = {
        status: "unlocked",
        source: "coin",
        episode_id: 11,
        balance_after: coins,
      };
    } else if (p.includes("/comments"))
      result = { items: [], total: 0, next_cursor: null };
    else if (p === "/v1/plans")
      result = {
        billing_mode: "one_time",
        items: [
          {
            code: "weekly",
            name_en: "Weekly pass",
            name_zh: "周卡",
            billing_mode: "subscription",
            duration_days: 7,
            recurring_interval: "week",
            price_cents: 499,
            currency: "USD",
            benefits: { items: ["Watch member episodes"] },
          },
        ],
      };
    else if (p === "/v1/coin-packs")
      result = {
        items: [
          {
            code: "starter",
            name_en: "Starter coins",
            name_zh: "金币包",
            price_cents: 299,
            currency: "USD",
            base_coins: 300,
            bonus_coins: 30,
            coins: 330,
            version_id: 1,
          },
        ],
      };
    else if (p === "/v1/billing/channels")
      result = {
        items: [
          {
            code: "stripe-test",
            name: "Stripe test fixture",
            provider: "stripe",
            enabled: true,
          },
        ],
      };
    else if (p === "/v1/commerce/checkout")
      result = {
        url: origin + "/?checkout=success#account/orders",
        session_id: "fixture-session",
        order_no: "ORDER-FIXTURE",
      };
    else if (p === "/v1/commerce/orders")
      result = {
        items: [
          {
            order_no: "ORDER-FIXTURE",
            product_type: "coin_pack",
            product_code: "starter",
            product_name: "Starter coins",
            amount_cents: 299,
            currency: "USD",
            status: orderState,
            paid_at: "2026-11-14T00:00:00Z",
            created_at: "2026-11-14T00:00:00Z",
            coins: 330,
            billing_mode: "one_time",
          },
        ],
        has_more: false,
      };
    else if (p === "/v1/me/favorites") {
      const offset = Number(url.searchParams.get("offset") || 0),
        limit = Number(url.searchParams.get("limit") || 48);
      result = {
        items: savedDramas.slice(offset, offset + limit),
        total: savedDramas.length,
      };
      if (favoritesDelay)
        await new Promise((resolve) => setTimeout(resolve, favoritesDelay));
    } else if (p === "/v1/me/history") {
      const offset = Number(url.searchParams.get("offset") || 0),
        limit = Number(url.searchParams.get("limit") || 20);
      result = {
        items: historyItems.slice(offset, offset + limit),
        total: historyItems.length,
        limit,
        offset,
      };
    } else if (p === "/v1/me/ledger")
      result = {
        items: [
          {
            amount: -60,
            balance_after: 60,
            biz_type: "episode_unlock",
            created_at: "2026-11-14T00:00:00Z",
          },
        ],
      };
    else if (p === "/v1/me/lists/likes")
      result = {
        total: 1,
        items: [{ target_type: "drama", target_id: 1, drama: dramas[0] }],
      };
    else if (p === "/v1/me/checkin") {
      if (!tasksChecked) coins += 10;
      tasksChecked = true;
      result = { amount: 10, balance: coins, replayed: false };
    } else if (p === "/v1/me/messages") result = { unread: 0, items: [] };
    else if (p === "/v1/tasks" || p === "/v1/me/tasks")
      result = {
        balance: p === "/v1/me/tasks" ? coins : 0,
        checkin: {
          checked_today: p === "/v1/me/tasks" && tasksChecked,
          streak: 1,
          amount: 10,
        },
        items: [
          {
            code: "watch_8",
            name: "Watch episodes",
            description: "Watch eight episodes.",
            amount: 20,
            progress: 3,
            target: 8,
            claimed: false,
          },
        ],
      };
    else if (p === "/v1/support/meta")
      result = {
        categories: [
          { code: "general", name_en: "General", name_zh: "一般", fields: [] },
          { code: "billing", name_en: "Billing", name_zh: "付款", fields: [] },
        ],
      };
    else if (p === "/v1/support/tickets" && req.method() === "GET") {
      const pageNumber = Number(url.searchParams.get("page") || 1),
        pageSize = Number(url.searchParams.get("page_size") || 25);
      const all = [...(ticket ? [ticket] : []), mergedTicket, ...archives];
      result = {
        items: all.slice((pageNumber - 1) * pageSize, pageNumber * pageSize),
        total: all.length,
        page: pageNumber,
        page_size: pageSize,
      };
    } else if (p === "/v1/support/tickets/T-MERGED")
      result = { ticket: mergedTicket, messages: [] };
    else if (p === "/v1/support/tickets/T-MAIN")
      result = {
        ticket: mainTicket,
        messages: [
          {
            id: 800,
            body: "Original combined message",
            author_type: "user",
            created_at: "2026-11-14T00:00:00Z",
          },
        ],
      };
    else if (p === "/v1/support/tickets" && req.method() === "POST") {
      ticket = {
        id: 1,
        number: "T-1",
        subject: body.subject,
        category: body.category,
        status: "open",
        order_no: body.order_no || null,
        created_at: "2026-11-14T00:00:00Z",
        updated_at: "2026-11-14T00:00:00Z",
        can_reply: true,
        unread_count: 0,
        csat_score: null,
      };
      replyMessages = [
        {
          id: 1,
          body: body.body,
          author_type: "user",
          created_at: "2026-11-14T00:00:00Z",
        },
      ];
      result = supportDetail();
    } else if (p === "/v1/support/tickets/T-1/messages") {
      replyMessages.push({
        id: 2,
        body: body.body,
        author_type: "user",
        created_at: "2026-11-14T00:00:00Z",
      });
      result = supportDetail();
    } else if (p === "/v1/support/tickets/T-1/close") {
      ticket.status = "closed";
      ticket.can_reply = false;
      result = supportDetail();
    } else if (p === "/v1/support/tickets/T-1/csat") {
      ticket.csat_score = body.score;
      result = supportDetail();
    } else if (p === "/v1/support/tickets/T-1") result = supportDetail();
    else if (p === "/v1/reactions") {
      if (body.kind === "favorite" && body.remove) {
        const index = savedDramas.findIndex((d) => d.id === body.target_id);
        if (index >= 0) savedDramas.splice(index, 1);
      }
      result = { ok: true };
    } else if (p.startsWith("/v1/playback/") || p.endsWith("/read"))
      result = { ok: true };
    else if (p.startsWith("/v1/legal/"))
      result = { slug: "terms", title: "Terms", body: "Test legal document." };
    else {
      unknown.push(req.method() + " " + p);
      status = 501;
      result = { detail: "Unknown browser fixture endpoint: " + p };
    }
    await req.respond({
      status,
      contentType: "application/json",
      body: JSON.stringify(result),
    });
    return;
  }
  if (
    url.protocol === "http:" ||
    url.protocol === "data:" ||
    url.protocol === "about:"
  )
    await req.continue();
  else await req.abort();
});
async function go(hash) {
  await page.goto(origin + "/" + hash, { waitUntil: "networkidle0" });
}
async function text(value) {
  await page.waitForFunction(
    (v) => document.body.innerText.includes(v),
    {},
    value,
  );
}
async function clickText(value) {
  await page.evaluate((v) => {
    const button = [...document.querySelectorAll("button,a")].find(
      (el) => el.textContent.trim() === v,
    );
    if (!button) throw Error("Missing control " + v);
    button.click();
  }, value);
}
const checks = [];
function pass(name) {
  checks.push(name);
  console.log("PASS " + name);
}
try {
  await page.goto(origin + "/?pid=fixture-plan#home", {
    waitUntil: "networkidle0",
  });
  await text("Trending stories");
  for (const width of [1280, 1440, 1920, 390]) {
    await page.setViewport({ width, height: 1000 });
    await page.screenshot({
      path: path.join(OUT, "home-" + width + ".png"),
      fullPage: true,
    });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      "horizontal overflow at " + width,
    );
    pass("home has no horizontal overflow at " + width);
  }
  await page.setViewport({ width: 1440, height: 1000 });
  await go("#preview");
  await text("30 dramas");
  assert.equal(
    await page.$$(".drama-grid .drama-card").then((x) => x.length),
    24,
  );
  await clickText("Next");
  await text("25–30 of 30");
  assert.equal(
    await page.$$(".drama-grid .drama-card").then((x) => x.length),
    6,
  );
  pass("legacy preview route supports paginated catalog");
  assert(
    requests.some(
      (request) =>
        request.path === "/v1/catalog" &&
        new URLSearchParams(request.query).get("free_only") === "true",
    ),
  );
  await go("#browse?tag=Romance&language=en&sort=popular&group=1");
  await text("30 dramas");
  assert(
    requests.some(
      (request) =>
        request.path === "/v1/catalog" &&
        new URLSearchParams(request.query).get("group") === "1" &&
        new URLSearchParams(request.query).get("sort") === "popular",
    ),
  );
  await go("#browse?q=Test%20Story");
  await text("1 drama");
  assert.equal(
    await page.$eval(
      ".catalog-filters label:nth-child(2) input",
      (input) => input.value,
    ),
    "",
  );
  assert.equal(
    await page.$eval(
      ".catalog-filters label:nth-child(3) input",
      (input) => input.value,
    ),
    "",
  );
  assert.equal(
    await page.$eval(".catalog-filters select", (select) => select.value),
    "newest",
  );
  pass(
    "group and preview filters are sent and route changes reset stale filters",
  );

  await page.type(".catalog-filters label:nth-child(2) input", "Romance");
  await page.type(".catalog-filters label:nth-child(3) input", "en");
  await page.select(".catalog-filters select", "popular");
  await clickText("Apply filters");
  await text("1 drama");
  assert(
    new URLSearchParams(page.url().split("?")[1]).get("tag") === "Romance",
  );
  await page.reload({ waitUntil: "networkidle0" });
  assert.equal(
    await page.$eval(".catalog-filters select", (el) => el.value),
    "popular",
  );
  assert.equal(
    await page.$eval(
      ".catalog-filters label:nth-child(3) input",
      (el) => el.value,
    ),
    "en",
  );
  await clickText("Clear filters");
  await text("30 dramas");
  assert(page.url().endsWith("#browse"));
  pass("applied catalog filters survive reload and clear through the URL");
  await go("#me");
  await text("Sign in to like and save dramas");
  assert(await page.$(".member-premium"));
  assert.equal(
    await page.$$(".member-actions a").then((items) => items.length),
    5,
  );
  assert.equal(
    await page.$$(".library-tabs a").then((items) => items.length),
    3,
  );
  pass("guest account preserves the full overview and library navigation");

  media = Buffer.from(
    await page.evaluate(async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 160;
      canvas.height = 240;
      const context = canvas.getContext("2d");
      const stream = canvas.captureStream(12);
      const recorder = new MediaRecorder(stream, {
        mimeType: "video/webm;codecs=vp8",
      });
      const chunks = [];
      recorder.ondataavailable = (e) => chunks.push(e.data);
      const done = new Promise((resolve) => (recorder.onstop = resolve));
      recorder.start();
      let i = 0;
      const timer = setInterval(() => {
        context.fillStyle = i++ % 2 ? "#56243b" : "#22375b";
        context.fillRect(0, 0, 160, 240);
        context.fillStyle = "white";
        context.font = "16px Arial";
        context.fillText("Test episode", 20, 120);
      }, 80);
      await new Promise((resolve) => setTimeout(resolve, 3000));
      recorder.stop();
      clearInterval(timer);
      await done;
      stream.getTracks().forEach((track) => track.stop());
      return Array.from(
        new Uint8Array(
          await new Blob(chunks, { type: "video/webm" }).arrayBuffer(),
        ),
      );
    }),
  );
  await go("#watch/1");
  await page.waitForFunction(
    () => document.querySelector("video")?.currentTime > 0.2,
  );
  await page.waitForSelector('select[aria-label="Subtitles"]');
  await page.select('select[aria-label="Subtitles"]', "off");
  await page.waitForFunction(
    () => document.querySelector("video")?.textTracks[0]?.mode === "disabled",
  );
  await page.select('select[aria-label="Playback speed"]', "1.5");
  assert.equal(await page.$eval("video", (video) => video.playbackRate), 1.5);
  pass(
    "real fixture media decodes and legacy SRT captions/speed controls work",
  );
  await go("#watch/1?episode=11");
  await text("The story continues");
  assert.equal(
    requests.filter(
      (r) => r.path === "/v1/playback" && r.body.episode_id === 11,
    ).length,
    0,
  );
  pass("guest does not request locked playback");
  await clickText("Sign in to continue");
  await page.waitForSelector("[role=dialog]");
  await page.type("input[type=email]", "viewer@example.com");
  await clickText("Send verification code");
  await page.waitForSelector("input[autocomplete=one-time-code]");
  await page.type("input[autocomplete=one-time-code]", "123456");
  await clickText("Sign in / Create account");
  await page.waitForFunction(() =>
    document
      .querySelector("[role=dialog]")
      ?.textContent.includes("Confirm unlock"),
  );
  await clickText("Confirm unlock");
  await page.waitForFunction(() => !document.querySelector("[role=dialog]"));
  assert.equal(requests.filter((r) => r.path === "/v1/unlock").length, 1);
  assert(
    requests.find((r) => r.path === "/v1/unlock").headers["idempotency-key"],
  );
  assert.equal(coins, 60);
  pass("login restores unlock intent with one idempotent deduction");
  assert.equal(
    requests.find((request) => request.path === "/v1/auth/email/authenticate")
      .headers["x-jjtv-attribution"],
    "fixture-plan",
  );
  pass("validated landing attribution survives login");
  membersOnly = true;
  await page.reload({ waitUntil: "networkidle0" });
  await text("This episode requires a membership.");
  assert.equal(await page.$(".locked-player button"), null);
  assert.equal(
    requests.filter((request) => request.path === "/v1/unlock").length,
    1,
  );
  await clickText("See memberships");
  await text("Membership plans");
  membersOnly = false;
  pass("members-only episodes offer membership without a coin deduction");
  await page.screenshot({
    path: path.join(OUT, "watch-desktop.png"),
    fullPage: true,
  });
  await go("#coins?drama=1&episode=11");
  await text("330");
  await clickText("Coins");
  await page.waitForFunction(() => location.hash.includes("tab=coins"));
  await page.reload({ waitUntil: "networkidle0" });
  assert.equal(
    await page.$eval(".price-tabs button:nth-child(2)", (el) =>
      el.getAttribute("aria-pressed"),
    ),
    "true",
  );
  assert(await page.$("#membership-plans"));
  assert(await page.$("#coin-packs"));
  await text("300 base + 30 bonus coins");
  await text("Access for 7 days");
  await text("per week");
  pass(
    "price tabs retain deep links and show configured base and bonus values",
  );
  await clickText("Buy coins");
  await text("Payment confirmed.");
  const checkout = requests.find((r) => r.path === "/v1/commerce/checkout");
  assert.equal(checkout.body.product_type, "coin_pack");
  assert.equal(checkout.body.code, "starter");
  assert(checkout.body.idempotency_key);
  assert.equal(checkout.body.attribution_pid, "fixture-plan");
  assert(checkout.body.success_url.endsWith("#watch/1?episode=11"));
  pass("coin purchase uses Stripe product contract and verified order return");
  await go("#account/history");
  await text("Episode 2");
  assert(
    (
      await page.$eval(".history-item", (el) => el.getAttribute("href"))
    ).includes("episode=11"),
  );
  pass("history links to the watched episode");
  assert.equal(await page.$(".checkout-notice"), null);
  pass("payment return notice is cleared after navigation");
  await clickText("Next");
  await text("Episode 22");
  assert.equal(await page.$$(".history-item").then((items) => items.length), 1);
  assert(
    requests.some(
      (request) =>
        request.path === "/v1/me/history" &&
        new URLSearchParams(request.query).get("offset") === "20",
    ),
  );
  await go("#account/favorites");
  await text("Saved drama 48");
  assert.equal(
    await page.$eval(
      '.consumer-header nav a[aria-current="page"]',
      (el) => el.textContent,
    ),
    "Me",
  );
  await clickText("Next");
  await text("Saved drama 49");
  assert.equal(await page.$$(".drama-card").then((items) => items.length), 1);
  pass("older history and saved dramas remain accessible through paging");
  await page.click('button[aria-label="Remove Saved drama 49 from saved"]');
  await text("Saved drama 48");
  assert.equal(savedDramas.length, 48);
  assert.equal(await page.$$(".saved-card").then((items) => items.length), 48);
  pass("removing the last saved item returns to the previous valid page");
  await go("#account/likes");
  await text("Test Story");
  assert(await page.$(".drama-card"));
  pass("liked dramas load from the account list endpoint");
  favoritesDelay = 350;
  await go("#account");
  await page.evaluate(() => (window.location.hash = "account/favorites"));
  await page.waitForRequest(
    (request) => new URL(request.url()).pathname === "/v1/me/favorites",
  );
  await page.evaluate(() => (window.location.hash = "account/orders"));
  await text("ORDER-FIXTURE");
  await new Promise((resolve) => setTimeout(resolve, 500));
  assert(
    (await page.$eval("table", (table) => table.innerText)).includes(
      "ORDER-FIXTURE",
    ),
  );
  favoritesDelay = 0;
  pass("a late account request cannot overwrite the selected section");
  await clickText("Get help");
  await page.waitForSelector('input[maxlength="200"]');
  assert.equal(
    await page.$eval(
      " .support-main form label:nth-of-type(3) input",
      (el) => el.value,
    ),
    "ORDER-FIXTURE",
  );
  assert.equal(
    await page.$eval(".support-page select", (el) => el.value),
    "billing",
  );
  pass("order support opens a billing ticket with the order number preserved");
  await go("#account/orders");
  meFailures = 2;
  await page.reload({ waitUntil: "networkidle0" });
  await text("Your session is saved.");
  assert.equal(
    await page.evaluate(() => localStorage.getItem("jjtv_pc_token")),
    "browser-fixture-token",
  );
  await clickText("Retry account connection");
  await page.waitForSelector(".profile-button");
  await page.waitForFunction(() => !document.querySelector(".auth-notice"));
  await go("#account/overview");
  await page.waitForSelector(".profile-form");
  meFailures = 1;
  await clickText("Save changes");
  await text("Temporary account outage");
  assert(await page.$(".profile-button"));
  assert.equal(
    await page.evaluate(() => localStorage.getItem("jjtv_pc_token")),
    "browser-fixture-token",
  );
  await clickText("Retry account connection");
  await page.waitForFunction(() => !document.querySelector(".auth-notice"));
  pass("bootstrap and refresh outages preserve the session and support retry");
  await go("#support");
  await text("Merged playback question");
  await clickText("Older tickets");
  await text("Earlier conversation 10");
  assert(
    requests.some(
      (request) =>
        request.path === "/v1/support/tickets" &&
        new URLSearchParams(request.query).get("page") === "2",
    ),
  );
  await clickText("Newer tickets");
  await text("Merged playback question");
  await go("#support?ticket=T-MERGED");
  await text("This conversation was merged");
  await clickText("Open main conversation");
  await text("Original combined message");
  assert(page.url().includes("ticket=T-MAIN"));
  pass(
    "older support tickets and merged source links reach the main conversation",
  );
  await clickText("New ticket");
  await page.type('input[maxlength="200"]', "Playback help");
  await page.type("textarea", "An episode needs help.");
  await clickText("Send ticket");
  await text("An episode needs help.");
  await page.type("textarea", "More details.");
  await clickText("Send reply");
  await text("More details.");
  await clickText("Close ticket");
  await clickText("Confirm close");
  await text("How was your support experience?");
  await clickText("5/5");
  await page.waitForFunction(
    () => !document.body.innerText.includes("How was your support experience?"),
  );
  assert.equal(ticket.csat_score, 5);
  await text("Your rating: 5/5");
  await page.reload({ waitUntil: "networkidle0" });
  await text("Your rating: 5/5");
  pass("support create/reply/close/rating is wired to authenticated endpoints");
  await page.screenshot({
    path: path.join(OUT, "support-desktop.png"),
    fullPage: true,
  });
  await go("#account/overview");
  await clickText("Sign out");
  await page.click(".sign-in");
  await page.waitForSelector("[role=dialog]");
  await page.$eval("[role=dialog]", (dialog) => {
    const focusable = dialog.querySelectorAll(
      'button:not(:disabled),a[href],input,select,textarea,[tabindex="0"]',
    );
    focusable[focusable.length - 1].focus();
  });
  await page.keyboard.press("Tab");
  assert.equal(
    await page.evaluate(() =>
      document.activeElement?.getAttribute("aria-label"),
    ),
    "Close dialog",
  );
  await page.keyboard.press("Escape");
  assert.equal(await page.$("[role=dialog]"), null);
  assert.equal(
    await page.evaluate(() => document.activeElement?.textContent),
    "Sign in",
  );
  pass("dialog Escape closes and restores keyboard focus");
  await go("#rewards");
  await page.waitForSelector(".task-checkin");
  await clickText("Check in");
  await page.waitForFunction(
    () => document.querySelectorAll('[role="dialog"]').length === 2,
  );
  await page.keyboard.press("Escape");
  assert.equal(
    await page.$$('[role="dialog"]').then((items) => items.length),
    1,
  );
  assert(await page.$(".rewards-modal"));
  pass("Escape dismisses only the nested login while keeping daily tasks open");
  await clickText("Check in");
  await page.waitForSelector("input[type=email]");
  await page.type("input[type=email]", "viewer@example.com");
  const sendsBefore = requests.filter(
    (r) => r.path === "/v1/auth/email/code",
  ).length;
  await clickText("I already have a code");
  await page.type("input[autocomplete=one-time-code]", "123456");
  const checkinsBefore = requests.filter(
    (r) => r.path === "/v1/me/checkin",
  ).length;
  await clickText("Sign in / Create account");
  await text("You earned 10 coins.");
  assert.equal(
    requests.filter((r) => r.path === "/v1/auth/email/code").length,
    sendsBefore,
  );
  assert.equal(
    requests.filter((r) => r.path === "/v1/me/checkin").length,
    checkinsBefore + 1,
  );
  assert.equal(
    await page.$eval(".tasks-intro span", (el) => el.textContent),
    coins + " coins",
  );
  await text("Checked in today");
  pass(
    "an existing email code restores one check-in and refreshes private rewards",
  );
  for (const width of [1440, 390]) {
    await page.setViewport({ width, height: 1000 });
    for (const route of ["#account", "#price?tab=coins", "#rewards"]) {
      await go(route);
      await page.waitForSelector(
        route === "#rewards"
          ? ".task-row"
          : route === "#account"
            ? ".library-tabs"
            : ".coin-pack-card",
      );
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        route + " overflow at " + width,
      );
      await page.screenshot({
        path: path.join(
          OUT,
          route.split("?")[0].slice(1) + "-detail-" + width + ".png",
        ),
        fullPage: true,
      });
    }
  }
  pass("account, price and reward details fit desktop and narrow screens");
  await page.setViewport({ width: 1440, height: 1000 });
  await go("#account/overview");
  await clickText("Sign out");
  await page.goto(origin + "/?checkout=success#account/orders", {
    waitUntil: "networkidle0",
  });
  await text("Sign in to check your payment and updated balance.");
  await page.click(".checkout-actions button");
  await page.waitForSelector("input[type=email]");
  await page.type("input[type=email]", "viewer@example.com");
  await clickText("I already have a code");
  await page.type("input[autocomplete=one-time-code]", "123456");
  await clickText("Sign in / Create account");
  await text("Payment confirmed.");
  pass("payment return resumes exact order verification after signing back in");
  orderState = "failed";
  await page.goto(origin + "/?checkout=success#account/orders", {
    waitUntil: "networkidle0",
  });
  await text("Payment was not completed.");
  assert.equal(await page.$(".checkout-confirmed"), null);
  await page.evaluate(() => sessionStorage.removeItem("jjtv_checkout_order"));
  await page.goto(origin + "/?checkout=success#account/orders", {
    waitUntil: "networkidle0",
  });
  await text("Open your order history to check payment confirmation.");
  assert.equal(await page.$(".checkout-confirmed"), null);
  pass("failed and unknown payment returns never claim a confirmed payment");
  assert.deepEqual(unknown, []);
  assert.deepEqual(errors, []);
  pass("no unknown API contracts or uncaught browser errors");
  await fs.writeFile(
    path.join(OUT, "report.json"),
    JSON.stringify(
      { mocked: true, providerCalls: false, checks, screenshots: OUT },
      null,
      2,
    ),
  );
  console.log("REPORT " + path.join(OUT, "report.json"));
} catch (error) {
  console.error("BODY", await page.evaluate(() => document.body.innerText));
  console.error("BROWSER_ERRORS", errors);
  console.error("LAST_REQUESTS", JSON.stringify(requests.slice(-8)));
  await page.screenshot({
    path: path.join(OUT, "failure.png"),
    fullPage: true,
  });
  throw error;
} finally {
  await browser.close();
  await server.close();
}
