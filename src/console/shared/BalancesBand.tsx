import { fmtNum } from "@/console/lib/format";
import { StatTile, type MoneyFn } from "@/console/shared/StatTile";

export interface BalancesBandProps {
  cashEUR: number | null;
  currentEquity: number | null;
  investedEUR: number;
  investedPct: number;
  positionsCount: number;
  netDeposits: number | null;
  startEquity: number | null;
  iso: string;
  money: MoneyFn;
}

/**
 * Balances band (#3321 / C1) — liquidity/composition tiles (Cash · Invested · Net deposits).
 * Extracted verbatim from Overview.tsx so the console and the live-demo render the same band.
 */
export function BalancesBand({
  cashEUR,
  currentEquity,
  investedEUR,
  investedPct,
  positionsCount,
  netDeposits,
  startEquity,
  iso,
  money,
}: BalancesBandProps) {
  const tiles = [
    {
      label: "Cash / buying power",
      value: cashEUR != null ? money(cashEUR, { compact: true }) : "—",
      hint:
        cashEUR != null && cashEUR < 0
          ? `margin used · ${iso}`
          : `settled cash · ${iso}`,
    },
    {
      label: "Invested",
      value:
        currentEquity != null ? money(investedEUR, { compact: true }) : "—",
      hint: `${fmtNum(investedPct, 1)}% of book · ${positionsCount} ${positionsCount === 1 ? "position" : "positions"}`,
    },
    {
      label: "Net deposits",
      value:
        netDeposits != null
          ? money(netDeposits, { sign: true, compact: true })
          : "—",
      hint:
        startEquity != null
          ? `started ${money(startEquity, { compact: true })}`
          : "since funding",
    },
  ];
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
      {tiles.map((t) => (
        <StatTile key={t.label} label={t.label} value={t.value} hint={t.hint} />
      ))}
    </div>
  );
}
