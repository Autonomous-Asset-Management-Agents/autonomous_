import type { SpecialistReportDTO, SpecialistReportsResponse } from "@/lib/api";
import type { SpecialistReport, MlReturns } from "@/console/types";

/**
 * Specialist-report adapter (G1b′ / RPAR-#1284). Maps the engine's
 * GET /specialist-reports DTO (snake_case) to the console's camelCase
 * SpecialistReport view-model.
 *
 * Replicates the production bundle's `adaptSpecialistReports` conversions
 * exactly:
 *   - sentiment_score is a 0-100 engine scale → divide by 10 for the 0-10
 *     view-model the SentimentBar / "/10" display expect (fixes the historical
 *     "54.1 / 10" bug).
 *   - recommendation is lowercase from the engine ("buy"/"hold"/"sell") → coerce
 *     to UPPERCASE BUY/HOLD/SELL; unrecognised → null.
 *   - pros/cons are coerced to `{text, value}[]`, tolerating either the
 *     structured engine shape or a bare string array.
 *   - null-safety NEVER `or`-masks a legitimate 0: `?? null` / `?? default`
 *     is used throughout, never `|| null`, so a real 0.0 (e.g. a max-bearish
 *     sentiment, a 0 confidence, a 0% short interest) survives.
 *
 * Note: this endpoint carries no senate / price / position / sparkline data, so
 * those bundle fields are not produced here (see console/types.ts scope note).
 */

/** Coerce a recommendation string into the view union. null when unrecognised. */
function coerceRecommendation(raw: string | undefined | null): "BUY" | "HOLD" | "SELL" | null {
  if (!raw) return null;
  const upper = raw.toUpperCase();
  if (upper === "BUY" || upper === "HOLD" || upper === "SELL") return upper;
  return null;
}

/** Coerce an ml_direction string into the view union. */
function coerceMlDirection(
  raw: string | undefined | null,
): "up" | "down" | "neutral" | "unavailable" | null {
  if (!raw) return null;
  // "neutral" is a REAL model verdict (the TFT ran and predicted a flat move) —
  // distinct from "unavailable" (no served model). It must survive; only
  // "unavailable"/null mean "no ML".
  if (raw === "up" || raw === "down" || raw === "neutral" || raw === "unavailable") return raw;
  return null;
}

/**
 * Coerce the engine's pros/cons into `{text, value}[]`.
 * Accepts the structured `{text, value}` shape (passed through), or a bare
 * string array (mapped to `{text, value:""}`). Anything else → [].
 */
function coercePoints(
  raw: { text: string; value: string }[] | string[] | undefined | null,
): { text: string; value: string }[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((p) => {
      if (typeof p === "string") return { text: p, value: "" };
      if (p && typeof p === "object" && typeof p.text === "string") {
        return { text: p.text, value: typeof p.value === "string" ? p.value : "" };
      }
      return null;
    })
    .filter((p): p is { text: string; value: string } => p !== null);
}

/** Trim a prose value to null when empty/whitespace; never masks real content. */
function prose(s: string | undefined | null): string | null {
  if (!s) return null;
  const t = s.trim();
  return t ? t : null;
}

function adaptOne(r: SpecialistReportDTO): SpecialistReport {
  const mlReturns: MlReturns = {
    base: r.ml_base_return_pct ?? null,
    bull: r.ml_bull_return_pct ?? null,
    bear: r.ml_bear_return_pct ?? null,
  };

  return {
    symbol: r.symbol,

    recommendation: coerceRecommendation(r.recommendation),
    // Engine emits sentiment on a 0-100 scale; the view-model + card are 0-10.
    // `?? null` (not `|| null`) so a legitimate 0.0 (max-bearish) survives.
    sentimentScore: r.sentiment_score != null ? r.sentiment_score / 10 : null,
    confidence: r.confidence ?? null,
    escalate: r.escalate ?? false,
    escalateReason: prose(r.escalate_reason),

    companySummary: prose(r.about) ?? prose(r.company_summary),
    investmentThesis: prose(r.investment_thesis),
    bullCase: prose(r.bull_case),
    bearCase: prose(r.bear_case),
    newsSummary: prose(r.news_summary),
    headlines: Array.isArray(r.headlines) ? r.headlines : [],
    reasons: Array.isArray(r.reasons) ? r.reasons : [],
    edgeSignals: Array.isArray(r.edge_signals) ? r.edge_signals : [],

    mlDirection: coerceMlDirection(r.ml_direction),
    mlConfidence: r.ml_confidence ?? null,
    mlReturns,
    signalQuality: prose(r.signal_quality),
    reportQuality: r.report_quality ?? null,
    reportQualityLabel: r.report_quality_label ?? null,
    // RPT-GOLD (#1998): the full auditable note is raw Markdown — keep it verbatim
    // (NOT through `prose`, which is for short single-paragraph fields).
    reportMarkdown: typeof r.report_markdown === "string" ? r.report_markdown : null,
    reportAuditOk: typeof r.report_audit_ok === "boolean" ? r.report_audit_ok : null,
    reportAuditSummary: typeof r.report_audit_summary === "string" ? r.report_audit_summary : null,
    // The note's own verdict. Preserve the undefined-vs-null distinction: `undefined`
    // = the report generator is OFF (field absent), `null` = the note ABSTAINED. Both
    // pass coerceRecommendation, but only `null` must survive as "abstained" — so we
    // gate on `=== undefined` BEFORE coercing, never `?? null` (which would collapse
    // the two and reintroduce the fabricated badge).
    reportRecommendation:
      r.report_recommendation === undefined ? undefined : coerceRecommendation(r.report_recommendation),
    walkforwardIc: r.walkforward_ic ?? null,
    walkforwardSharpe: r.walkforward_sharpe ?? null,

    shortInterestPct: r.short_interest_pct ?? null,
    insiderTradesCount: r.insider_trades_count ?? null,
    politicalTradesCount: r.political_trades_count ?? null,
    materialEventsCount: r.material_events_count ?? null,
    redditMentions: r.reddit_mentions ?? null,

    summary: r.summary ?? "",
    pros: coercePoints(r.pros),
    cons: coercePoints(r.cons),

    // Rich-synthesis card (Direction A). `groundingOk` is the presence signal:
    // `undefined` (card flag OFF) → null; a real boolean survives (incl. false).
    // `?? null` throughout, never `||` — a legitimate 0.0 vol-band value survives.
    groundingOk: typeof r.grounding_ok === "boolean" ? r.grounding_ok : null,
    synthesisCitations: Array.isArray(r.synthesis_citations)
      ? r.synthesis_citations.map((c) => ({
          kind: c.kind,
          claim: c.claim,
          headlineIndex: c.headline_index,
          headlineText: c.headline_text,
        }))
      : [],
    synthesisDropped: Array.isArray(r.synthesis_dropped)
      ? r.synthesis_dropped.map((d) => ({ kind: d.kind, claim: d.claim, reason: d.reason }))
      : [],
    volBandLowPct: r.vol_band_low_pct ?? null,
    volBandBasePct: r.vol_band_base_pct ?? null,
    volBandHighPct: r.vol_band_high_pct ?? null,
    volBandSigmaPct: r.vol_band_sigma_pct ?? null,
    scenarioBasis: prose(r.scenario_basis),
    lstmXsecPercentile: r.lstm_xsec_percentile ?? null,
    lstmXsecRank: r.lstm_xsec_rank ?? null,
    lstmXsecUniverse: r.lstm_xsec_universe ?? null,
    lastClose: r.last_close ?? null,
    priceBandLow: r.price_band_low ?? null,
    priceBandHigh: r.price_band_high ?? null,
    chg20dPct: r.chg_20d_pct ?? null,
    dist52wHighPct: r.dist_52w_high_pct ?? null,
    rsi14: r.rsi_14 ?? null,
    macdSignal: typeof r.macd_signal === "string" ? r.macd_signal : null,

    updatedAt: r.updated_at ? new Date(r.updated_at) : null,
  };
}

/**
 * The recommendation the RESEARCH READER badge (RecPill) must show.
 *
 * The note's OWN cross-sectional verdict (`reportRecommendation`) is the source of
 * truth: BUY/HOLD/SELL when the report generator produced this note, or `null` when
 * the note ABSTAINED (no rank) — in which case the badge shows nothing, NEVER the
 * uncomputed `recommendation` default "hold" (the report-only whole-universe path
 * never computes `recommendation`, so it stays "hold" and would fabricate a HALTEN
 * over an abstaining note). Only when the report generator is OFF (the field is
 * absent -> `undefined`) does the badge fall back to the specialist's own
 * `recommendation` — the legacy structured-fields reader, where no rank-based
 * verdict exists.
 */
export function reportBadgeRecommendation(
  r: SpecialistReport,
): "BUY" | "HOLD" | "SELL" | null {
  return r.reportRecommendation !== undefined ? r.reportRecommendation : r.recommendation;
}

/**
 * Map a GET /specialist-reports response to SpecialistReport[].
 * Returns [] for null/undefined input, a non-"ok" status (e.g. the registry-off
 * "unavailable" contract), or an empty/malformed reports array.
 */
export function adaptSpecialistReports(
  resp: SpecialistReportsResponse | null | undefined,
): SpecialistReport[] {
  if (!resp || resp.status !== "ok" || !Array.isArray(resp.reports) || resp.reports.length === 0) {
    return [];
  }
  return resp.reports.map(adaptOne);
}
