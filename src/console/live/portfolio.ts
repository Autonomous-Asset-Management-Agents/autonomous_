import type { PortfolioSummaryResponse } from "@/lib/api";
import { getCompanyName } from "@/console/lib/companyName";

/**
 * Portfolio adapter (G3, #1050): map the engine's /portfolio-summary response
 * to the console's Position shape. The engine returns snake_case fields and no
 * per-share price; the console table shows avg-entry, last price and book
 * weight, so those are derived here:
 *   last     = market_value / qty
 *   avgEntry = (market_value - unrealized_pnl) / qty     (cost basis)
 *   weight   = market_value / equity * 100               (% of book)
 *   cash     = engine `cash` (real Alpaca account.cash) when present, else the derived
 *              equity - Σ market_value fallback (legacy engines that send no cash field)
 */
export interface ConsolePosition {
  symbol: string;
  name: string;
  qty: number;
  avgEntry: number;
  last: number;
  marketValue: number;
  unrealizedEUR: number;
  unrealizedPct: number;
  weight: number;
  heldDays: number;
}

export interface PortfolioView {
  positions: ConsolePosition[];
  cashEUR: number | null;
  currentEquity: number | null;
  /** PR-B: real account currency ISO (from /portfolio-summary), fail-soft "USD". */
  accountCurrency: string;
  /** Bug B (0.3.5): which account this snapshot belongs to (engine PAPER_TRADING mode);
   *  null when the response omits it (error path / pre-0.3.5 engine). */
  paperTrading: boolean | null;
}

export function adaptPortfolio(resp: PortfolioSummaryResponse | null | undefined): PortfolioView {
  const equity = typeof resp?.equity === "number" ? resp.equity : null;
  const raw = Array.isArray(resp?.positions) ? resp!.positions! : [];

  const positions: ConsolePosition[] = raw.map((p) => {
    // Nullish-coalesce, never `|| 0` — a real 0 (flat pnl, a closing position)
    // must survive, not be masked as "missing". qty guards div-by-zero only;
    // a short (qty < 0) flows through and yields the correct positive prices
    // (negative market_value / negative qty).
    const qty = p.qty ?? 0;
    const mv = p.market_value ?? 0;
    const pnl = p.unrealized_pnl ?? 0;
    return {
      symbol: p.symbol,
      name: getCompanyName(p.symbol) ?? p.symbol, // full name from the bundled SEC map; falls back to the ticker
      qty,
      last: qty ? mv / qty : 0,
      avgEntry: qty ? (mv - pnl) / qty : 0,
      marketValue: mv,
      unrealizedEUR: pnl,
      unrealizedPct: p.unrealized_pnl_pct ?? 0,
      weight: equity ? (mv / equity) * 100 : 0,
      heldDays: p.days_held ?? 0,
    };
  });

  const invested = positions.reduce((a, p) => a + p.marketValue, 0);
  // Prefer the engine's REAL broker cash (Alpaca account.cash) — the authoritative Cash/margin
  // figure, which can be negative (margin used) and differs from `equity − invested` whenever
  // buying power / unsettled funds are in play. Fall back to the derived value only when the engine
  // sends no finite cash (error path / pre-0.4.4 engine), so an older engine still shows a number.
  const realCash =
    typeof resp?.cash === "number" && Number.isFinite(resp.cash) ? resp.cash : null;
  return {
    positions,
    currentEquity: equity,
    cashEUR: realCash != null ? realCash : equity != null ? equity - invested : null,
    accountCurrency: resp?.currency ?? "USD",
    paperTrading: typeof resp?.paper_trading === "boolean" ? resp.paper_trading : null,
  };
}
