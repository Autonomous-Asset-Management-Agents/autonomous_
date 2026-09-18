/**
 * Console test data (UX E2E, #1050).
 *
 * One canonical, hand-tuned data set the journey suites (and the Playwright
 * browser layer) share, so a scenario reads the same data whether it is driven
 * through the React tree (vitest) or intercepted on the wire (Playwright
 * `page.route`). Shapes mirror the real engine DTOs in `src/lib/api.ts` exactly
 * — these are what `/portfolio-summary`, `/round-table-decisions` and
 * `/benchmark-equity` return — so the real adapters run unchanged in tests.
 *
 * Pure data only (no `vi`, no DOM) so it is import-safe from any runner.
 */
import type {
  PortfolioSummaryResponse,
  RoundTableDecisionsResponse,
  BenchmarkEquityResponse,
} from "@/lib/api";
import type {
  AlpacaValidateResult,
  OllamaProvisionResult,
} from "@/lib/desktopBridge";

// ── Operator / account identity ─────────────────────────────────────────────
export const operator = {
  name: "Georg",
  brokerName: "Alpaca Paper",
  accountTag: "PA3XYZ··· · paper",
} as const;

// ── /portfolio-summary ──────────────────────────────────────────────────────
// Three open positions: two winners, one loser. Derived fields (last, avgEntry,
// weight, cash) are computed by the real adapter from these snake_case inputs.
export const portfolioSummary: PortfolioSummaryResponse = {
  status: "success",
  equity: 105_000,
  last_equity: 104_200,
  total_unrealized_pnl: 94,
  positions: [
    { symbol: "AAPL", qty: 10, market_value: 2_000, unrealized_pnl: 150, unrealized_pnl_pct: 8.11, days_held: 12 },
    { symbol: "NVDA", qty: 5, market_value: 6_000, unrealized_pnl: -120, unrealized_pnl_pct: -1.96, days_held: 4 },
    { symbol: "MSFT", qty: 8, market_value: 3_200, unrealized_pnl: 64, unrealized_pnl_pct: 2.04, days_held: 20 },
  ],
};

/** Same endpoint, fresh paper account: connected but nothing open yet. */
export const portfolioEmpty: PortfolioSummaryResponse = {
  status: "success",
  equity: 100_000,
  last_equity: 100_000,
  total_unrealized_pnl: 0,
  positions: [],
};

// ── /round-table-decisions ──────────────────────────────────────────────────
// One decision per symbol = asdict(SenateSession); the adapter aggregates votes.
export const roundTableDecisions: RoundTableDecisionsResponse = {
  status: "success",
  total: 3,
  decisions: [
    {
      symbol: "AAPL",
      signal_action: "BUY",
      consensus_score: 0.72,
      gatekeeper_approved: true,
      gatekeeper_reason: "",
      timestamp: "2026-06-15T14:32:00Z",
      session_id: "sess-aapl-01",
      votes: [
        { name: "Momentum", agent_name: "momentum_specialist", score: 0.81, weight: 0.3, signal: "BUY", vetoed: false, reasoning: "Breakout on volume." },
        { name: "Value", agent_name: "value_specialist", score: 0.55, weight: 0.25, signal: "BUY", vetoed: false, reasoning: "Fair multiple." },
        { name: "Risk", agent_name: "risk_specialist", score: 0.40, weight: 0.2, signal: "HOLD", vetoed: false, reasoning: "Watch drawdown." },
      ],
    },
    {
      symbol: "TSLA",
      signal_action: "SELL",
      consensus_score: -0.41,
      gatekeeper_approved: true,
      gatekeeper_reason: "",
      timestamp: "2026-06-15T14:31:00Z",
      session_id: "sess-tsla-01",
      votes: [
        { name: "Momentum", agent_name: "momentum_specialist", score: -0.62, weight: 0.3, signal: "SELL", vetoed: false, reasoning: "Trend break." },
        { name: "Sentiment", agent_name: "sentiment_specialist", score: -0.30, weight: 0.2, signal: "SELL", vetoed: false, reasoning: "Negative news flow." },
      ],
    },
    {
      symbol: "NVDA",
      signal_action: "HOLD",
      consensus_score: 0.12,
      gatekeeper_approved: false,
      gatekeeper_reason: "Position size cap reached",
      timestamp: "2026-06-15T14:30:00Z",
      session_id: "sess-nvda-01",
      votes: [
        { name: "Momentum", agent_name: "momentum_specialist", score: 0.55, weight: 0.3, signal: "BUY", vetoed: false, reasoning: "Still strong." },
        { name: "Risk", agent_name: "risk_specialist", score: 0.05, weight: 0.2, signal: "HOLD", vetoed: true, reasoning: "Concentration veto." },
      ],
    },
  ],
};

export const roundTableEmpty: RoundTableDecisionsResponse = { status: "success", total: 0, decisions: [] };

// ── /benchmark-equity ───────────────────────────────────────────────────────
export const benchmarkEquity: BenchmarkEquityResponse = {
  points: [
    { date: "2026-06-09", equity: 100_000 },
    { date: "2026-06-10", equity: 100_800 },
    { date: "2026-06-11", equity: 101_400 },
    { date: "2026-06-12", equity: 103_100 },
    { date: "2026-06-13", equity: 104_200 },
    { date: "2026-06-14", equity: 105_000 },
  ],
  spy_points: [
    { date: "2026-06-09", equity: 100_000 },
    { date: "2026-06-10", equity: 100_300 },
    { date: "2026-06-11", equity: 100_100 },
    { date: "2026-06-12", equity: 100_900 },
    { date: "2026-06-13", equity: 101_200 },
    { date: "2026-06-14", equity: 101_500 },
  ],
  initial_capital: 100_000,
  final_equity: 105_000,
  strategy: "RLAgent",
};

// ── /chat (support) ─────────────────────────────────────────────────────────
export const chat = {
  question: "is the market open?",
  reply: "The US market is open — regular session until 22:00 CET.",
  followUp: "how many positions do I hold?",
  followUpReply: "You hold 3 open positions: AAPL, NVDA and MSFT.",
} as const;

// ── Onboarding bridge results ───────────────────────────────────────────────
export const alpacaValid: AlpacaValidateResult = { ok: true, status: 200 };
export const alpacaRejected: AlpacaValidateResult = { ok: false, status: 403 };
export const alpacaUnreachable: AlpacaValidateResult = { ok: false, status: 0 };

export const ollamaSuccess: OllamaProvisionResult = { ok: true, model: "llama3.2", baseUrl: "http://127.0.0.1:11434" };
export const ollamaNeedsManual: OllamaProvisionResult = { ok: false, needsManual: true, error: "Install Ollama from ollama.com, then retry." };
export const ollamaFailed: OllamaProvisionResult = { ok: false, error: "Local AI setup failed." };

export const sampleKeys = {
  alpacaKeyId: "PKTEST1234567890ABCD",
  alpacaSecret: "verysecret-paper-key-do-not-use-in-prod",
  geminiKey: "AIzaTESTexampleexampleexampleexample",
} as const;

// ── Engine lifecycle ────────────────────────────────────────────────────────
export const engineLogs: string[] = [
  "[boot] AAAgents engine starting…",
  "[db] local SQLite ready (data/aaagents.db)",
  "[redis] disabled (desktop) — in-memory state client",
  "[engine] specialists loaded · paper-trading mode",
  "[engine] ready — listening on 127.0.0.1",
];
