import { fmtPct } from "@/console/lib/format";
import { StatTile } from "@/console/shared/StatTile";

/** One KPI for both sides of a comparison tile — portfolio left, S&P 500 right (#4513). */
export interface KpiPair {
  portfolio: number | null;
  benchmark: number | null;
}

export interface KpiBandProps {
  /** Since-inception return in %, deposit-neutral (from the equity curve), null → "—". */
  performance: KpiPair;
  /** Max drawdown in % (negative); null or >= 0 → "—". */
  maxDrawdown: KpiPair;
  /** Annualized Sharpe, null → "—" (e.g. live history still building). */
  sharpe: KpiPair;
  /** Shared "since inception" hint (baseline-aware). */
  sinceHint: string;
  /** Sharpe hint — "after ~20 live days" while building, else "realised curve". */
  sharpeHint: string;
  /** Left-side label: "Your portfolio" in the console, the brand wordmark on the live demo. */
  brandLabel?: string;
}

const PORTFOLIO_LABEL = "Your portfolio";

const signedTone = (v: number | null) =>
  v != null ? (v >= 0 ? "text-bull" : "text-bear") : undefined;

interface Side {
  text: string;
  tone?: string;
}

function ComparisonValue({
  left,
  right,
  leftLabel,
}: {
  left: Side;
  right: Side;
  leftLabel: string;
}) {
  // A brand wordmark keeps its own case; the plain label follows the sublabel style.
  const brand = leftLabel !== PORTFOLIO_LABEL;
  return (
    <div className="grid grid-cols-[1fr_1px_1fr] gap-3.5 items-end w-full">
      <div>
        <div
          className="sublabel font-body mb-1"
          style={
            brand
              ? { textTransform: "none", letterSpacing: "normal" }
              : undefined
          }
        >
          {leftLabel}
        </div>
        <div className={left.tone ?? "text-white/92"}>{left.text}</div>
      </div>
      <div className="self-stretch bg-white/10" aria-hidden="true" />
      <div className="text-right">
        <div className="sublabel font-body mb-1">S&amp;P 500</div>
        <div className={right.tone ?? "text-white/92"}>{right.text}</div>
      </div>
    </div>
  );
}

/**
 * KPI band (#3321 / C1, #4513) — three comparison tiles framed "since inception":
 * Performance · Max drawdown · Sharpe, each "Your portfolio" left vs "S&P 500" right.
 * Rendered with the existing StatTile; the value row holds the two sides.
 */
export function KpiBand({
  performance,
  maxDrawdown,
  sharpe,
  sinceHint,
  sharpeHint,
  brandLabel = PORTFOLIO_LABEL,
}: KpiBandProps) {
  const pct = (v: number | null): Side =>
    v != null ? { text: fmtPct(v), tone: signedTone(v) } : { text: "—" };
  const dd = (v: number | null): Side =>
    v != null && v < 0 ? { text: fmtPct(v), tone: "text-bear" } : { text: "—" };
  const ratio = (v: number | null): Side => ({
    text: v != null ? v.toFixed(2) : "—",
  });

  const tiles: Array<{ label: string; left: Side; right: Side; hint: string }> =
    [
      {
        label: "Performance",
        left: pct(performance.portfolio),
        right: pct(performance.benchmark),
        hint: sinceHint,
      },
      {
        label: "Max drawdown",
        left: dd(maxDrawdown.portfolio),
        right: dd(maxDrawdown.benchmark),
        hint: sinceHint,
      },
      {
        label: "Sharpe (annualized)",
        left: ratio(sharpe.portfolio),
        right: ratio(sharpe.benchmark),
        hint: sharpeHint,
      },
    ];
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {tiles.map((t) => (
        <StatTile
          key={t.label}
          label={t.label}
          value={
            <ComparisonValue
              left={t.left}
              right={t.right}
              leftLabel={brandLabel}
            />
          }
          hint={t.hint}
          baseline
        />
      ))}
    </div>
  );
}
