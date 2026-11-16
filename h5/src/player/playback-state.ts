export type VideoLevel = { width?: number; height?: number };

/** Unknown/live durations must never create NaN range values or negative seeks. */
export function clampPosition(position: number, duration?: number): number {
  const safe = Number.isFinite(position) ? Math.max(0, position) : 0;
  return duration != null && Number.isFinite(duration) && duration > 0
    ? Math.min(safe, duration)
    : safe;
}

export function playbackRate(rate: number): number {
  return Number.isFinite(rate) && rate >= 0.25 && rate <= 4 ? rate : 1;
}

export function playbackTime(seconds: number): string {
  const value = Math.floor(clampPosition(seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const rest = String(value % 60).padStart(2, "0");
  return hours
    ? hours + ":" + String(minutes).padStart(2, "0") + ":" + rest
    : minutes + ":" + rest;
}

function shortSide(level: VideoLevel): number {
  const width = level.width || 0,
    height = level.height || 0;
  return width > 0 && height > 0
    ? Math.min(width, height)
    : Math.max(width, height);
}

export function qualityOptions(levels: VideoLevel[]): string[] {
  const sizes = levels
    .map(shortSide)
    .filter((value) => value > 0)
    .map((value) => {
      if (value >= 1000) return 1080;
      if (value >= 700) return 720;
      if (value >= 450) return 480;
      if (value >= 340) return 360;
      return value;
    });
  return [
    "Auto",
    ...Array.from(new Set(sizes))
      .sort((a, b) => b - a)
      .map((value) => value + "P"),
  ];
}

/** -1 means ABR; an unready manifest can safely retain the request for later. */
export function qualityLevel(quality: string, levels: VideoLevel[]): number {
  const target = Number.parseInt(quality.replace(/\D/g, ""), 10);
  if (
    !levels.length ||
    !Number.isFinite(target) ||
    target <= 0 ||
    /^(auto|original)$/i.test(quality)
  )
    return -1;
  let best = -1,
    difference = Infinity;
  levels.forEach((level, index) => {
    const size = shortSide(level);
    if (size > 0 && Math.abs(size - target) < difference) {
      difference = Math.abs(size - target);
      best = index;
    }
  });
  return best;
}

export type ResumeBookmark = { identity: string; attempt: number };
export function matchesResume(
  bookmark: ResumeBookmark | null,
  identity: string,
  attempt: number,
): boolean {
  return (
    !!bookmark && bookmark.identity === identity && bookmark.attempt === attempt
  );
}
