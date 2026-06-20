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

    updatedAt: r.updated_at ? new Date(r.updated_at) : null,
  };
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
