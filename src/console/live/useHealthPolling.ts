import { useEffect } from "react";
import { fetchHealth } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import { nextServingState } from "./healthServing";

const POLL_MS = 10_000; // gap BETWEEN polls (not a fixed cadence)

/**
 * Polls /health and writes `strategy_running` (the engine's trading-loop active
 * flag) into the store, for the sidebar "Live Trading" indicator. Recursive
 * setTimeout loop (same robustness contract as usePortfolioPolling): the next
 * poll is scheduled only after the previous settles, so a slow engine never
 * stacks overlapping requests. A failed poll is swallowed and keeps the last
 * value (in the cloud/browser build there is no engine → stays null → "—").
 */
export function useHealthPolling(): void {
  const setStrategyRunning = useStore((s) => s.setStrategyRunning);
  const setSystemHalted = useStore((s) => s.setSystemHalted);
  const setPaperTrading = useStore((s) => s.setPaperTrading);
  const setEngineServing = useStore((s) => s.setEngineServing);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // Consecutive unreachable-poll count, for the debounced 'serving' signal (survives across loop
    // iterations, resets on remount).
    let failStreak = 0;
    const loop = async () => {
      if (!alive) return;
      try {
        const h = await fetchHealth();
        if (alive && h) {
          setStrategyRunning(h.strategy_running ?? false);
          setSystemHalted(h.system_halted ?? false);
          // #2190: capture live-vs-paper for the Overview order panel.
          setPaperTrading(h.paper_trading ?? null);
        }
        // LSR R4 (#2251): serving iff reachable AND not "starting" — the false→true edge (which
        // LiveTradingSwitchCard uses to recover the account tile) still fires on the first good poll.
        // DEBOUNCED (healthServing): the engine's event loop can stall for seconds under compute load,
        // so a single unreachable/slow poll must NOT flap serving→false (the Sidebar renders that as a
        // false "engine starting…"). An affirmative status:"starting" still flips immediately.
        if (alive) {
          const { serving, failStreak: fs } = nextServingState({
            current: useStore.getState().engineServing,
            reachable: !!h,
            starting: !!h && h.status === "starting",
            failStreak,
          });
          failStreak = fs;
          setEngineServing(serving);
        }
      } finally {
        if (alive) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [setStrategyRunning, setSystemHalted, setPaperTrading, setEngineServing]);
}
