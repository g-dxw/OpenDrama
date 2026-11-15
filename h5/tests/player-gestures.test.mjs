import test from "node:test";
import assert from "node:assert/strict";
import { classifyEpisodeSwipe } from "../src/player/gestures.ts";

const swipe = (changes = {}) => ({ dx: 3, dy: -100, startX: 187, viewportWidth: 375, ...changes });

test("a vertical swipe moves to the next or previous episode", () => {
  assert.equal(classifyEpisodeSwipe(swipe({ dy: -90 })), 1);
  assert.equal(classifyEpisodeSwipe(swipe({ dy: 90 })), -1);
});

test("horizontal and ambiguous diagonal movement keeps the current episode", () => {
  assert.equal(classifyEpisodeSwipe(swipe({ dx: 140, dy: -70 })), 0);
  assert.equal(classifyEpisodeSwipe(swipe({ dx: -100, dy: 100 })), 0);
  assert.equal(classifyEpisodeSwipe(swipe({ dx: 0, dy: 0 })), 0);
  assert.equal(classifyEpisodeSwipe(swipe({ dx: 60, dy: -80 })), 1);
});

test("an episode change requires at least 60 pixels of vertical travel", () => {
  assert.equal(classifyEpisodeSwipe(swipe({ dx: 0, dy: -59.99 })), 0);
  assert.equal(classifyEpisodeSwipe(swipe({ dx: 0, dy: 59.99 })), 0);
  assert.equal(classifyEpisodeSwipe(swipe({ dx: 0, dy: -60 })), 1);
  assert.equal(classifyEpisodeSwipe(swipe({ dx: 0, dy: 60 })), -1);
});

test("the vertical dominance boundary is inclusive in both directions", () => {
  assert.equal(classifyEpisodeSwipe(swipe({ dx: 48, dy: -60 })), 1);
  assert.equal(classifyEpisodeSwipe(swipe({ dx: -48, dy: 60 })), -1);
  assert.equal(classifyEpisodeSwipe(swipe({ dx: 48.01, dy: -60 })), 0);
  assert.equal(classifyEpisodeSwipe(swipe({ dx: -48.01, dy: 60 })), 0);
});

test("swipes starting in either 20 pixel navigation edge belong to the browser", () => {
  for (const startX of [0, 10, 20, 355, 365, 375]) {
    assert.equal(classifyEpisodeSwipe(swipe({ startX })), 0, String(startX));
  }
  assert.equal(classifyEpisodeSwipe(swipe({ startX: 20.01 })), 1);
  assert.equal(classifyEpisodeSwipe(swipe({ startX: 354.99, dy: 100 })), -1);
});

test("small and invalid viewports cannot produce episode navigation", () => {
  for (const viewportWidth of [-10, 0, 20, 40, NaN, Infinity, -Infinity]) {
    assert.equal(classifyEpisodeSwipe(swipe({ viewportWidth, startX: viewportWidth / 2 })), 0);
  }
  assert.equal(classifyEpisodeSwipe(swipe({ viewportWidth: 41, startX: 20.5 })), 1);
  assert.equal(classifyEpisodeSwipe(swipe({ viewportWidth: 1024, startX: 512 })), 1);
});

test("invalid coordinate telemetry and out of bounds starts are ignored", () => {
  for (const field of ["dx", "dy", "startX", "viewportWidth"]) {
    for (const value of [NaN, Infinity, -Infinity, undefined, null, "100"]) {
      assert.equal(classifyEpisodeSwipe(swipe({ [field]: value })), 0, field + ": " + value);
    }
  }
  assert.equal(classifyEpisodeSwipe(swipe({ startX: -1 })), 0);
  assert.equal(classifyEpisodeSwipe(swipe({ startX: 376 })), 0);
  for (const input of [null, undefined, [], "swipe", 1, {}]) {
    assert.equal(classifyEpisodeSwipe(input), 0);
  }
});

test("classification preserves a frozen gesture snapshot", () => {
  const input = Object.freeze(swipe());
  assert.equal(classifyEpisodeSwipe(input), 1);
  assert.deepEqual(input, { dx: 3, dy: -100, startX: 187, viewportWidth: 375 });
});
