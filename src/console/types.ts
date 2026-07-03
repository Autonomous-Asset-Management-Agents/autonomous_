// Canonical console view-model types. These describe what the specialist-card
// components render — NOT the API wire shapes. The adapter layer
// (src/console/live/specialist.ts) maps the engine DTO into these.
//
// Scope note (G1b′): the engine's GET /specialist-reports endpoint carries the
// per-symbol research note + the symbol's own ML/TFT read. It does NOT carry
// price/position/sparkline data, nor a senate-vote breakdown (that lives in the
// separate /round-table-decisions endpoint, rendered by the Round-Table section
// of the Reports page). The bundle's senate/last/dayChange/position/avgEntry/
// unrealizedPct/sparkline/senateSummary fields are therefore intentionally NOT
// part of this type — the card renders gracefully without them.

// ML return scenario (base/bull/bear from the per-symbol TFT specialist).
export interface MlReturns {
  base: number | null; // %
  bull: number | null; // %
  bear: number | null; // %
}

export interface SpecialistReport {
  symbol: string;

  // ── Core verdict ──
  recommendation: "BUY" | "HOLD" | "SELL" | null;
  sentimentScore: number | null; // 0-10 (engine emits 0-100; adapter rescales)
  confidence: number | null;     // 0-1
  escalate: boolean;
  escalateReason: string | null;

  // ── Research-note prose ──
  companySummary: string | null;   // 2-3 sentence business overview (about field)
  investmentThesis: string | null; // full thesis prose
  bullCase: string | null;
  bearCase: string | null;
  newsSummary: string | null;
  headlines: string[];             // real recent news headlines
  reasons: string[];               // signal reasons
  edgeSignals: string[];           // edge_signals

  // ── ML / TFT model read ──
  mlDirection: "up" | "down" | "neutral" | "unavailable" | null;
  mlConfidence: number | null;
  mlReturns: MlReturns;
  signalQuality: string | null;     // "ml_plus_llm" | "llm_only" | …
  reportQuality: number | null;     // #1490: 0-100 deterministic quality score (null when flag OFF)
  reportQualityLabel: string | null; // #1490: "Strong" | "Fair" | "Thin"
  walkforwardIc: number | null;     // TFT walk-forward out-of-sample IC
  walkforwardSharpe: number | null; // TFT walk-forward out-of-sample Sharpe

  // ── Alternative-data signal counts ──
  shortInterestPct: number | null;
  insiderTradesCount: number | null;
  politicalTradesCount: number | null;
  materialEventsCount: number | null;
  redditMentions: number | null;

  // ── Decision-card fields: deterministic pros/cons + plain-language summary ──
  summary: string;
  pros: { text: string; value: string }[];
  cons: { text: string; value: string }[];

  updatedAt: Date | null;
}
