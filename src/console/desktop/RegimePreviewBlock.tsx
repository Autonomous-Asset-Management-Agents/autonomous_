import { useEffect, useState } from "react";
import { getRegimePreview, type RegimePreview } from "@/lib/api";

/**
 * Read-only "Market regime reading" block (#3361) under the Sizing group of the
 * Advanced section. Shows what the regime throttle WOULD do at the APPLIED settings —
 * also while the throttle is off, so the signal can be observed before it is armed.
 * Purely factual: numbers and state, no advice. Changes nothing; a missing endpoint
 * or data gap degrades to a neutral "not available" line, never a crash.
 */

const COMPONENT_LABELS: Array<[string, string]> = [
  ["credit", "Credit (HYG)"],
  ["rates", "Rates (TLT)"],
  ["oil", "Oil (USO)"],
  ["correlation", "Sector correlation"],
  ["spy_drawdown", "Index decline (SPY)"],
];

const fmt = (v: number | null | undefined): string =>
  typeof v === "number" && Number.isFinite(v) ? v.toFixed(1) : "—";

export function RegimePreviewBlock() {
  const [reading, setReading] = useState<RegimePreview | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    // Promise.resolve() hop: a synchronous throw (partially mocked api module) lands
    // in .catch instead of crashing the section.
    Promise.resolve()
      .then(() => getRegimePreview())
      .then((r) => {
        if (alive) setReading(r ?? null);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const available = !failed && reading?.available === true;

  return (
    <div data-testid="regime-preview" className="py-3 border-t border-white/6">
      <div className="flex items-baseline justify-between gap-4">
        <div className="text-[13px] font-semibold text-white/90">
          Market regime reading
          <span className="ml-2 rounded-full bg-white/8 px-2 py-0.5 text-[10px] font-medium text-white/50 align-middle">
            Read-only
          </span>
        </div>
        {available && (
          <span className="num text-[10.5px] text-white/30">as of {reading?.asof}</span>
        )}
      </div>

      {!available && (
        <div
          data-copy="description"
          className="text-[11.5px] text-white/45 leading-relaxed mt-0.5"
        >
          {failed || reading
            ? "No current reading available. Orders keep their normal size."
            : "Loading…"}
        </div>
      )}

      {available && reading && (
        <>
          <div
            data-copy="description"
            className="text-[11.5px] text-white/45 leading-relaxed mt-0.5 max-w-sm"
          >
            Based on the applied settings, not on pending changes. 0 is calm, 100 is
            stressed, measured against the reading's own history.
          </div>
          <div className="mt-3 grid grid-cols-3 gap-3">
            <div>
              <div className="text-[10.5px] text-white/30">Reading</div>
              <div className="num text-[15px] text-white/90">{fmt(reading.score)}</div>
            </div>
            <div>
              <div className="text-[10.5px] text-white/30">
                Threshold ({reading.threshold_percentile}th pct)
              </div>
              <div className="num text-[15px] text-white/90">{fmt(reading.threshold)}</div>
            </div>
            <div>
              <div className="text-[10.5px] text-white/30">New buy orders now</div>
              <div data-testid="regime-preview-state" className="num text-[15px] text-white/90">
                {reading.would_throttle
                  ? reading.enabled
                    ? `× ${reading.factor.toFixed(2)}`
                    : `× ${(reading.size_factor_setting ?? reading.factor).toFixed(2)} if on`
                  : "Normal size"}
              </div>
            </div>
          </div>
          <div className="mt-3 space-y-1.5">
            {COMPONENT_LABELS.map(([key, label]) => {
              const v = reading.components?.[key];
              const w = reading.weights?.[key];
              const pct = typeof v === "number" && Number.isFinite(v) ? v : null;
              return (
                <div key={key} className="flex items-center gap-3">
                  <span className="text-[11px] text-white/45 w-36 shrink-0">
                    {label}
                    {typeof w === "number" && (
                      <span className="text-white/25"> · {Math.round(w * 100)}%</span>
                    )}
                  </span>
                  <span className="flex-1 h-1 rounded-full bg-white/8 overflow-hidden">
                    <span
                      className="block h-full bg-white/40"
                      style={{ width: `${pct === null ? 0 : Math.max(0, Math.min(100, pct))}%` }}
                    />
                  </span>
                  <span className="num text-[11px] text-white/60 w-10 text-right">
                    {fmt(pct)}
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
