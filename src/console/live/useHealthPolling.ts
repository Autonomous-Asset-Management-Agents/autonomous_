import { useEffect } from "react";
import { fetchHealth } from "@/lib/api";
import { useStore } from "@/console/store/useStore";

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
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (!alive) return;
      try {
        const h = await fetchHealth();
        if (alive && h) {
          setStrategyRunning(h.strategy_running ?? false);
          setSystemHalted(h.system_halted ?? false);
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
  }, [setStrategyRunning, setSystemHalted]);
}
