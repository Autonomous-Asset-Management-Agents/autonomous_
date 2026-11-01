import { useEffect, useState } from "react";
import { getSizingPreview, type SizingPreview } from "@/lib/api";
import { SIZING_METHOD_LABELS } from "@/console/desktop/advancedTradingFields";

/**
 * Read-only "Effect on a new position" block (#5005) under the Sizing group of the
 * Advanced section. Shows what the engine's sizer returns at the APPLIED settings: the
 * target of a new position, the cash per open slot and the limit that binds. A model
 * calculation without single-name signals, numbers only, no advice. Changes nothing;
 * an unreachable engine degrades to a neutral line and the settings stay usable.
 */

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

const fmtPct = (v: number | null | undefined): string =>
  isNum(v) ? `${Number((v * 100).toFixed(2))}%` : "—";

const fmtUsd = (v: number | null | undefined): string =>
  isNum(v)
    ? `$${v.toLocaleString("en-US", { maximumFractionDigits: v % 1 === 0 ? 0 : 2 })}`
    : "—";

const limitLabel = (p: SizingPreview): string => {
  const code = p.binding_limit ?? p.zero_reason;
  return code ? code.replace(/_/g, " ") : "None";
};

const methodLabel = (mode: SizingPreview["sizing_mode"]): string =>
  SIZING_METHOD_LABELS[mode === "conviction" ? "off" : (mode ?? "")] ?? "—";

export function SizingPreviewBlock() {
  const [preview, setPreview] = useState<SizingPreview | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    // Promise.resolve() hop: a synchronous throw (partially mocked api module) lands
    // in .catch instead of crashing the section.
    Promise.resolve()
      .then(() => getSizingPreview())
      .then((p) => {
        if (alive) setPreview(p ?? null);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const available = !failed && preview?.available === true;

  return (
    <div data-testid="sizing-preview" className="py-3 border-t border-white/6">
      <div className="text-[13px] font-semibold text-white/90">
        Effect on a new position
        <span className="ml-2 rounded-full bg-white/8 px-2 py-0.5 text-[10px] font-medium text-white/50 align-middle">
          Read-only
        </span>
      </div>

      {!available && (
        <div
          data-copy="description"
          className="text-[11.5px] text-white/45 leading-relaxed mt-0.5"
        >
          {failed || preview ? "No model calculation available." : "Loading…"}
        </div>
      )}

      {available && preview && (
        <>
          <div
            data-copy="description"
            className="text-[11.5px] text-white/45 leading-relaxed mt-0.5 max-w-sm"
          >
            Model calculation without single-name signals, based on the applied settings,
            not on pending changes. An actual order also depends on the stock's own
            volatility and stop distance.
          </div>
          <div className="mt-3 grid grid-cols-3 gap-3">
            <div>
              <div className="text-[10.5px] text-white/30">Target</div>
              <div data-testid="sizing-preview-target" className="num text-[15px] text-white/90">
                {fmtPct(preview.target_weight)} · {fmtUsd(preview.target_value)}
              </div>
            </div>
            <div>
              <div className="text-[10.5px] text-white/30">Cash per open slot</div>
              <div data-testid="sizing-preview-cash" className="num text-[15px] text-white/90">
                {fmtUsd(preview.cash_per_slot)}
              </div>
            </div>
            <div>
              <div className="text-[10.5px] text-white/30">Limited by</div>
              <div data-testid="sizing-preview-limit" className="num text-[15px] text-white/90">
                {limitLabel(preview)}
              </div>
            </div>
          </div>
          <div
            data-testid="sizing-preview-method"
            className="mt-2 text-[11px] text-white/45"
          >
            Sizing method: {methodLabel(preview.sizing_mode)}
          </div>
        </>
      )}
    </div>
  );
}
