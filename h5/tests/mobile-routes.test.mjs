import assert from "node:assert/strict";
import test from "node:test";
import { readMobileRoute } from "../src/routes.ts";

test("My List has its own navigation identity and defaults to saved dramas", () => {
  const route = readMobileRoute("#mylist");
  assert.equal(route.supported, true);
  assert.equal(route.tab, "mylist");
  assert.equal(route.meTab, "favorites");
  assert.equal(route.meSheet, null);
});

test("My List deep links retain the requested collection", () => {
  for (const collection of ["history", "favorites", "likes"]) {
    const route = readMobileRoute("#mylist/" + collection);
    assert.equal(route.supported, true);
    assert.equal(route.tab, "mylist");
    assert.equal(route.meTab, collection);
    assert.equal(route.meSheet, null);
  }
});

test("My List cannot open account settings or sign-out sheets", () => {
  for (const path of ["#mylist/settings?sheet=signout", "#mylist/orders"]) {
    const route = readMobileRoute(path);
    assert.equal(route.supported, false);
    assert.equal(route.meSheet, null);
  }
});

test("existing account and checkout links retain Profile navigation", () => {
  const settings = readMobileRoute("#account/settings?library=favorites&sheet=signout");
  assert.equal(settings.supported, true);
  assert.equal(settings.tab, "me");
  assert.equal(settings.meTab, "favorites");
  assert.equal(settings.meSheet, "signout");
  const premium = readMobileRoute("#premium");
  assert.equal(premium.tab, "me");
  assert.equal(premium.meSheet, "premium");
  assert.equal(readMobileRoute("#price?tab=coins").supported, false);
});

test("continue-watching links preserve the exact drama and episode", () => {
  const route = readMobileRoute("#watch/14?episode=590");
  assert.equal(route.supported, true);
  assert.equal(route.dramaId, 14);
  assert.equal(route.episodeId, 590);
  assert.equal(route.searching, false);
});
