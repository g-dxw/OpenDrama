export type EpisodeSwipe = {
  dx: number;
  dy: number;
  startX: number;
  viewportWidth: number;
};

/** Classify a completed swipe without consuming browser navigation edges. */
export function classifyEpisodeSwipe(input: EpisodeSwipe): -1 | 0 | 1 {
  if (!input || typeof input !== "object" || Array.isArray(input)) return 0;
  const { dx, dy, startX, viewportWidth } = input;
  if (![dx, dy, startX, viewportWidth].every(Number.isFinite)) return 0;

  const edgeWidth = 20;
  if (viewportWidth <= edgeWidth * 2 || startX < 0 || startX > viewportWidth) return 0;
  if (startX <= edgeWidth || startX >= viewportWidth - edgeWidth) return 0;
  if (Math.abs(dy) < 60 || Math.abs(dy) < 1.25 * Math.abs(dx)) return 0;
  return dy < 0 ? 1 : -1;
}
