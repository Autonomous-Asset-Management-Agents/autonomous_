import { fmtEUR, fmtPct, fmtNum } from "@/console/lib/format";
import { Sparkline } from "@/console/shared/Sparkline";
import { useStore } from "@/console/store/useStore";
import { usePortfolioPolling } from "@/console/live/usePortfolioPolling";

/**
 * Console Positions page (G3, #1050). Ported from the desktop bundle; reads the
 * live portfolio from the store (polled from /portfolio-summary through the
 * desktop-aware api layer). The per-row 30d sparkline is a synthetic shape
 * around entry→last (the engine doesn't return per-symbol price history yet).
 */
export function Positions() {
  usePortfolioPolling();
  const positions = useStore((s) => s.positions);
  const cashEUR = useStore((s) => s.cashEUR);
  const currentEquity = useStore((s) => s.currentEquity);
  const invested = positions.reduce((a, p) => a + p.marketValue, 0);
  const investedPct = currentEquity ? (invested / currentEquity) * 100 : 0;
  const totalUnrealized = positions.reduce((a, p) => a + p.unrealizedEUR, 0);

  return (
    <div className="px-8 py-7 space-y-6">
      <div>
        <div className="eyebrow mb-2">Open positions</div>
        <div className="flex items-baseline gap-6 flex-wrap">
          <h1 className="text-[26px] font-bold tracking-tight2 text-white/92">{positions.length} positions</h1>
          <span className="text-[13px] text-white/55">
            Invested <span className="num text-white/92">{fmtEUR(invested)}</span> · {fmtPct(investedPct, 1).replace("+", "")} of book
          </span>
          <span className="text-[13px] text-white/55">
            Cash <span className="num text-white/92">{cashEUR !== null ? fmtEUR(cashEUR) : "—"}</span>
          </span>
          <span className={`text-[13px] num ${totalUnrealized >= 0 ? "text-bull" : "text-bear"}`}>
            Unrealized {fmtEUR(totalUnrealized, { sign: true })}
          </span>
        </div>
      </div>

      {positions.length === 0 ? (
        <div className="surface px-8 py-14 text-center text-white/35 text-[13px]">
          No open positions — or the engine is still warming up.
        </div>
      ) : (
        <div className="surface overflow-hidden">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-white/16">
                {["Symbol", "Name", "Qty", "Avg entry", "Last", "Market value", "Unrealized", "Weight", "Held", "30d"].map((h, i) => (
                  <th key={h} className={`px-4 py-3 font-semibold text-[10px] tracking-[0.12em] uppercase ${i >= 2 && i !== 9 ? "text-right" : i === 9 ? "text-center" : "text-left"}`}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map((p, i) => {
                const sparkData = Array.from({ length: 24 }, (_, k) =>
                  p.avgEntry + (p.last - p.avgEntry) * (k / 23) + Math.sin(k * 0.7 + i) * (p.last * 0.012),
                );
                return (
                  <tr key={p.symbol} className="border-t border-white/5 hover:bg-white/[0.025] transition-colors">
                    <td className="px-4 py-3.5 font-bold tracking-tight2 text-white/92">{p.symbol}</td>
                    <td className="px-4 py-3.5 text-white/55">{p.name}</td>
                    <td className="px-4 py-3.5 text-right num">{fmtNum(p.qty, 0)}</td>
                    <td className="px-4 py-3.5 text-right num text-white/55">€{fmtNum(p.avgEntry)}</td>
                    <td className="px-4 py-3.5 text-right num text-white/92">€{fmtNum(p.last)}</td>
                    <td className="px-4 py-3.5 text-right num text-white/92">{fmtEUR(p.marketValue)}</td>
                    <td className={`px-4 py-3.5 text-right num font-semibold ${p.unrealizedPct >= 0 ? "text-bull" : "text-bear"}`}>
                      <div>{fmtEUR(p.unrealizedEUR, { sign: true })}</div>
                      <div className="text-[10px] opacity-80">{fmtPct(p.unrealizedPct)}</div>
                    </td>
                    <td className="px-4 py-3.5 text-right num text-white/55">{fmtNum(p.weight, 1)}%</td>
                    <td className="px-4 py-3.5 text-right num text-white/55">{p.heldDays}d</td>
                    <td className="px-4 py-3.5 text-center">
                      <div className="inline-block">
                        <Sparkline data={sparkData} width={64} height={20} />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
