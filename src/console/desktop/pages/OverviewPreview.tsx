import { useEffect } from "react";
import { useStore } from "@/console/store/useStore";
import { Overview } from "@/console/desktop/pages/Overview";
import { adaptPortfolio } from "@/console/live/portfolio";
import { adaptEquity } from "@/console/live/equity";
import { adaptRoundTableDecisions } from "@/console/live/roundTable";

/**
 * Dev-only visual preview of the restructured Overview (#overview-ia). Seeds the store with
 * representative data and renders the REAL <Overview/> so the arrangement can be reviewed in the
 * actual design — no engine needed (the page's polls fail-soft in the browser and keep this seed).
 *
 *     npm run dev  →  http://localhost:8082/overview-preview   (or the port Vite prints)
 *
 * Live mode + a ≥20-day curve is seeded on purpose so the full KPI row AND the relocated (live-only)
 * Order book are both visible. Not shipped in the desktop app.
 */

// 22 daily points → liveHistoryReady is true (no "building" placeholder), so S&P/Sharpe render.
function mkCurve(base: number, totalDrift: number) {
  const start = Date.UTC(2026, 6, 1);
  return Array.from({ length: 22 }, (_, i) => ({
    date: new Date(start + i * 86_400_000).toISOString().slice(0, 10),
    equity: Math.round(base * (1 + (totalDrift * i) / 21 + (i % 3 === 0 ? 0.004 : -0.002))),
  }));
}

const SUMMARY = {
  status: "success" as const,
  equity: 105_000,
  last_equity: 104_200,
  currency: "USD",
  paper_trading: false,
  cash: 18_900, // real broker cash (PR #2806) — deliberately ≠ equity − Σ market_value
  positions: [
    { symbol: "AAPL", qty: 10, market_value: 2_000, unrealized_pnl: 150, unrealized_pnl_pct: 8.11, days_held: 12 },
    { symbol: "NVDA", qty: 5, market_value: 6_000, unrealized_pnl: -120, unrealized_pnl_pct: -1.96, days_held: 4 },
    { symbol: "MSFT", qty: 8, market_value: 3_200, unrealized_pnl: 64, unrealized_pnl_pct: 2.04, days_held: 20 },
    { symbol: "HIG", qty: 20, market_value: 2_860, unrealized_pnl: 90, unrealized_pnl_pct: 3.25, days_held: 2 },
  ],
};

const BENCH = {
  points: mkCurve(100_000, 0.05),
  spy_points: mkCurve(100_000, 0.03),
  twr_pct: 4.8,
  net_deposits: 5_000,
  pnl_net_of_deposits: 0,
  initial_capital: 100_000,
  final_equity: 105_000,
};

const DECISIONS = {
  status: "success" as const,
  total: 3,
  decisions: [
    { symbol: "AAPL", signal_action: "BUY", consensus_score: 0.72, gatekeeper_approved: true, timestamp: "2026-07-22T14:32:00Z",
      votes: [
        { name: "Momentum", agent_name: "momentum", score: 0.81, weight: 0.3, signal: "BUY", vetoed: false, reasoning: "" },
        { name: "Value", agent_name: "value", score: 0.55, weight: 0.25, signal: "BUY", vetoed: false, reasoning: "" },
        { name: "Risk", agent_name: "risk", score: 0.40, weight: 0.2, signal: "HOLD", vetoed: false, reasoning: "" },
      ] },
    { symbol: "TSLA", signal_action: "SELL", consensus_score: 0.29, gatekeeper_approved: true, timestamp: "2026-07-22T14:31:00Z",
      votes: [
        { name: "Momentum", agent_name: "momentum", score: 0.20, weight: 0.3, signal: "SELL", vetoed: false, reasoning: "" },
        { name: "Sentiment", agent_name: "sentiment", score: 0.35, weight: 0.2, signal: "SELL", vetoed: false, reasoning: "" },
      ] },
    { symbol: "NVDA", signal_action: "HOLD", consensus_score: 0.55, gatekeeper_approved: false, gatekeeper_reason: "Position size cap", timestamp: "2026-07-22T14:30:00Z",
      votes: [
        { name: "Momentum", agent_name: "momentum", score: 0.55, weight: 0.3, signal: "BUY", vetoed: false, reasoning: "" },
        { name: "Risk", agent_name: "risk", score: 0.50, weight: 0.2, signal: "HOLD", vetoed: true, reasoning: "" },
      ] },
  ],
};

const ORDERS = [
  { id: "o1", symbol: "HIG", side: "buy" as const, qty: 10, type: "limit", limitPrice: 54.1, stopPrice: null, status: "new", submittedAt: new Date(Date.UTC(2026, 6, 22, 13, 40)) },
  { id: "o2", symbol: "NVDA", side: "sell" as const, qty: 4, type: "market", limitPrice: null, stopPrice: null, status: "new", submittedAt: new Date(Date.UTC(2026, 6, 22, 13, 42)) },
];

export default function OverviewPreview() {
  useEffect(() => {
    const p = adaptPortfolio(SUMMARY);
    const e = adaptEquity(BENCH);
    const rt = adaptRoundTableDecisions(DECISIONS);
    useStore.setState({
      positions: p.positions,
      currentEquity: p.currentEquity,
      cashEUR: p.cashEUR,
      accountCurrency: p.accountCurrency,
      paperTrading: false,
      equityCurve: e.equityCurve,
      benchmarkCurve: e.benchmarkCurve,
      lastEquity: e.lastEquity,
      twrPct: e.twrPct,
      netDeposits: e.netDeposits,
      roundTable: rt,
      marketOpen: true,
      brokerLabel: "Alpaca · Live",
      openOrders: ORDERS,
      lastSyncAt: Date.now() - 4_000,
    });
  }, []);

  return (
    <div className="aaa-console" style={{ minHeight: "100vh", background: "#0b0b0c", overflowY: "auto" }}>
      <Overview />
    </div>
  );
}
