import { useEffect } from "react";
import { fetchBenchmarkEquity } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import { adaptEquity } from "./equity";

const POLL_MS = 60_000; // equity curves move slowly; 60s gap between polls

/**
 * Polls /benchmark-equity → store equity/benchmark curves. Recursive setTimeout
 * loop (not setInterval), so a slow engine never stacks overlapping requests
 * (same robustness contract as usePortfolioPolling). Failed polls keep the last
 * value.
 */
export function useEquityPolling(): void {
  const setEquity = useStore((s) => s.setEquity);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (!alive) return;
      try {
        const resp = await fetchBenchmarkEquity();
        if (alive && resp) setEquity(adaptEquity(resp));
      } finally {
        if (alive) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [setEquity]);
}
