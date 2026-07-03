/**
 * Pure helper for the sidebar "Live Trading" indicator. Kept out of the Sidebar
 * component file so the component module only exports components (react-refresh).
 *
 * `strategyRunning` is the engine's /health `strategy_running` flag:
 *   null  → "—"    (no poll yet / no local engine)
 *   false → "Idle" (engine up, trading loop not running)
 *   true  → "Paper" (OSS desktop is paper-only) or "Live" when `isLive`
 *           (wired by the live edition; renders red).
 */
export function tradingLabel(
  strategyRunning: boolean | null,
  isLive: boolean,
): { text: string; live: boolean } {
  if (strategyRunning == null) return { text: "—", live: false };
  if (!strategyRunning) return { text: "Idle", live: false };
  return isLive ? { text: "Live", live: true } : { text: "Paper", live: false };
}
