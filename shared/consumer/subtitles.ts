export interface SubtitleTrack {
  language: string;
  label: string;
  url: string;
  is_default: boolean;
  format?: "srt" | "vtt";
}
/** Convert timestamps, retaining cue identifiers and caption markup accepted by WebVTT. */
export function srtToWebVtt(source: string): string {
  const text = source
    .replace(/^\uFEFF/, "")
    .replace(/\r\n?/g, "\n")
    .trim();
  if (/^WEBVTT(?:[ \t]|\n|$)/.test(text)) return text + "\n";
  const cues = text.replace(
    /(\d{2}:\d{2}:\d{2}),(\d{3})(\s*-->\s*)(\d{2}:\d{2}:\d{2}),(\d{3})/g,
    (_, a, b, arrow, c, d) => a + "." + b + arrow + c + "." + d,
  );
  return "WEBVTT\n\n" + cues + "\n";
}
export async function prepareSubtitleTracks(
  tracks: SubtitleTrack[],
  signal?: AbortSignal,
): Promise<{ tracks: SubtitleTrack[]; dispose(): void }> {
  const urls: string[] = [];
  try {
    const results = await Promise.allSettled(
      tracks.map(async (track) => {
        const isSrt =
          track.format === "srt" || /\.srt(?:$|[?#])/i.test(track.url);
        if (!isSrt) return track;
        const response = await fetch(track.url, { signal });
        if (!response.ok)
          throw new Error("Could not load subtitles (" + response.status + ")");
        const url = URL.createObjectURL(
          new Blob([srtToWebVtt(await response.text())], { type: "text/vtt" }),
        );
        urls.push(url);
        return { ...track, url, format: "vtt" as const };
      }),
    );
    const failed = results.find((result) => result.status === "rejected");
    if (failed?.status === "rejected") throw failed.reason;
    const prepared = results.flatMap((result) =>
      result.status === "fulfilled" ? [result.value] : [],
    );
    return {
      tracks: prepared,
      dispose: () => urls.forEach((url) => URL.revokeObjectURL(url)),
    };
  } catch (error) {
    urls.forEach((url) => URL.revokeObjectURL(url));
    throw error;
  }
}
