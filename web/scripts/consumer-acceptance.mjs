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
let restoredLikeScenario = false,
  restoredFeedSnapshotLiked = false,
  feedServerLiked = false,
  releaseRestoredFeed,
  restoredFeedStarted;
let feedCursorScenario = false,
  expireFeedOwner = false,
  feedGuestFirstFailed = false;
const invalidFeedCursors = [];
let privateComments = false,
  holdOldComments = false,
  expireCommentOwner = false,
  releaseOldComments,
  oldCommentsStarted;
let checkoutConflictCode = null,
  cancelConflictCode = null,
  checkoutConflictSequence = [];
const conflictMessages = {
  pending_subscription_checkout: "Resume or cancel your existing checkout.",
  checkout_payment_pending:
    "Your payment is awaiting its invoice confirmation.",
  checkout_in_progress: "Your checkout session is still being prepared.",
  checkout_expired: "This payment attempt has expired.",
  checkout_recovery_required:
    "Please contact support about this pending order.",
  checkout_already_paid: "This order has already been paid.",
};
const conflictActions = {
  pending_subscription_checkout: "resume_or_cancel",
  checkout_payment_pending: "wait_for_payment",
  checkout_in_progress: "retry",
  checkout_expired: "new_purchase",
  checkout_recovery_required: "contact_support",
  checkout_already_paid: "view_orders",
};
function fixtureConflict(code) {
  return {
    detail: {
      code,
      message: conflictMessages[code],
      order_no: "ORDER-FIXTURE",
      action: conflictActions[code],
      ...(code === "pending_subscription_checkout"
        ? { checkout_url: origin + "/?checkout=cancel#price" }
        : {}),
    },
  };
}
let expireSupportA = false,
  holdOldSupport = false,
  holdSecondSupport = false;
let releaseOldSupport,
  releaseSecondSupport,
  oldSupportStarted,
  secondSupportStarted;
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
const profile = (second = false) => ({
  id: second ? 84 : 42,
  public_id: second ? "second-viewer" : "test-viewer",
  email: second ? "second@example.com" : "viewer@example.com",
  display_name: second ? "Second viewer" : "Test viewer",
  avatar_url: "",
  bio: "",
  coins,
  membership: { active: false, plan_code: null, until: null, canceling: false },
});
const play = () => ({
  drama: {
    ...dramas[0],
    synopsis:
      "A long browser fixture synopsis makes the expand and collapse controls observable. ".repeat(
        9,
      ),
  },
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
    const secondUser =
      req.headers().authorization === "Bearer browser-fixture-token-b";
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
    else if (p === "/v1/config")
      result = {
        tabs: [
          {
            code: "discover",
            title_en: "Discover",
            title_zh: "发现",
            position: 0,
            locked: true,
            group_id: null,
          },
        ],
        copies: {},
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
    } else if (p === "/v1/feed") {
      const authorization = req.headers().authorization;
      result = {
        session_id: "fixture-feed-session",
        page_size: Number(url.searchParams.get("limit") || 12),
        items: dramas
          .slice(0, Number(url.searchParams.get("limit") || 12))
          .map((drama, index) => ({
            drama: {
              ...drama,
              ...(index === 2 ? { free_episode_count: 0 } : {}),
              ...(restoredLikeScenario && index === 0
                ? {
                    liked: authorization ? restoredFeedSnapshotLiked : false,
                    like_count:
                      1 +
                      Number(
                        Boolean(authorization) && restoredFeedSnapshotLiked,
                      ),
                  }
                : {}),
            },
            episode: {
              id: index === 0 ? 10 : 1000 + index,
              episode_number: 1,
              title: "Episode 1",
              duration_seconds: 60,
              free: index !== 2,
              comment_count: 0,
            },
            item_id: String(drama.id),
            resume_seconds: 0,
            entry_reason: "browser_fixture",
            score: 1,
          })),
        next_cursor: null,
        has_more: false,
        preload: { fetch_when_remaining: 3 },
      };
      if (feedCursorScenario) {
        const cursor = url.searchParams.get("cursor");
        if (
          cursor &&
          (cursor !== "OWNER_A_CURSOR" ||
            authorization !== "Bearer browser-fixture-token")
        ) {
          invalidFeedCursors.push({
            cursor,
            authenticated: Boolean(authorization),
          });
          status = 409;
          result = { detail: "Feed cursor belongs to another viewer" };
        } else if (!authorization && !feedGuestFirstFailed) {
          feedGuestFirstFailed = true;
          status = 503;
          result = { detail: "Feed first page temporarily unavailable" };
        } else if (authorization) {
          result.next_cursor = "OWNER_A_CURSOR";
          result.has_more = true;
        }
      }
      if (restoredLikeScenario && authorization) {
        restoredFeedStarted?.();
        await new Promise((resolve) => {
          releaseRestoredFeed = resolve;
        });
      }
    } else if (p === "/v1/auth/providers")
      result = {
        email: { enabled: true },
        google: { enabled: false, client_id: null },
        facebook: { enabled: false, app_id: null },
      };
    else if (p === "/v1/auth/email/code") result = { sent: true };
    else if (p === "/v1/auth/email/authenticate")
      result = {
        token:
          body.email === "second@example.com"
            ? "browser-fixture-token-b"
            : "browser-fixture-token",
        user_id: body.email === "second@example.com" ? 84 : 42,
        public_id:
          body.email === "second@example.com" ? "second-viewer" : "test-viewer",
      };
    else if (p === "/v1/me") {
      if (req.method() === "GET" && meFailures > 0) {
        meFailures--;
        status = 503;
        result = { detail: "Temporary account outage" };
      } else result = profile(secondUser);
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
    } else if (p.includes("/comments")) {
      if (!privateComments) result = { items: [], total: 0, next_cursor: null };
      else if (req.method() === "POST") {
        if (secondUser && expireCommentOwner) {
          status = 401;
          result = { detail: "Comment session expired" };
        } else result = { status: "pending" };
      } else if (secondUser) {
        if (holdOldComments && url.searchParams.get("cursor")) {
          oldCommentsStarted?.();
          await new Promise((resolve) => {
            releaseOldComments = resolve;
          });
        }
        result = {
          items: [
            {
              id: url.searchParams.get("cursor") ? 9002 : 9001,
              body: url.searchParams.get("cursor")
                ? "PRIVATE LATE PENDING COMMENT FROM FIRST ACCOUNT"
                : "PRIVATE PENDING COMMENT FROM FIRST ACCOUNT",
              status: "pending",
              user: { id: 84, display_name: "Second viewer" },
              created_at: "2026-11-14T00:00:00Z",
            },
          ],
          total: 2,
          next_cursor: url.searchParams.get("cursor") ? null : 9001,
        };
      } else if (req.headers().authorization) {
        status = 404;
        result = { detail: "Comments are unavailable" };
      } else result = { items: [], total: 0, next_cursor: null };
    } else if (p === "/v1/plans")
      result = {
        billing_mode: "one_time",
        items: [
          {
            code: "weekly",
            name_en: "Weekly pass",
            name_zh: "周卡",
            billing_mode: "subscription",
            duration_days: null,
            recurring_interval: "week",
            price_cents: 499,
            currency: "USD",
            benefits: { items: ["Watch member episodes"] },
          },
          {
            code: "weekly-once",
            name_en: "Seven-day access",
            name_zh: "七天权益",
            billing_mode: "one_time",
            duration_days: 7,
            recurring_interval: null,
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
    else if (p === "/v1/commerce/checkout") {
      const code = checkoutConflictSequence.length
        ? checkoutConflictSequence.shift()
        : checkoutConflictCode;
      if (code === "service_unavailable") {
        status = 503;
        result = { detail: "Temporary checkout gateway unavailable" };
      } else if (code) {
        status = 409;
        result = fixtureConflict(code);
      } else
        result = {
          url: origin + "/?checkout=success#account/orders",
          session_id: "fixture-session",
          order_no: "ORDER-FIXTURE",
        };
    } else if (p === "/v1/commerce/orders/ORDER-FIXTURE/cancel") {
      if (cancelConflictCode) {
        status = 409;
        result = fixtureConflict(cancelConflictCode);
      } else
        result = {
          order_no: "ORDER-FIXTURE",
          status: "failed",
          canceled: true,
        };
    } else if (p === "/v1/commerce/orders")
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
      const all = secondUser
        ? []
        : [...(ticket ? [ticket] : []), mergedTicket, ...archives];
      result = {
        items: all.slice((pageNumber - 1) * pageSize, pageNumber * pageSize),
        total: all.length,
        page: pageNumber,
        page_size: pageSize,
      };
      if (secondUser) {
        status = 503;
        result = { detail: "Second account support list unavailable" };
      }
    } else if (p === "/v1/support/tickets/T-NOT-FOUND") {
      status = 404;
      result = { detail: "Ticket not found" };
    } else if (p === "/v1/support/tickets/T-MERGED")
      result = { ticket: mergedTicket, messages: [] };
    else if (p === "/v1/support/tickets/T-MAIN") {
      if (holdOldSupport && !secondUser) {
        oldSupportStarted?.();
        await new Promise((resolve) => {
          releaseOldSupport = resolve;
        });
      }
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
    } else if (p === "/v1/support/tickets" && req.method() === "POST") {
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
    } else if (p === "/v1/support/tickets/T-1") {
      if (secondUser) {
        if (holdSecondSupport) {
          secondSupportStarted?.();
          await new Promise((resolve) => {
            releaseSecondSupport = resolve;
          });
        }
        status = 404;
        result = { detail: "Ticket not found" };
      } else if (expireSupportA) {
        status = 401;
        result = { detail: "Session expired" };
      } else result = supportDetail();
    } else if (p === "/v1/reactions") {
      if (body.kind === "favorite" && body.remove) {
        const index = savedDramas.findIndex(
          (drama) => drama.id === body.target_id,
        );
        if (index >= 0) savedDramas.splice(index, 1);
      }
      if (
        restoredLikeScenario &&
        body.kind === "like" &&
        body.target_id === 1
      ) {
        feedServerLiked = true;
        result = { active: true };
      } else if (
        feedCursorScenario &&
        expireFeedOwner &&
        req.headers().authorization
      ) {
        status = 401;
        result = { detail: "Feed owner session expired" };
      } else result = { ok: true };
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
    url.origin === origin ||
    ["data:", "about:", "blob:"].includes(url.protocol)
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
async function assertFeedViewport(index, label) {
  await page.waitForFunction(
    (index) => {
      const scroll = document.querySelector(".feed-scroll"),
        item = document.querySelector('.feed-item[data-index="' + index + '"]');
      return (
        scroll &&
        item &&
        Math.abs(
          item.getBoundingClientRect().top - scroll.getBoundingClientRect().top,
        ) < 0.5
      );
    },
    {},
    index,
  );
  const geometry = await page.evaluate((index) => {
    const scroll = document.querySelector(".feed-scroll"),
      item = document.querySelector('.feed-item[data-index="' + index + '"]');
    return {
      windowY: window.scrollY,
      containerTop: scroll.getBoundingClientRect().top,
      itemTop: item.getBoundingClientRect().top,
      innerScrollTop: scroll.scrollTop,
    };
  }, index);
  assert.equal(
    geometry.windowY,
    0,
    label + " moved the outer page: " + JSON.stringify(geometry),
  );
  assert.equal(
    geometry.containerTop,
    64,
    label + " moved the feed container: " + JSON.stringify(geometry),
  );
  assert.equal(
    geometry.itemTop,
    64,
    label + " did not align the active item: " + JSON.stringify(geometry),
  );
  if (index > 0)
    assert(
      geometry.innerScrollTop > 0,
      label + " did not scroll the inner feed",
    );
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
  await go("#foryou");
  await page.waitForFunction(
    () => document.querySelector(".for-you-page video")?.currentTime > 0.2,
  );
  assert.equal(
    await page.$$(".for-you-page video").then((videos) => videos.length),
    1,
  );
  await page.$eval(".for-you-page video", (video) => video.pause());
  const reactionsBeforeFeedLogin = requests.filter(
    (request) => request.path === "/v1/reactions",
  ).length;
  await page.click(
    '.feed-item[data-index="0"] button[aria-label="Like drama"]',
  );
  await page.waitForSelector('[role="dialog"]');
  assert.equal(
    requests.filter((request) => request.path === "/v1/reactions").length,
    reactionsBeforeFeedLogin,
  );
  await page.keyboard.press("Escape");
  await page.screenshot({
    path: path.join(OUT, "for-you-desktop.png"),
    fullPage: true,
  });
  const secondFeedPlayback = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/v1/playback" &&
      JSON.parse(request.postData() || "{}").episode_id === 1001,
  );
  await page.click('button[aria-label="Next episode"]');
  await secondFeedPlayback;
  await page.waitForFunction(() => {
    const video = document.querySelector(".for-you-page video");
    return (
      video?.closest(".feed-item")?.dataset.index === "1" &&
      video.currentTime > 0.2
    );
  });
  await page.$eval(".for-you-page video", (video) => video.pause());
  await assertFeedViewport(1, "Manual Next to free episode");
  pass(
    "vertical feed plays the active free episode, protects guest reactions and advances to the next video",
  );
  await page.click('button[aria-label="Next episode"]');
  await page.waitForFunction(() => {
    const item = document.querySelector('.feed-item[data-index="2"]'),
      scroll = document.querySelector(".feed-scroll");
    return (
      !document.querySelector(".for-you-page video") &&
      Math.abs(
        item.getBoundingClientRect().top - scroll.getBoundingClientRect().top,
      ) < 4
    );
  });
  assert.equal(
    requests.filter(
      (request) =>
        request.path === "/v1/playback" && request.body.episode_id === 1002,
    ).length,
    0,
  );
  assert.equal(
    await page.$eval('.feed-item[data-index="2"] .feed-locked a', (link) =>
      link.getAttribute("href"),
    ),
    "#watch/3?episode=1002",
  );
  await assertFeedViewport(2, "Manual Next to locked episode");
  pass(
    "a locked feed episode opens the actual watch route without requesting media authorization",
  );
  await page.click('button[aria-label="Previous episode"]');
  await page.waitForFunction(() => {
    const video = document.querySelector(".for-you-page video");
    return (
      video?.closest(".feed-item")?.dataset.index === "1" &&
      video.currentTime > 0.2
    );
  });
  await page.$eval(".for-you-page video", async (video) => {
    window.__consumerAutomaticEnded = false;
    video.pause();
    video.addEventListener(
      "ended",
      () => {
        window.__consumerAutomaticEnded = true;
      },
      { once: true },
    );
    if (Number.isFinite(video.duration) && video.duration > 0.4)
      video.currentTime = video.duration - 0.25;
    await video.play();
  });
  await page.waitForFunction(
    () =>
      window.__consumerAutomaticEnded === true &&
      !document.querySelector(".for-you-page video"),
  );
  await assertFeedViewport(2, "Actual media ended to locked episode");
  assert.equal(
    requests.filter(
      (request) =>
        request.path === "/v1/playback" && request.body.episode_id === 1002,
    ).length,
    0,
  );
  pass(
    "real media ending advances only the inner feed while the page and active item remain at the 64px header",
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
  await page.$eval("video", (video) => video.pause());
  const playerLayout = await page.evaluate(() => {
    const frame = document
        .querySelector(".video-frame")
        .getBoundingClientRect(),
      side = document.querySelector(".watch-sidebar").getBoundingClientRect();
    return {
      ratio: frame.width / frame.height,
      height: frame.height,
      frameRight: frame.right,
      sideLeft: side.left,
      sideWidth: side.width,
      columns: getComputedStyle(
        document.querySelector(".episode-grid"),
      ).gridTemplateColumns.split(" ").length,
    };
  });
  assert(Math.abs(playerLayout.ratio - 9 / 16) < 0.01);
  assert(playerLayout.height >= 600 && playerLayout.height <= 740);
  assert(playerLayout.sideLeft > playerLayout.frameRight);
  assert(Math.abs(playerLayout.sideWidth - 400) < 3);
  assert.equal(playerLayout.columns, 10);
  const collapsedSynopsis = await page.$eval(
    ".synopsis",
    (el) => el.getBoundingClientRect().height,
  );
  await clickText("more");
  assert.equal(
    await page.$eval(".synopsis-more", (button) =>
      button.getAttribute("aria-expanded"),
    ),
    "true",
  );
  assert(
    (await page.$eval(".synopsis", (el) => el.getBoundingClientRect().height)) >
      collapsedSynopsis,
  );
  await clickText("less");
  pass(
    "desktop player keeps a portrait stage, right-side episode grid and expandable synopsis",
  );
  await page.click("aria/Comment");
  await page.waitForSelector(".watch-comments-modal");
  assert(
    await page.$eval(".watch-comments-modal", (dialog) =>
      dialog.contains(document.activeElement),
    ),
  );
  await page.keyboard.press("Tab");
  assert.equal(
    await page.evaluate(() =>
      document.activeElement?.getAttribute("aria-label"),
    ),
    "Close dialog",
  );
  await page.keyboard.press("Escape");
  assert.equal(await page.$(".watch-comments-modal"), null);
  assert.equal(
    await page.evaluate(() => document.activeElement?.textContent),
    "Comment",
  );
  pass(
    "player comments open a keyboard-focused dialog and restore the comment control",
  );
  await page.evaluate(() => {
    history.replaceState(
      {},
      "",
      "/?checkout=success&code=fixture-oauth&state=fixture-state#watch/1?episode=10",
    );
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (value) => {
          window.__fixtureSharedLink = value;
        },
      },
    });
  });
  await page.click("aria/Share");
  await page.waitForSelector('input[aria-label="Drama link"]');
  const sharedLink = await page.$eval(
    'input[aria-label="Drama link"]',
    (input) => input.value,
  );
  const sharedUrl = new URL(sharedLink);
  assert.equal(sharedUrl.origin, origin);
  assert.equal(sharedUrl.search, "");
  assert.equal(sharedUrl.hash, "#watch/1?episode=10");
  await clickText("Copy link");
  await text("Link copied");
  assert.equal(
    await page.evaluate(() => window.__fixtureSharedLink),
    sharedLink,
  );
  await page.keyboard.press("Escape");
  pass(
    "player sharing copies the selected episode link without checkout or OAuth query data",
  );
  await page.screenshot({
    path: path.join(OUT, "watch-desktop.png"),
    fullPage: true,
  });
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
    path: path.join(OUT, "membership-paywall-destination.png"),
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
  const membershipCards = await page.$$eval(".membership-card", (cards) =>
    cards.map((card) => card.textContent),
  );
  assert(!membershipCards[0].includes("Access for"));
  assert(membershipCards[0].includes("per week"));
  assert(membershipCards[1].includes("Access for 7 days"));
  assert(!membershipCards[1].includes("per week"));
  pass(
    "subscriptions show billing cadence while one-time plans show fixed access days",
  );
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
  for (const width of [1280, 1440, 1920, 390]) {
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
  const openPrice = async () => {
    await go("#price");
    await page.reload({ waitUntil: "networkidle0" });
    await page.waitForFunction(() =>
      [...document.querySelectorAll(".membership-card button")].some(
        (button) =>
          button.textContent.trim() === "Subscribe" && !button.disabled,
      ),
    );
  };
  const checkouts = () =>
    requests.filter((request) => request.path === "/v1/commerce/checkout");
  const lastCheckout = () => checkouts().at(-1);
  orderState = "paid";
  checkoutConflictCode = "pending_subscription_checkout";
  await openPrice();
  await clickText("Subscribe");
  await text("A membership checkout is already open");
  await page.setViewport({ width: 390, height: 1000 });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({
    path: path.join(OUT, "checkout-recovery-390.png"),
    fullPage: true,
  });
  await page.setViewport({ width: 1440, height: 1000 });
  assert(
    await page.$$eval(".membership-card button", (buttons) =>
      buttons.every((button) => button.disabled),
    ),
  );
  const beforeResume = checkouts().length;
  await clickText("Continue checkout");
  await text("Checkout was cancelled.");
  assert.equal(checkouts().length, beforeResume);
  assert.equal(
    await page.evaluate(() => sessionStorage.getItem("jjtv_checkout_order")),
    "ORDER-FIXTURE",
  );
  pass(
    "a pending subscription can resume its existing checkout without creating another session",
  );
  await openPrice();
  await clickText("Subscribe");
  await text("A membership checkout is already open");
  const cancelledKey = lastCheckout().body.idempotency_key;
  await clickText("Cancel pending checkout");
  await text("Pending checkout cancelled. You can choose your plan again.");
  assert(
    requests.some(
      (request) =>
        request.path === "/v1/commerce/orders/ORDER-FIXTURE/cancel" &&
        request.method === "POST",
    ),
  );
  checkoutConflictCode = null;
  await clickText("Subscribe");
  await text("Payment confirmed.");
  assert.notEqual(lastCheckout().body.idempotency_key, cancelledKey);
  pass(
    "confirmed checkout cancellation releases the UI and creates a fresh purchase intent",
  );
  checkoutConflictCode = "service_unavailable";
  await openPrice();
  await clickText("Buy coins");
  await text("Temporary checkout gateway unavailable");
  const unknownCoinKey = lastCheckout().body.idempotency_key;
  checkoutConflictCode = "pending_subscription_checkout";
  await clickText("Subscribe");
  await text("A membership checkout is already open");
  await clickText("Cancel pending checkout");
  await text("Pending checkout cancelled. You can choose your plan again.");
  checkoutConflictCode = null;
  await clickText("Buy coins");
  await text("Payment confirmed.");
  assert.equal(lastCheckout().body.product_type, "coin_pack");
  assert.equal(lastCheckout().body.idempotency_key, unknownCoinKey);
  pass(
    "cancelling a membership preserves an unrelated coin purchase key whose previous result was unknown",
  );
  checkoutConflictSequence = ["checkout_in_progress", null];
  await openPrice();
  await clickText("Subscribe");
  await text("Checkout is being prepared");
  const retryKey = lastCheckout().body.idempotency_key;
  await clickText("Retry checkout");
  await text("Payment confirmed.");
  assert.equal(lastCheckout().body.idempotency_key, retryKey);
  pass("an in-progress checkout retries the original idempotency key");
  checkoutConflictSequence = ["checkout_expired", "service_unavailable", null];
  await openPrice();
  await clickText("Subscribe");
  await text("This checkout has expired");
  const expiredKey = lastCheckout().body.idempotency_key;
  await clickText("Start new checkout");
  await text("Temporary checkout gateway unavailable");
  const freshKey = lastCheckout().body.idempotency_key;
  assert.notEqual(freshKey, expiredKey);
  await clickText("Retry checkout");
  await text("Payment confirmed.");
  assert.equal(lastCheckout().body.idempotency_key, freshKey);
  pass(
    "an expired checkout creates an explicit fresh intent and preserves its key after a service failure",
  );
  checkoutConflictCode = "pending_subscription_checkout";
  cancelConflictCode = "checkout_payment_pending";
  orderState = "created";
  await openPrice();
  await clickText("Subscribe");
  await text("A membership checkout is already open");
  const beforePending = checkouts().length;
  await clickText("Cancel pending checkout");
  await text("Payment is being confirmed");
  assert.equal(await page.$(".checkout-recovery .button.primary"), null);
  assert(
    await page.$$eval(".membership-card button", (buttons) =>
      buttons.every((button) => button.disabled),
    ),
  );
  await clickText("Check payment status");
  await text("Payment is still being confirmed.");
  assert.equal(checkouts().length, beforePending);
  orderState = "paid";
  await clickText("Check payment status");
  await text("Payment confirmed. This order has been paid.");
  assert.equal(checkouts().length, beforePending);
  pass(
    "a completed checkout cannot be cancelled into a second payment and waits for confirmed order status",
  );
  cancelConflictCode = null;
  for (const code of ["checkout_recovery_required", "checkout_already_paid"]) {
    checkoutConflictCode = code;
    await openPrice();
    await clickText("Subscribe");
    await text(conflictMessages[code]);
    assert.equal(await page.$(".checkout-recovery button"), null);
    assert(
      (
        await page.$eval(".checkout-recovery .text-button", (link) =>
          link.getAttribute("href"),
        )
      ).includes("order=ORDER-FIXTURE"),
    );
    assert(
      (
        await page.$eval(".checkout-recovery .button.secondary", (link) =>
          link.getAttribute("href"),
        )
      ).includes("account/orders"),
    );
  }
  checkoutConflictCode = null;
  pass(
    "manual recovery and already-paid conflicts expose order and support links without another purchase",
  );
  await go("#support?ticket=T-1");
  await text("An episode needs help.");
  await page.evaluate(() => {
    location.hash = "support?ticket=T-NOT-FOUND";
  });
  await text("Ticket not found");
  assert.equal(await page.$(".messages .message"), null);
  assert(
    !(await page.$eval(".support-main", (el) => el.innerText)).includes(
      "An episode needs help.",
    ),
  );
  pass("a support detail 404 clears the previously opened conversation");
  await page.evaluate(() => {
    location.hash = "support?ticket=T-1";
  });
  await text("An episode needs help.");
  await clickText("New ticket");
  await page.type('input[maxlength="200"]', "PRIVATE DRAFT FROM ACCOUNT A");
  await page.type("textarea", "PRIVATE BODY FROM ACCOUNT A");
  holdOldSupport = true;
  const oldStarted = new Promise((resolve) => {
    oldSupportStarted = resolve;
  });
  await page.evaluate(() => {
    location.hash = "support?ticket=T-MAIN";
  });
  await oldStarted;
  expireSupportA = true;
  await page.evaluate(() => {
    location.hash = "support?ticket=T-1";
  });
  await text("Sign in to create a ticket and follow your conversations.");
  holdSecondSupport = true;
  const secondStarted = new Promise((resolve) => {
    secondSupportStarted = resolve;
  });
  await page.click(".sign-in");
  await page.waitForSelector("input[type=email]");
  await page.type("input[type=email]", "second@example.com");
  await clickText("I already have a code");
  await page.type("input[autocomplete=one-time-code]", "123456");
  await clickText("Sign in / Create account");
  await page.waitForFunction(
    () => document.querySelector(".profile-button")?.textContent === "S",
  );
  await secondStarted;
  const assertPrivateSupportAbsent = async () => {
    const state = await page.evaluate(() => ({
      text: document.querySelector(".support-page")?.textContent || "",
      draft: document.querySelector('input[maxlength="200"]')?.value || "",
    }));
    for (const privateText of [
      "An episode needs help.",
      "Original combined message",
      "Earlier conversation",
      "PRIVATE DRAFT FROM ACCOUNT A",
      "PRIVATE BODY FROM ACCOUNT A",
    ])
      assert(
        !state.text.includes(privateText) && !state.draft.includes(privateText),
        "Previous account data remained: " + privateText,
      );
  };
  await assertPrivateSupportAbsent();
  const oldResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/v1/support/tickets/T-MAIN",
  );
  releaseOldSupport();
  await oldResponse;
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  await assertPrivateSupportAbsent();
  assert(page.url().includes("ticket=T-1"));
  releaseSecondSupport();
  await text("Ticket not found");
  await assertPrivateSupportAbsent();
  assert.equal(await page.$$(".ticket-link").then((items) => items.length), 0);
  assert.equal(await page.$(".messages .message"), null);
  await clickText("New ticket");
  assert.equal(
    await page.$eval('input[maxlength="200"]', (input) => input.value),
    "",
  );
  assert.equal(await page.$eval("textarea", (input) => input.value), "");
  pass(
    "support account switching discards private caches, drafts and late responses despite 404 and list failures",
  );
  privateComments = true;
  await go("#watch/1?episode=10");
  await page.waitForFunction(
    () => document.querySelector("video")?.currentTime > 0.1,
  );
  await page.$eval("video", (video) => video.pause());
  await page.click("aria/Comment");
  await text("PRIVATE PENDING COMMENT FROM FIRST ACCOUNT");
  await page.type("#comment-body", "PRIVATE COMMENT DRAFT FROM FIRST ACCOUNT");
  holdOldComments = true;
  const commentStarted = new Promise((resolve) => {
    oldCommentsStarted = resolve;
  });
  await clickText("More comments");
  await commentStarted;
  expireCommentOwner = true;
  await clickText("Post comment");
  await page.waitForSelector(".sign-in");
  const assertPrivateCommentsAbsent = async () => {
    const state = await page.evaluate(() => ({
      text: document.body.textContent,
      draft: document.querySelector("#comment-body")?.value || "",
    }));
    for (const privateText of [
      "PRIVATE PENDING COMMENT FROM FIRST ACCOUNT",
      "PRIVATE LATE PENDING COMMENT FROM FIRST ACCOUNT",
      "PRIVATE COMMENT DRAFT FROM FIRST ACCOUNT",
    ])
      assert(
        !state.text.includes(privateText) && !state.draft.includes(privateText),
        "Previous account comment remained: " + privateText,
      );
  };
  await assertPrivateCommentsAbsent();
  await page.waitForFunction(
    () => document.querySelector("video")?.currentTime > 0.1,
  );
  await page.$eval("video", (video) => video.pause());
  if (!(await page.$(".watch-comments-modal")))
    await page.click("aria/Comment");
  await page.waitForSelector("#comment-body");
  const guestComment = "Guest comment replay after account change.";
  await page.type("#comment-body", guestComment);
  await clickText("Post comment");
  await page.waitForSelector("input[type=email]");
  await page.type("input[type=email]", "viewer@example.com");
  await clickText("I already have a code");
  await page.type("input[autocomplete=one-time-code]", "123456");
  const guestCommentRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/v1/episodes/10/comments" &&
      request.method() === "POST" &&
      JSON.parse(request.postData() || "{}").body === guestComment,
  );
  await clickText("Sign in / Create account");
  await guestCommentRequest;
  await text("Your comment was submitted for review.");
  assert.equal(
    requests.filter(
      (request) =>
        request.path === "/v1/episodes/10/comments" &&
        request.method === "POST" &&
        request.body.body === guestComment,
    ).length,
    1,
  );
  pass(
    "guest comment intent survives the player login transition and submits exactly once",
  );
  await text("Comments are unavailable");
  const oldCommentsResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/v1/episodes/10/comments" &&
      new URL(response.url()).searchParams.get("cursor") === "9001",
  );
  releaseOldComments();
  await oldCommentsResponse;
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  await assertPrivateCommentsAbsent();
  assert.equal(
    await page
      .$$(".watch-comments-modal .comment")
      .then((items) => items.length),
    0,
  );
  pass(
    "pending comments and drafts stay private across account changes, a new-owner 404 and late old replies",
  );
  for (const alreadyLiked of [false, true]) {
    await page.keyboard.press("Escape");
    await go("#account/overview");
    await page.waitForSelector(".member-signout");
    await clickText("Sign out");
    restoredLikeScenario = true;
    restoredFeedSnapshotLiked = alreadyLiked;
    feedServerLiked = alreadyLiked;
    releaseRestoredFeed = undefined;
    const feedHeld = new Promise((resolve) => {
      restoredFeedStarted = resolve;
    });
    await go("#foryou");
    await page.waitForFunction(
      () => document.querySelector(".for-you-page video")?.currentTime > 0.1,
    );
    await page.$eval(".for-you-page video", (video) => video.pause());
    const reactionsBefore = requests.filter(
      (request) =>
        request.path === "/v1/reactions" &&
        request.body.kind === "like" &&
        request.body.target_id === 1,
    ).length;
    await page.click(
      '.feed-item[data-index="0"] button[aria-label="Like drama"]',
    );
    await page.waitForSelector("input[type=email]");
    await page.type("input[type=email]", "viewer@example.com");
    await clickText("I already have a code");
    await page.type("input[autocomplete=one-time-code]", "123456");
    const reactionResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/v1/reactions" &&
        response.request().method() === "POST",
    );
    await clickText("Sign in / Create account");
    await feedHeld;
    await reactionResponse;
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)),
        ),
    );
    assert.equal(await page.$$(".feed-item").then((items) => items.length), 0);
    assert.equal(feedServerLiked, true);
    releaseRestoredFeed();
    await page.waitForSelector(
      '.feed-item[data-index="0"] button[aria-label="Unlike drama"]',
    );
    assert.equal(
      await page.$eval(
        '.feed-item[data-index="0"] button[aria-label="Unlike drama"] small',
        (counter) => counter.textContent,
      ),
      "2",
    );
    assert.equal(
      requests.filter(
        (request) =>
          request.path === "/v1/reactions" &&
          request.body.kind === "like" &&
          request.body.target_id === 1,
      ).length,
      reactionsBefore + 1,
    );
    restoredLikeScenario = false;
  }
  pass(
    "restored feed likes survive delayed unliked or already-liked snapshots and increment the counter once",
  );
  feedCursorScenario = true;
  expireFeedOwner = false;
  feedGuestFirstFailed = false;
  await page.reload({ waitUntil: "networkidle0" });
  await page.waitForSelector(".profile-button");
  await page.waitForFunction(
    () => document.querySelectorAll(".feed-item").length === 6,
  );
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  expireFeedOwner = true;
  await page.click(
    '.feed-item[data-index="0"] button[aria-label="Like drama"]',
  );
  await page.waitForSelector(".sign-in");
  await text("Feed first page temporarily unavailable");
  const feedRetry = page.waitForRequest(
    (request) => new URL(request.url()).pathname === "/v1/feed",
  );
  await clickText("Try again");
  const retryRequest = await feedRetry;
  assert.equal(new URL(retryRequest.url()).searchParams.get("cursor"), null);
  assert.equal(retryRequest.headers().authorization, undefined);
  await page.waitForSelector(".feed-item");
  assert.deepEqual(invalidFeedCursors, []);
  pass(
    "a new identity retries its failed first feed page without the previous viewer's pagination cursor",
  );
  feedCursorScenario = false;
  expireFeedOwner = false;
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
  releaseOldSupport?.();
  releaseSecondSupport?.();
  releaseOldComments?.();
  releaseRestoredFeed?.();
  await browser.close();
  await server.close();
}
