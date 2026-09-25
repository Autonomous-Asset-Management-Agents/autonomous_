import { fmtPct } from "@/console/lib/format";
import { StatTile } from "@/console/shared/StatTile";

export interface KpiBandProps {
  /** Deposit-neutral since-inception return (TWR), null → "—". */
  totalReturnPct: number | null;
  /** S&P 500 since-inception return, null → "—". */
  benchInception: number | null;
  /** Cash-flow-adjusted max drawdown (negative); >= 0 → "—". */
  maxDD: number;
  /** Annualized Sharpe, null → "—" (e.g. live history still building). */
  sharpe: number | null;
  /** Shared "since inception" hint (baseline-aware). */
  sinceHint: string;
  /** Sharpe hint — "after ~20 live days" while building, else "realised curve". */
  sharpeHint: string;
  /** Brand wordmark for the first card (default "autonomous_"). */
  brandLabel?: string;
}

/**
 * KPI band (#3321 / C1) — the return/risk group framed "since inception"
 * (autonomous_ · S&P 500 · Max drawdown · Sharpe). Extracted verbatim from Overview.tsx.
 */
export function KpiBand({
  totalReturnPct,
  benchInception,
  maxDD,
  sharpe,
  sinceHint,
  sharpeHint,
  brandLabel = "autonomous_",
}: KpiBandProps) {
  const tiles: Array<{
    label: string;
    value: string;
    hint: string;
    tone?: string;
    labelLower?: boolean;
  }> = [
    {
      label: brandLabel,
      labelLower: true,
      value: totalReturnPct != null ? fmtPct(totalReturnPct) : "—",
      hint: sinceHint,
      tone:
        totalReturnPct != null
          ? totalReturnPct >= 0
            ? "text-bull"
            : "text-bear"
          : undefined,
    },
    {
      label: "S&P 500",
      value: benchInception != null ? fmtPct(benchInception) : "—",
      hint: sinceHint,
      tone:
        benchInception != null
          ? benchInception >= 0
            ? "text-bull"
            : "text-bear"
          : undefined,
    },
    {
      label: "Max drawdown",
      value: maxDD < 0 ? fmtPct(maxDD) : "—",
      hint: sinceHint,
      tone: "text-bear",
    },
    {
      label: "Sharpe (annualized)",
      value: sharpe != null ? sharpe.toFixed(2) : "—",
      hint: sharpeHint,
    },
  ];
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {tiles.map((t) => (
        <StatTile
          key={t.label}
          label={t.label}
          value={t.value}
          hint={t.hint}
          tone={t.tone}
          labelLower={t.labelLower}
          baseline
        />
      ))}
    </div>
  );
}
