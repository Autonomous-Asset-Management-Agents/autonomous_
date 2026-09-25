import { useEffect, useState } from "react";

// Public live-demo snapshot contract (mirrors scripts/demo_snapshot.py PublicSnapshot, #1587).
export type DemoPosition = {
  symbol: string;
  qty: number;
  market_value: number;
  unrealized_pl_pct: number;
};
export type DemoDecision = {
  symbol: string;
  action: string;
  consensus: number;
  conviction: number | null;
  summary: string;
  /** RQ-1 (#1516): final execution-gate outcome (Iron-Dome / risk / kill-switch),
   *  server-joined by the engine. "executed" | "blocked:*" | "pending"; null/absent
   *  for a HOLD → the demo badge renders nothing. */
  execution_outcome?: string | null;
};
export type DemoReport = {
  symbol: string;
  summary: string;
  sentiment: string | null;
  as_of: string | null;
} | null;
export type DemoEquityPoint = { date: string; equity: number; benchmark: number | null };
export type DemoSnapshot = {
  generated_at: string;
  status: string;
  disclaimer: string;
  equity: number;
  cash: number;
  day_pl_pct: number;
  positions: DemoPosition[];
  decisions: DemoDecision[];
  report: DemoReport;
  equity_curve: DemoEquityPoint[];
};

const STALE_AFTER_MS = 2 * 60 * 60 * 1000; // 2h — runner publishes hourly, so only "paused" after ~2 missed cycles

/**
 * Polls the curated, public snapshot.json (NOT the live backend — that is what the existing
 * console hooks do). Fail-soft: on a fetch error or a stale snapshot the page shows a calm
 * "paused" state instead of an error (Epic #1582 graceful degradation).
 */
// The snapshot URL is build-configurable: set VITE_DEMO_SNAPSHOT_URL to the published
// object (e.g. a public GCS URL the runner writes to) so the live site reads fresh data;
// defaults to the static /demo-snapshot.json bundled in dist (fixture / local preview). (#96)
const DEFAULT_SNAPSHOT_URL =
  (import.meta.env.VITE_DEMO_SNAPSHOT_URL as string | undefined) || "/demo-snapshot.json";

export function useSnapshotPolling(url = DEFAULT_SNAPSHOT_URL, intervalMs = 60 * 60 * 1000) {
  const [snapshot, setSnapshot] = useState<DemoSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const res = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as DemoSnapshot;
        if (active) {
          setSnapshot(data);
          setError(null);
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const id = setInterval(load, intervalMs);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [url, intervalMs]);

  // Re-evaluate staleness on a timer (kept out of render so the hook stays pure).
  useEffect(() => {
    const check = () =>
      setStale(
        snapshot
          ? Date.now() - new Date(snapshot.generated_at).getTime() > STALE_AFTER_MS
          : false
      );
    check();
    const id = setInterval(check, 5 * 60 * 1000);
    return () => clearInterval(id);
  }, [snapshot]);

  const paused = !!error || stale;
  return { snapshot, error, paused };
}
