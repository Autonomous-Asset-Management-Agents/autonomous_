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
  dataStale: boolean; // REPORTS-UX ⑥ (#2710): any source's freshness is beyond the cutoff

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

  // ── Full auditable research note (RPT-GOLD, #1998) — all null unless the
  //    report generator flag is ON and the note was produced this cycle ──
  reportMarkdown: string | null;     // the deterministic 8-section auditable Markdown note
  reportAuditOk: boolean | null;     // mechanical AuditResult.integrity_ok
  reportAuditSummary: string | null; // e.g. "61 claims · 0 flags"
  // The note's OWN cross-sectional verdict — the source of truth for the reader
  // badge. `null` = the note ABSTAINED (no rank) -> show no pill. `undefined` = the
  // report generator is OFF (field absent) -> the badge falls back to `recommendation`.
  // See reportBadgeRecommendation() in console/live/specialist.ts.
  reportRecommendation?: "BUY" | "HOLD" | "SELL" | null;
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

  // ── Rich-synthesis card (Direction A) — all null/empty unless the card flag is
  //    ON. `groundingOk` is the card's presence signal (non-null ⇒ render the
  //    grounded card). Every displayed bull/bear/thesis claim is bound to a real
  //    headline via `synthesisCitations`; `synthesisDropped` is the transparent
  //    ledger of rejected (ungrounded/fabricated) claims. The vol band is a
  //    non-directional HAR-RV spread (base is always 0.0). ──
  groundingOk: boolean | null;
  synthesisCitations: SynthesisCitation[];
  synthesisDropped: SynthesisDropped[];
  volBandLowPct: number | null;
  volBandBasePct: number | null;
  volBandHighPct: number | null;
  volBandSigmaPct: number | null;
  // LSTM cross-section standing (the single rank definition the board votes on;
  // null triple = symbol absent from the panel → the card abstains, never guesses)
  lstmXsecPercentile: number | null;
  lstmXsecRank: number | null;
  lstmXsecUniverse: number | null;
  // Own-model price range (HAR-RV on last close) + hard price stats
  lastClose: number | null;
  priceBandLow: number | null;
  priceBandHigh: number | null;
  chg20dPct: number | null;
  dist52wHighPct: number | null;
  // TA reads (Group-B keys; null until the TA stage produced them this cycle)
  rsi14: number | null;
  macdSignal: string | null;
  scenarioBasis: string | null;

  updatedAt: Date | null;
}

/** One grounded claim, bound to the headline that supports it. */
export interface SynthesisCitation {
  kind: string; // "bull" | "bear" | "thesis"
  claim: string;
  headlineIndex: number;
  headlineText: string;
}

/** One rejected claim, with the mechanical reason it was dropped. */
export interface SynthesisDropped {
  kind: string;
  claim: string;
  reason: string;
}
