import { useEffect } from "react";
import { fetchBenchmarkEquity } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import { adaptEquity } from "./equity";
import { readEquityCache, writeEquityCache } from "./equityCache";

const POLL_MS = 60_000; // equity curves move slowly; 60s gap between polls

// #2588: responses that mean "the fetch/engine FAILED" (not "the history is
// genuinely empty"). These must never overwrite a good curve with an empty one —
// the engine returns internal_error with HTTP 200 + points:[] (e.g. the desktop
// schema-drift outage), and the api layer returns fetch_failed on a network
// error. Honest empty states ("No benchmark run yet.", "no_live_history") are
// NOT listed: there the empty curve is the truth and must render.
const DEGRADED_MESSAGES = new Set(["internal_error", "fetch_failed"]);

/**
 * Polls /benchmark-equity → store equity/benchmark curves. Recursive setTimeout
 * loop (not setInterval), so a slow engine never stacks overlapping requests
 * (same robustness contract as usePortfolioPolling). Failed polls keep the last
 * value.
 */
export function useEquityPolling(): void {
  const setEquity = useStore((s) => s.setEquity);
  useEffect(() => {
    // PR-A instant-data: paint the last cached curve immediately, before the
    // (event-loop-starved, #2497) /benchmark-equity poll resolves. The fresh
    // poll below silently overwrites it.
    const cached = readEquityCache();
    if (cached) setEquity(cached);
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (!alive) return;
      try {
        const resp = await fetchBenchmarkEquity();
        if (alive && resp) {
          if (resp.message && DEGRADED_MESSAGES.has(resp.message)) {
            // Loud + non-destructive: keep the last good curve on screen.
            console.warn(
              `equity poll degraded (${resp.message}) — keeping the last good curve (#2588)`,
            );
          } else {
            const view = adaptEquity(resp);
            setEquity(view);
            writeEquityCache(view);
          }
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
  }, [setEquity]);
}
