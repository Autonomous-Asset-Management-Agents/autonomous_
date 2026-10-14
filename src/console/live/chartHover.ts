// src/console/live/chartHover.ts — PR-A crosshair helper (pure, testable).

/**
 * Nearest data index for a horizontal pointer fraction (0..1) over an
 * EVENLY-SPACED series of n points (EquityChart spreads points uniformly across
 * the x-axis). Clamps out-of-range fractions into the series. Returns null when
 * there is nothing to hover (fewer than 2 points, or a non-finite fraction) —
 * the caller then shows no crosshair/tooltip.
 */
export function nearestIndex(fraction: number, n: number): number | null {
  if (!Number.isFinite(fraction) || n < 2) return null;
  const clamped = Math.min(1, Math.max(0, fraction));
  return Math.round(clamped * (n - 1));
}
