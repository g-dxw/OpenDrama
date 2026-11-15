import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import * as state from "./playback-state.ts";
const {
  clampPosition,
  playbackTime,
  playbackRate,
  qualityOptions,
  qualityLevel,
} = state;
const require = createRequire(import.meta.url);
const ts = require("typescript");

test("seek positions handle non-finite input, negative positions and changed durations", () => {
  assert.equal(clampPosition(NaN, 90), 0);
  assert.equal(clampPosition(Infinity, 90), 0);
  assert.equal(clampPosition(-12, 90), 0);
  assert.equal(clampPosition(120, 90), 90);
  assert.equal(clampPosition(45.5, 90), 45.5);
  assert.equal(clampPosition(45.5, Infinity), 45.5);
  assert.equal(clampPosition(45.5, 0), 45.5);
});
test("clock and playback-rate formatting stay valid for media controls", () => {
  assert.equal(playbackTime(0), "0:00");
  assert.equal(playbackTime(65.9), "1:05");
  assert.equal(playbackTime(3605), "1:00:05");
  assert.equal(playbackTime(NaN), "0:00");
  assert.equal(playbackRate(NaN), 1);
  assert.equal(playbackRate(-1), 1);
  assert.equal(playbackRate(1.5), 1.5);
});
const levels = [
  { width: 360, height: 640 },
  { width: 480, height: 854 },
  { width: 720, height: 1280 },
  { width: 1280, height: 720 },
];
test("quality reports actual portrait/landscape levels and selects the nearest short side", () => {
  assert.deepEqual(qualityOptions(levels), ["Auto", "720P", "480P", "360P"]);
  assert.equal(qualityLevel("720P", levels), 2);
  assert.equal(qualityLevel("1080P", levels), 2);
  assert.equal(qualityLevel("Auto", levels), -1);
  assert.equal(qualityLevel("Original", levels), -1);
  assert.equal(qualityLevel("720P", []), -1);
  assert.deepEqual(qualityOptions([{ width: 0, height: 0 }]), ["Auto"]);
});

/** Run the real adapter against an event-driven HLS stub, without a browser or network. */
function adapter(supported = true) {
  const timers = new Set();
  class FakeHls {
    static Events = { MANIFEST_PARSED: "manifest", ERROR: "error" };
    static ErrorTypes = { NETWORK_ERROR: "network", MEDIA_ERROR: "media" };
    static instances = [];
    static isSupported() {
      return supported;
    }
    levels = [];
    currentLevel = -1;
    listeners = new Map();
    starts = 0;
    recoveries = 0;
    destroyed = false;
    constructor() {
      FakeHls.instances.push(this);
    }
    on(name, fn) {
      this.listeners.set(name, fn);
    }
    fire(name, value) {
      this.listeners.get(name)?.(name, value);
    }
    loadSource() {}
    attachMedia() {}
    startLoad() {
      this.starts++;
    }
    stopLoad() {}
    recoverMediaError() {
      this.recoveries++;
    }
    destroy() {
      this.destroyed = true;
    }
  }
  const compiled = ts.transpileModule(
    readFileSync(new URL("./hls.ts", import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020,
      },
    },
  ).outputText;
  const module = { exports: {} };
  vm.runInNewContext(compiled, {
    module,
    exports: module.exports,
    require(name) {
      if (name === "hls.js") return { default: FakeHls };
      if (name === "./playback-state") return state;
      throw Error(name);
    },
    setTimeout(fn) {
      const token = { fn };
      timers.add(token);
      return token;
    },
    clearTimeout(token) {
      timers.delete(token);
    },
  });
  const video = {
    src: "",
    pauses: 0,
    loads: 0,
    pause() {
      this.pauses++;
    },
    removeAttribute() {
      this.src = "";
    },
    load() {
      this.loads++;
    },
  };
  return {
    attach: module.exports.attachHls,
    FakeHls,
    video,
    tick() {
      const batch = [...timers];
      timers.clear();
      batch.forEach((timer) => timer.fn());
    },
  };
}
test("quality chosen before a manifest is applied when the real levels arrive", () => {
  const a = adapter();
  const reports = [];
  const handle = a.attach(
    a.video,
    "https://cdn.test/master.m3u8?token=x",
    (value) => reports.push(Array.from(value)),
  );
  handle.setQuality("720P");
  const hls = a.FakeHls.instances[0];
  hls.levels = levels;
  hls.fire("manifest", { levels });
  assert.equal(hls.currentLevel, 2);
  assert.deepEqual(reports.at(-1), ["Auto", "720P", "480P", "360P"]);
  handle.setQuality("Auto");
  assert.equal(hls.currentLevel, -1);
});
test("MP4 source reports Original only and does not pretend to switch HLS levels", () => {
  const a = adapter();
  const reports = [];
  const handle = a.attach(a.video, "https://cdn.test/episode.mp4", (value) =>
    reports.push(Array.from(value)),
  );
  handle.setQuality("1080P");
  assert.deepEqual(reports, [["Original"]]);
  assert.equal(a.FakeHls.instances.length, 0);
  assert.equal(a.video.src, "https://cdn.test/episode.mp4");
  handle.destroy();
  assert.equal(a.video.src, "");
});
test("fatal network recovery is bounded and then exposes explicit retry", () => {
  const a = adapter();
  let failures = 0;
  a.attach(a.video, "/master.m3u8", undefined, () => failures++);
  const hls = a.FakeHls.instances[0];
  for (let i = 0; i < 3; i++) {
    hls.fire("error", { fatal: true, type: "network" });
    a.tick();
  }
  assert.equal(hls.starts, 2);
  assert.equal(failures, 1);
});
test("fatal media recovery is bounded and destroying the source cancels pending recovery", () => {
  const a = adapter();
  let failures = 0;
  const handle = a.attach(a.video, "/master.m3u8", undefined, () => failures++);
  const hls = a.FakeHls.instances[0];
  hls.fire("error", { fatal: true, type: "media" });
  hls.fire("error", { fatal: true, type: "media" });
  assert.equal(hls.recoveries, 1);
  assert.equal(failures, 1);
  handle.destroy();
  hls.fire("error", { fatal: true, type: "network" });
  a.tick();
  assert.equal(hls.starts, 0);
  assert.equal(hls.destroyed, true);
});

test("a sheet resume intent cannot cross identity, episode or source attempts", () => {
  const bookmark = { identity: "site-user:episode1", attempt: 3 };
  assert.equal(state.matchesResume(bookmark, "site-user:episode1", 3), true);
  assert.equal(state.matchesResume(bookmark, "other-user:episode1", 3), false);
  assert.equal(state.matchesResume(bookmark, "site-user:episode2", 3), false);
  assert.equal(state.matchesResume(bookmark, "site-user:episode1", 4), false);
  assert.equal(state.matchesResume(null, "site-user:episode1", 3), false);
});

test("native HLS reports browser-managed Auto without invented manual levels", () => {
  const a = adapter(false);
  const reports = [];
  const handle = a.attach(
    a.video,
    "https://cdn.test/master.m3u8?token=native",
    (value) => reports.push(Array.from(value)),
  );
  handle.setQuality("720P");
  assert.deepEqual(reports, [["Auto"]]);
  assert.equal(a.FakeHls.instances.length, 0);
  assert.equal(handle.handlesMediaErrors, false);
  assert.equal(a.video.src, "https://cdn.test/master.m3u8?token=native");
  handle.destroy();
  assert.equal(a.video.src, "");
});
