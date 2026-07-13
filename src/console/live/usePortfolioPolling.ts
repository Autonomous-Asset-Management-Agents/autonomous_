import { useEffect } from "react";
import { fetchPortfolioSummary } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import { adaptPortfolio } from "./portfolio";

const POLL_MS = 15_000; // gap BETWEEN polls (not a fixed cadence) — see below

/**
 * Polls /portfolio-summary (through the desktop-aware api layer) and writes the
 * adapted view into the store.
 *
 * Uses a recursive setTimeout loop, NOT setInterval: the next poll is scheduled
 * only AFTER the previous one settles, so a slow/blocked engine can never stack
 * overlapping in-flight requests (connection exhaustion). A failed poll is
 * swallowed — the store keeps its last value and the loop simply schedules the
 * next attempt.
 */
export function usePortfolioPolling(): void {
  const setPortfolio = useStore((s) => s.setPortfolio);
  const markSynced = useStore((s) => s.markSynced);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const loop = async () => {
      if (!alive) return;
      try {
        const resp = await fetchPortfolioSummary();
        if (alive && resp) {
          setPortfolio(adaptPortfolio(resp));
          markSynced();
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
  }, [setPortfolio, markSynced]);
}
