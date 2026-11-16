/** Isolated async transport checks: actual shared TypeScript, no network or credentials. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import ts from "typescript";
const source = await fs.readFile(
  new URL("../../shared/consumer/client.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
  },
}).outputText;
const { createConsumerClient, ApiError } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
function harness(initial) {
  let token = initial,
    resets = 0;
  const calls = [];
  const client = createConsumerClient({
    apiBase: "https://fixture.invalid",
    tokenStore: {
      get: () => token,
      set: (value) => {
        token = value;
      },
    },
    deviceId: () => "fixture-device",
    fetch: (url, init) =>
      new Promise((resolve, reject) => {
        calls.push({
          url,
          headers: new Headers(init.headers),
          resolve,
          reject,
        });
      }),
  });
  client.onUnauthorized(() => {
    resets++;
    token = null;
  });
  return {
    client,
    calls,
    get token() {
      return token;
    },
    set token(value) {
      token = value;
    },
    get resets() {
      return resets;
    },
  };
}
const complete = (call, status, detail) =>
  call.resolve(
    new Response(JSON.stringify({ detail }), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
const rejectsStatus = (promise, status) =>
  assert.rejects(
    promise,
    (error) => error instanceof ApiError && error.status === status,
  );
const checks = [];
const pass = (name) => {
  checks.push(name);
  console.log("PASS " + name);
};
{
  const h = harness("account-a");
  const old = h.client.get("/v1/me");
  const oldRejected = rejectsStatus(old, 401);
  assert.equal(h.calls[0].headers.get("Authorization"), "Bearer account-a");
  h.token = "account-b";
  const current = h.client.get("/v1/me");
  h.calls[1].resolve(new Response(JSON.stringify({ id: 84 }), { status: 200 }));
  assert.deepEqual(await current, { id: 84 });
  complete(h.calls[0], 401, "Old account expired");
  await oldRejected;
  assert.equal(h.token, "account-b");
  assert.equal(h.resets, 0);
  pass(
    "a delayed account-A 401 cannot invalidate a successful account-B session",
  );
}
{
  const h = harness(null);
  const old = h.client.get("/v1/me");
  const rejected = rejectsStatus(old, 401);
  assert.equal(h.calls[0].headers.get("Authorization"), null);
  h.token = "account-b";
  complete(h.calls[0], 401, "Guest request rejected");
  await rejected;
  assert.equal(h.token, "account-b");
  assert.equal(h.resets, 0);
  pass("a request sent as guest cannot clear a later authenticated session");
}
{
  const h = harness("current-account");
  const request = h.client.get("/v1/me");
  const rejected = rejectsStatus(request, 401);
  complete(h.calls[0], 401, "Current session expired");
  await rejected;
  assert.equal(h.token, null);
  assert.equal(h.resets, 1);
  pass("a current authenticated 401 still clears the invalid session");
}
{
  const h = harness("current-account");
  const request = h.client.post("/v1/test", {}, { Authorization: "" });
  const rejected = rejectsStatus(request, 401);
  assert.equal(h.calls[0].headers.get("Authorization"), "");
  complete(h.calls[0], 401, "Missing authentication");
  await rejected;
  assert.equal(h.token, "current-account");
  assert.equal(h.resets, 0);
  pass(
    "a request with authentication explicitly omitted cannot invalidate stored credentials",
  );
}
{
  const h = harness("current-account");
  const request = h.client.post("/v1/auth/email/authenticate", {});
  const rejected = rejectsStatus(request, 401);
  complete(h.calls[0], 401, "Incorrect verification code");
  await rejected;
  assert.equal(h.token, "current-account");
  assert.equal(h.resets, 0);
  pass("an authentication-form rejection does not erase an existing session");
}
{
  const h = harness("current-account");
  const a = rejectsStatus(h.client.get("/v1/me"), 401),
    b = rejectsStatus(h.client.get("/v1/me/tasks"), 401);
  complete(h.calls[0], 401, "Session expired");
  complete(h.calls[1], 401, "Session expired");
  await Promise.all([a, b]);
  assert.equal(h.resets, 1);
  pass(
    "parallel current-session failures invalidate once after storage is cleared",
  );
}
{
  const h = harness("current-account");
  for (const status of [403, 503]) {
    const request = h.client.get("/v1/me");
    const rejected = rejectsStatus(request, status);
    complete(h.calls.at(-1), status, "Temporary or permission failure");
    await rejected;
  }
  const request = h.client.get("/v1/me");
  const rejected = rejectsStatus(request, 0);
  h.calls.at(-1).reject(new Error("network unavailable"));
  await rejected;
  assert.equal(h.token, "current-account");
  assert.equal(h.resets, 0);
  pass("permission, service and network failures preserve the session");
}
{
  const h = harness("current-account");
  const detail = {
    code: "pending_subscription_checkout",
    message: "Resume or cancel the pending checkout.",
    order_no: "ORDER-FIXTURE",
    action: "resume_or_cancel",
    checkout_url: "https://checkout.fixture.invalid/resume",
  };
  const request = h.client.post("/v1/commerce/checkout", {});
  const rejected = assert.rejects(request, (error) => {
    assert(error instanceof ApiError);
    assert.equal(error.status, 409);
    assert.equal(error.message, detail.message);
    assert.deepEqual(error.data, { detail });
    return true;
  });
  complete(h.calls[0], 409, detail);
  await rejected;
  assert.equal(h.resets, 0);
  pass(
    "structured checkout conflicts retain their message and recovery payload",
  );
}
{
  const routeSource = await fs.readFile(new URL("../src/store-route.ts", import.meta.url), "utf8");
  const routeCompiled = ts.transpileModule(routeSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  const { resolveStoreRoute } = await import("data:text/javascript;base64," + Buffer.from(routeCompiled).toString("base64"));
  const coinsOnly = { coins: true, membership: false }, membersOnly = { coins: false, membership: true };
  assert.deepEqual(resolveStoreRoute("price", null, coinsOnly), { kind: "coin_pack", available: true });
  assert.deepEqual(resolveStoreRoute("premium", null, coinsOnly), { kind: "membership", available: false });
  assert.deepEqual(resolveStoreRoute("price", null, membersOnly), { kind: "membership", available: true });
  assert.deepEqual(resolveStoreRoute("coins", null, membersOnly), { kind: "coin_pack", available: false });
  assert.deepEqual(resolveStoreRoute("price", "coins", membersOnly), { kind: "coin_pack", available: false });
  assert.equal(resolveStoreRoute("price", null, { coins: false, membership: false }).available, false);
  assert.deepEqual(resolveStoreRoute("price", null, { coins: true, membership: true }), { kind: "membership", available: true });
  assert.deepEqual(resolveStoreRoute("premium", null, { coins: false, membership: false }, true), { kind: "membership", available: true });
  assert.deepEqual(resolveStoreRoute("price", null, { coins: false, membership: false }, true), { kind: "membership", available: true });
  assert.deepEqual(resolveStoreRoute("coins", null, { coins: false, membership: false }, true), { kind: "coin_pack", available: false });
  pass("price entry resolves the same product for rendering and feature authorization across site configurations");
}
console.log(
  "PASS " +
    checks.length +
    " isolated client checks; no network/provider calls",
);
