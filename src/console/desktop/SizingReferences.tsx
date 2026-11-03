import type { TradingSettings } from "@/lib/api";
import { formatValue } from "@/console/desktop/fieldUnits";

/**
 * #5046: sizing controls that live on another card, shown under the sizing group with their
 * current value. A reference, not a copy: each has its own apply path (Vol-Target and VIX
 * through the registry, positions through /api/portfolio-shape), so there is no second place
 * to write here. A value the engine did not send shows a dash, also for a switch.
 *
 * Advanced only: the strategy editor shares the field table, not this block.
 */

const DASH = "—";

/** "0.0150" → "1.5%"; null when the value is missing or not a number. */
const percent = (raw: string | undefined): string | null => {
  if (raw === undefined) return null;
  const shown = formatValue("fraction", raw);
  return shown === raw ? null : shown;
};

const scrollTo = (id: string) =>
  document.getElementById(id)?.scrollIntoView?.({ behavior: "smooth", block: "center" });

interface SizingReferencesProps {
  /** Empty after a failed read: every row then shows the dash, never an old value. */
  settings: Partial<TradingSettings>;
  positions: number | null;
}

interface Reference {
  id: string;
  label: string;
  value: string;
  /** A control further up this page; without it the row links to the Agents page. */
  anchor?: string;
}

export function SizingReferences({ settings, positions }: SizingReferencesProps) {
  const s = settings.settings ?? {};
  const volOn = s.VOL_TARGETING_SIZING_ENABLED;
  const vixOn = s.VIX_RISK_AGENT_ENABLED;
  const skewOn = s.SKEW_SIZE_TILT_ENABLED;
  const volTarget = percent(s.VOL_TARGET_DAILY_VOL);
  const skewCap = percent(s.SKEW_SIZE_TILT_CAP);

  const volValue = (): string => {
    if (volOn === "false") return "Off";
    return volOn === "true" && volTarget ? `${volTarget} per day` : DASH;
  };

  const vixValue = (): string => {
    // vol_targeting_scaler returns before it reads the influence when volatility sizing is off.
    if (volOn === "false" || vixOn === "false") return "Off";
    // #3619: with the IV forecast off this agent votes on the mean; the Agents card then has
    // no size control for it.
    if (settings.implied_vol_forecast_enabled === false) return "Not a sizing input";
    if (volOn !== "true" || vixOn !== "true") return DASH;
    return percent(s.VIX_SIZE_INFLUENCE) ?? DASH;
  };

  const skewValue = (): string => {
    if (skewOn === "false") return "Off";
    return skewOn === "true" && skewCap ? `±${skewCap}` : DASH;
  };

  const references: Reference[] = [
    { id: "vol-target", label: "Daily vol target", value: volValue(), anchor: "volatility-sizing" },
    {
      id: "positions",
      label: "Maximum positions",
      value: positions !== null ? `${positions} positions` : DASH,
      anchor: "max-positions",
    },
    { id: "vix", label: "VIX size influence", value: vixValue() },
    { id: "skew", label: "Skew sizing tilt", value: skewValue() },
  ];

  return (
    <div data-testid="sizing-references" className="py-3 border-t border-white/6">
      <div className="text-[13px] font-semibold text-white/90">Set on other cards</div>
      <div
        data-copy="description"
        className="text-[11.5px] text-white/45 leading-relaxed mt-0.5 max-w-sm"
      >
        These also act on the size of a new position. They are changed where they are set.
      </div>
      {references.map((r) => (
        <div
          key={r.id}
          data-testid={`sizing-ref-${r.id}`}
          className="flex items-baseline justify-between gap-4 mt-2"
        >
          <span className="text-[12px] text-white/70">{r.label}</span>
          <span className="flex items-baseline gap-3">
            <span className="num text-[12px] text-white/60">{r.value}</span>
            {r.anchor ? (
              <button
                type="button"
                onClick={() => scrollTo(r.anchor as string)}
                className="text-[11px] text-white/45 underline"
                aria-label={`Go to ${r.label} in Trading controls`}
              >
                Trading controls
              </button>
            ) : (
              <a
                href="#trading-agents"
                className="text-[11px] text-white/45 underline"
                aria-label={`Go to ${r.label} on the Agents page`}
              >
                Agents
              </a>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
