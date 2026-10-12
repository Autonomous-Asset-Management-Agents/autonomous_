/**
 * Pure helper for the sidebar "Live Trading" indicator. Kept out of the Sidebar
 * component file so the component module only exports components (react-refresh).
 *
 * `strategyRunning` is the engine's /health `strategy_running` flag:
 *   null  → "—"    (no poll yet / no local engine)
 *   false → "Idle" (engine up, trading loop not running)
 *   true  → "Paper" (OSS desktop is paper-only) or "Live" when `isLive`
 *           (wired by the live edition; renders red).
 *
 * `startupBlockedReason` is /health's `startup_blocked_reason` (B11): while the engine's
 * trading loop WAITS for the local LLM provider ("llm_unreachable") the idle label carries
 * an actionable `hint` pointing at Settings → System, where the local model is installed.
 */
export const LLM_MISSING_HINT = "AI model missing: Settings → System";

export function tradingLabel(
  strategyRunning: boolean | null,
  isLive: boolean,
  marketOpen: boolean | null = null,
  componentsDegraded: boolean | null = null,
  startupBlockedReason: string | null = null,
): { text: string; live: boolean; hint?: string } {
  if (strategyRunning == null) return { text: "—", live: false };
  // B11: while the engine WAITS for the local LLM provider, start_live_strategy has already set
  // strategy_running (it is set before the loop thread starts), so the flag is optimistic: nothing
  // trades yet. The blocked reason wins over the flag, and the idle label carries the hint.
  if (startupBlockedReason === "llm_unreachable") {
    return { text: "Idle", live: false, hint: LLM_MISSING_HINT };
  }
  // #2743/S3 (A4): the trading loop flag alone is optimistic — the engine reports strategy_running
  // even when a broker/model component is faulted and it cannot actually trade. When /health/deep
  // says a component is degraded, say so honestly instead of a confident "Live"/"Paper".
  if (strategyRunning && componentsDegraded) return { text: "Degraded", live: false };
  if (strategyRunning) return isLive ? { text: "Live", live: true } : { text: "Paper", live: false };
  // Loop not running. B11: when the engine says it is waiting for the LLM, the idle state is
  // something the operator can fix, so say where. Only that reason maps to a hint; others stay bare.
  const hint = {};
  // #2465: on the LIVE account with the market closed, say "Market closed" instead
  // of a bare "Idle" that reads like a fault — the operator is live, there is just nothing to trade
  // until the open. (Keeps the red live dot.) A market-open "Idle" still surfaces as a real state.
  if (isLive && marketOpen === false) return { text: "Market closed", live: true, ...hint };
  return { text: "Idle", live: false, ...hint };
}
