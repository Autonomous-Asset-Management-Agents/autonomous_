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
  marketOpen: boolean | null = null,
  componentsDegraded: boolean | null = null,
): { text: string; live: boolean } {
  if (strategyRunning == null) return { text: "—", live: false };
  // #2743/S3 (A4): the trading loop flag alone is optimistic — the engine reports strategy_running
  // even when a broker/model component is faulted and it cannot actually trade. When /health/deep
  // says a component is degraded, say so honestly instead of a confident "Live"/"Paper".
  if (strategyRunning && componentsDegraded) return { text: "Degraded", live: false };
  if (strategyRunning) return isLive ? { text: "Live", live: true } : { text: "Paper", live: false };
  // Loop not running. #2465: on the LIVE account with the market closed, say "Market closed" instead
  // of a bare "Idle" that reads like a fault — the operator is live, there is just nothing to trade
  // until the open. (Keeps the red live dot.) A market-open "Idle" still surfaces as a real state.
  if (isLive && marketOpen === false) return { text: "Market closed", live: true };
  return { text: "Idle", live: false };
}
