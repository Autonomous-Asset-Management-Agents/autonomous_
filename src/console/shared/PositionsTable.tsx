import type { ReactNode } from "react";
import { fmtPct, fmtNum } from "@/console/lib/format";
import { Sparkline } from "@/console/shared/Sparkline";
import { IconChevronRight } from "@/console/shared/Icons";
import type { MoneyFn } from "@/console/shared/StatTile";

export interface PositionRow {
  symbol: string;
  name?: string;
  avgEntry: number;
  last: number;
  marketValue: number;
  unrealizedPct: number;
  /** #3421: today's price move (percent) vs. yesterday's close, or null/undefined
   *  when unknown — the row then shows only the total P&L, no fabricated 0%. */
  changeTodayPct?: number | null;
}

export interface PositionsTableProps {
  positions: PositionRow[];
  money: MoneyFn;
  investedPct: number;
  limit?: number;
  /** Behavioural seam: the console navigates ("View all"); the public demo omits it. */
  onViewAll?: () => void;
  /** Behavioural seam: the console renders a navigating <SymbolLink/>; the demo plain text. */
  renderSymbol?: (sym: string) => ReactNode;
}

/**
 * Top positions card (#3321 / C1) — extracted verbatim from Overview.tsx. The navigating
 * SymbolLink and the "View all" button are injected (renderSymbol / onViewAll) so the public
 * read-only live-demo reuses the identical surface without the desktop navigation.
 */
export function PositionsTable({
  positions,
  money,
  investedPct,
  limit = 5,
  onViewAll,
  renderSymbol,
}: PositionsTableProps) {
  const sym = (s: string): ReactNode => (renderSymbol ? renderSymbol(s) : s);
  return (
    <div className="surface p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="eyebrow mb-2">Top positions</div>
          <div className="text-[14px] font-semibold tracking-tight2 text-white/92">
            {positions.length} open · {fmtNum(investedPct, 1)}% invested
          </div>
        </div>
        {onViewAll && (
          <button onClick={onViewAll} className="btn-link">
            View all <IconChevronRight width={10} height={10} />
          </button>
        )}
      </div>
      <div className="space-y-0">
        {positions.slice(0, limit).map((p) => (
          <div
            key={p.symbol}
            className="flex items-center gap-3 py-2.5 border-b border-white/5 last:border-0"
          >
            <div className="w-24 min-w-0">
              <div className="font-bold text-[13px] tracking-tight2 text-white/92 truncate">
                {sym(p.symbol.split(".")[0])}
              </div>
              {p.name && p.name !== p.symbol && (
                <div className="text-[10px] text-white/40 truncate leading-tight">
                  {p.name}
                </div>
              )}
            </div>
            <Sparkline data={[p.avgEntry, p.last]} width={56} height={20} />
            <div className="num text-[12px] text-white/92 ml-auto">
              {money(p.marketValue, { compact: true })}
            </div>
            {/* #3421: today's move — a compact coloured column beside the total P&L
                (the card has no dedicated price cell). Hidden when unknown. */}
            {p.changeTodayPct != null && (
              <div className="num text-[12px] w-14 text-right">
                <span
                  className={p.changeTodayPct >= 0 ? "text-bull" : "text-bear"}
                >
                  {fmtPct(p.changeTodayPct)}
                </span>
                <span className="block text-[8px] text-white/30 leading-none">
                  today
                </span>
              </div>
            )}
            <div
              className={`num text-[12px] w-16 text-right ${p.unrealizedPct >= 0 ? "text-bull" : "text-bear"}`}
            >
              {fmtPct(p.unrealizedPct)}
            </div>
          </div>
        ))}
        {positions.length === 0 && (
          <div className="text-[13px] text-white/45 p-4 text-center">
            No open positions.
          </div>
        )}
      </div>
    </div>
  );
}
