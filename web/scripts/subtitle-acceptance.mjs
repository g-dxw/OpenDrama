import assert from "node:assert/strict";
import {
  srtToWebVtt,
  prepareSubtitleTracks,
} from "../../shared/consumer/subtitles.ts";
const source =
  "\uFEFF1\r\n00:00:01,125 --> 00:00:03,500\r\n<i>Hello</i>\r\n\r\n2\r\n01:02:03,004 --> 01:02:04,005\r\nSecond cue\r\n";
const vtt = srtToWebVtt(source);
assert(vtt.startsWith("WEBVTT\n\n1\n"));
assert(vtt.includes("00:00:01.125 --> 00:00:03.500"));
assert(vtt.includes("01:02:03.004 --> 01:02:04.005"));
assert(vtt.includes("<i>Hello</i>"));
assert(!vtt.includes("\r"));
assert.equal(
  srtToWebVtt("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHi"),
  "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHi\n",
);
const oldFetch = globalThis.fetch;
globalThis.fetch = async () => new Response(source, { status: 200 });
try {
  const result = await prepareSubtitleTracks([
    {
      language: "en",
      label: "English",
      url: "https://fixture.invalid/caption.srt?token=signed",
      is_default: true,
    },
  ]);
  assert(result.tracks[0].url.startsWith("blob:"));
  assert(
    (await (await oldFetch(result.tracks[0].url)).text()).includes(
      "00:00:01.125",
    ),
  );
  const blob = result.tracks[0].url;
  result.dispose();
  await assert.rejects(() => oldFetch(blob));
  const vttTrack = {
    language: "en",
    label: "English",
    url: "https://fixture.invalid/caption.vtt",
    is_default: true,
  };
  const untouched = await prepareSubtitleTracks([vttTrack]);
  assert.equal(untouched.tracks[0].url, vttTrack.url);
  untouched.dispose();
  console.log(
    "PASS SRT timestamp/BOM/markup conversion, signed URL detection, VTT passthrough and Blob cleanup",
  );
} finally {
  globalThis.fetch = oldFetch;
}
