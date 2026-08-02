import { useEffect, useState } from "react";
import { fetchPortfolioIntraday } from "@/lib/api";
import { adaptIntraday, type EquityCurvePoint } from "./equity";
import { isIntradayRange } from "./intradayEquity";
import type { EquityRange } from "./equityRange";
import { readIntradayCache, writeIntradayCache } from "./equityCache";

const POLL_MS = 60_000; // intraday equity refreshes on the same cadence as the daily poll

/**
 * #2124: fetch the INTRADAY equity curve for the Overview's short ranges (1D/1W)
 * into LOCAL state — deliberately NOT the global store, so the daily
 * `equityCurve` (and the `lastEquity`/dayPL KPI derived from it) stays intact
 * (Archon review #4). Returns [] for long ranges or when intraday is
 * unavailable, so the caller fails soft to the daily curve.
 */
export function useIntradayEquity(range: EquityRange): EquityCurvePoint[] {
  const [curve, setCurve] = useState<EquityCurvePoint[]>(() => {
    if (!isIntradayRange(range)) return [];
    return readIntradayCache(range);
  });

  useEffect(() => {
    if (!isIntradayRange(range)) {
      return;
    }
    let alive = true;

    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (!alive) return;
      try {
        const resp = await fetchPortfolioIntraday(range);
        // Fail-soft: an empty/transient response (event-loop stall, pre-market)
        // must NOT wipe a good curve — keep the last good one until fresh data.
        const next = adaptIntraday(resp);
        if (alive && next.length >= 2) {
          setCurve(next);
          writeIntradayCache(range, next);
        }
      } finally {
        if (alive) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      setCurve([]);
    };
  }, [range]);

  return isIntradayRange(range) ? curve : [];
}
