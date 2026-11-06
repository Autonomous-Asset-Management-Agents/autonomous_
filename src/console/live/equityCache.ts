// src/console/live/equityCache.ts — PR-A Instant-Data.
// Persists the last equity/benchmark + intraday curves to localStorage so the
// Overview chart paints a real line INSTANTLY on mount (stale-while-revalidate),
// instead of the blank "Collecting equity history…" gap while the (event-loop-
// starved, #2497) /benchmark-equity + /portfolio-intraday polls resolve.
//
// Pure + fail-soft by design: every read returns null/[] on absent/malformed/
// wrong-version data and NEVER throws; every write is best-effort (quota errors
// swallowed). The cache is a display accelerator only — the live polls always
// overwrite it, so a stale cache can never mislead beyond the first repaint.

import type { EquityView, EquityCurvePoint } from "./equity";
import type { EquityRange } from "./equityRange";

const SCHEMA = 1;
const EQUITY_KEY = "aaa.console.equityCache.v1";
const INTRADAY_PREFIX = "aaa.console.intradayCache.v1.";
// Cap so a pathological curve can't bloat localStorage; keep the most recent.
const MAX_POINTS = 2000;

interface RawPoint {
  t: string;
  eur: number;
  cashflow?: number;
}

function ls(): Storage | null {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

function serializeCurve(curve: EquityCurvePoint[]): RawPoint[] {
  const c = curve.length > MAX_POINTS ? curve.slice(-MAX_POINTS) : curve;
  return c.map((p) => ({ t: p.t.toISOString(), eur: p.eur, cashflow: p.cashflow ?? 0 }));
}

function reviveCurve(raw: unknown): EquityCurvePoint[] {
  if (!Array.isArray(raw)) return [];
  const out: EquityCurvePoint[] = [];
  for (const r of raw) {
    if (!r || typeof r !== "object") continue;
    const t = new Date((r as RawPoint).t);
    const eur = (r as RawPoint).eur;
    const cf = (r as RawPoint).cashflow;
    if (!Number.isNaN(t.getTime()) && typeof eur === "number" && !Number.isNaN(eur)) {
      out.push({ t, eur, cashflow: typeof cf === "number" ? cf : 0 });
    }
  }
  return out;
}

export function writeEquityCache(view: EquityView): void {
  const store = ls();
  if (!store) return;
  try {
    store.setItem(
      EQUITY_KEY,
      JSON.stringify({
        v: SCHEMA,
        equityCurve: serializeCurve(view.equityCurve),
        benchmarkCurve: serializeCurve(view.benchmarkCurve),
        lastEquity: view.lastEquity,
        // #1957: persist the deposit-neutral KPIs so the instant-paint shows the correct
        // "since inception" return before the first poll resolves (not a deposit-inflated one).
        twrPct: view.twrPct,
        netDeposits: view.netDeposits,
        pnlNetOfDeposits: view.pnlNetOfDeposits,
        // #3071: persist the baseline labelling so the instant-paint hint is honest too.
        baselineDate: view.baselineDate,
        inceptionDate: view.inceptionDate,
      }),
    );
  } catch {
    /* quota / serialization — cache is best-effort, never fatal */
  }
}

export function readEquityCache(): EquityView | null {
  const store = ls();
  if (!store) return null;
  try {
    const rawStr = store.getItem(EQUITY_KEY);
    if (!rawStr) return null;
    const obj = JSON.parse(rawStr);
    if (!obj || obj.v !== SCHEMA) return null;
    const num = (v: unknown): number | null =>
      typeof v === "number" && Number.isFinite(v) ? v : null;
    return {
      equityCurve: reviveCurve(obj.equityCurve),
      benchmarkCurve: reviveCurve(obj.benchmarkCurve),
      lastEquity: typeof obj.lastEquity === "number" ? obj.lastEquity : null,
      twrPct: num(obj.twrPct),
      netDeposits: num(obj.netDeposits),
      pnlNetOfDeposits: num(obj.pnlNetOfDeposits),
      baselineDate: typeof obj.baselineDate === "string" ? obj.baselineDate : null,
      inceptionDate: typeof obj.inceptionDate === "string" ? obj.inceptionDate : null,
    };
  } catch {
    return null;
  }
}

export function writeIntradayCache(range: EquityRange, curve: EquityCurvePoint[]): void {
  const store = ls();
  if (!store) return;
  try {
    store.setItem(
      INTRADAY_PREFIX + range,
      JSON.stringify({ v: SCHEMA, curve: serializeCurve(curve) }),
    );
  } catch {
    /* best-effort */
  }
}

/**
 * Drop EVERY persisted equity + intraday curve. Called on a Paper↔Live switch (from
 * resetEquityCurves) so the OLD account's cached curve can never be repainted on the next
 * Overview mount / renderer reload. Without this, resetEquityCurves cleared only the in-memory
 * store while the localStorage cache lingered — so the paper ~100K baseline flashed a ~-99% fake
 * loss against the small live equity. Fail-soft: only removes our own keys, never throws.
 */
export function clearEquityCaches(): void {
  const store = ls();
  if (!store) return;
  try {
    store.removeItem(EQUITY_KEY);
    // Intraday caches are one key per range under INTRADAY_PREFIX — collect then remove them all
    // (collect first so removing while iterating store.key(i) can't skip entries).
    const stale: string[] = [];
    for (let i = 0; i < store.length; i++) {
      const k = store.key(i);
      if (k && k.startsWith(INTRADAY_PREFIX)) stale.push(k);
    }
    for (const k of stale) store.removeItem(k);
  } catch {
    /* best-effort — a failed clear just means the stale cache lingers one paint */
  }
}

export function readIntradayCache(range: EquityRange): EquityCurvePoint[] {
  const store = ls();
  if (!store) return [];
  try {
    const rawStr = store.getItem(INTRADAY_PREFIX + range);
    if (!rawStr) return [];
    const obj = JSON.parse(rawStr);
    if (!obj || obj.v !== SCHEMA) return [];
    return reviveCurve(obj.curve);
  } catch {
    return [];
  }
}
