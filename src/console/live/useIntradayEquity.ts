import { useEffect, useMemo, useState } from "react";
import { fetchPortfolioIntraday } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import { adaptIntraday, type EquityCurvePoint } from "./equity";
import { isIntradayRange, pickIntradayCurve, type IntradayStore } from "./intradayEquity";
import type { EquityRange } from "./equityRange";
import { writeIntradayCache } from "./equityCache";

const POLL_MS = 60_000; // intraday equity refreshes on the same cadence as the daily poll

/**
 * #2124: fetch the INTRADAY equity curve for the Overview's short ranges (1D/1W)
 * into LOCAL state — deliberately NOT the global store, so the daily
 * `equityCurve` (and the `lastEquity`/dayPL KPI derived from it) stays intact
 * (Archon review #4). Returns [] for long ranges or when intraday is
 * unavailable, so the caller fails soft to the daily curve.
 *
 * #3145: the curves are kept PER RANGE. The previous version held one curve and cleared it on
 * every range change, so switching 1D→1W dropped to the daily curve for ~1 s — a visible flash
 * of a different chart with different values. `pickIntradayCurve` serves the range's own
 * last-known curve, or seeds it from the persisted cache, and never another range's data.
 */
export function useIntradayEquity(range: EquityRange): EquityCurvePoint[] {
  const [store, setStore] = useState<IntradayStore>({});
  // A Paper↔Live switch must not repaint the OLD account's line on the freshly-switched page:
  // every account-scoped curve is dropped the moment the switch state changes. Done during
  // render (React's "adjust state when a prop changes" pattern) rather than in an effect, so
  // no frame can paint the old account's line first.
  const modeSwitch = useStore((s) => s.modeSwitch);
  const [seenSwitch, setSeenSwitch] = useState(modeSwitch);
  if (seenSwitch !== modeSwitch) {
    setSeenSwitch(modeSwitch);
    setStore({});
  }

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
        // Not while a switch is mid-flight: an OLD-account in-flight poll would leak its line.
        if (alive && next.length >= 2 && useStore.getState().modeSwitch == null) {
          setStore((prev) => ({ ...prev, [range]: next }));
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
      // #3145: the curve is NOT dropped here — keeping it is what makes a switch back
      // instant. A mode switch clears it explicitly (below), which is the case where the
      // old data would actually be wrong.
    };
  }, [range]);

  return useMemo(
    () => (modeSwitch != null ? [] : pickIntradayCurve(range, store)),
    [range, store, modeSwitch],
  );
}
