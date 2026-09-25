/**
 * "Time ago" label for the Overview "Last sync" line. Pure — takes `now`
 * explicitly so it is testable without faking the clock. `null` (no successful
 * poll yet) renders an honest "—" rather than a fabricated time.
 */
export function ago(since: number | null, now: number): string {
  if (since == null) return "—";
  const s = Math.max(0, Math.floor((now - since) / 1000));
  if (s < 3) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return `${h}h ago`;
}
