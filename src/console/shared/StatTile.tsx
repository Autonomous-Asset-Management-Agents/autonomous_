import type { ReactNode } from "react";

export interface StatTileProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  /** Value color class (e.g. "text-bull"); defaults to "text-white/92". */
  tone?: string;
  /** Keep the label lowercase (brand wordmark), overriding the .eyebrow uppercase. */
  labelLower?: boolean;
  /** Render the value row with `flex items-baseline` — the KPI band does, the balances band does not. */
  baseline?: boolean;
}

/**
 * Shared surface-flat stat tile — the exact card the Overview Balances band and KPI band render,
 * extracted (#3321 / C1) so the console dashboard AND the public live-demo render the identical
 * surface from one source. Pure/presentational: no hooks, all data via props.
 */
export function StatTile({
  label,
  value,
  hint,
  tone,
  labelLower,
  baseline,
}: StatTileProps) {
  const valueClass = baseline
    ? `flex items-baseline ${tone ?? "text-white/92"}`
    : (tone ?? "text-white/92");
  return (
    <div className="surface-flat rounded-xl p-4">
      <div
        className="eyebrow mb-2"
        style={
          labelLower
            ? { textTransform: "none", letterSpacing: "normal" }
            : undefined
        }
      >
        {label}
      </div>
      <div
        className={`num text-[22px] font-semibold tracking-tight2 ${valueClass}`}
      >
        {value}
      </div>
      <div className="text-[12px] text-white/45 mt-1">{hint}</div>
    </div>
  );
}

/** Money formatter shared by the bands/tables (mirrors console `useMoney().money`). */
export type MoneyFn = (
  n: number,
  opts?: { compact?: boolean; sign?: boolean },
) => string;
