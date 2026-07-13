import { fmtEUR, fmtPct, fmtNum } from "@/console/lib/format";
import { Sparkline } from "@/console/shared/Sparkline";
import { useStore } from "@/console/store/useStore";
import { usePortfolioPolling } from "@/console/live/usePortfolioPolling";
import { IconLightbulb } from "@/console/shared/Icons";

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
    <div className="px-8 py-7 space-y-6 max-w-[1100px]">
      <div>
        <div className="eyebrow mb-2">Active Positions</div>
        <div className="flex items-baseline gap-6 flex-wrap">
          <h1 className="text-[26px] font-bold tracking-tight2 text-white/92">{positions.length} Active Positions</h1>
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
        <p className="text-white/45 text-[13px] mt-1.5">
          Live overview of active investments, cash reserves, and unrealized portfolio returns.
        </p>
      </div>

      <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
        <IconLightbulb width={16} height={16} className="text-white/70 mt-1 shrink-0" />
        <div className="text-[13.5px] text-white/70 leading-relaxed space-y-2">
          <p>
            <span className="font-semibold text-white/90">Portfolio Allocation & Performance.</span>{" "}
            This view displays assets currently held by the trading bot, updated in real-time based on market feeds. Key performance indicators include your average entry price, current market value, and unrealized profit/loss (P&L) relative to total book equity.
          </p>
          <p className="text-[13px] text-white/45">
            <span className="text-white/60">Tip:</span> The trend column shows a simplified visual of the asset's price trajectory from your average entry point to the last traded price.
          </p>
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
                {["Symbol", "Name", "Qty", "Avg entry", "Last", "Market value", "Unrealized", "Weight", "Held", "Trend"].map((h, i) => (
                  <th key={h} className={`px-4 py-3 font-semibold text-[10px] tracking-[0.12em] uppercase ${i >= 2 && i !== 9 ? "text-right" : i === 9 ? "text-center" : "text-left"}`}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => {
                // Honest sparkline: a real avgEntry→last segment (2 points), not a
                // synthetic Math.sin curve — there is no per-symbol intraday history.
                const sparkData = [p.avgEntry, p.last];
                return (
                  <tr key={p.symbol} className="border-t border-white/5 hover:bg-white/[0.025] transition-colors">
                    <td className="px-4 py-3.5 font-bold tracking-tight2 text-white/92">{p.symbol}</td>
                    <td className="px-4 py-3.5 text-white/55">{p.name && p.name !== p.symbol ? p.name : "—"}</td>
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
