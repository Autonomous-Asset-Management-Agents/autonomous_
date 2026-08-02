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
  return c.map((p) => ({ t: p.t.toISOString(), eur: p.eur }));
}

function reviveCurve(raw: unknown): EquityCurvePoint[] {
  if (!Array.isArray(raw)) return [];
  const out: EquityCurvePoint[] = [];
  for (const r of raw) {
    if (!r || typeof r !== "object") continue;
    const t = new Date((r as RawPoint).t);
    const eur = (r as RawPoint).eur;
    if (!Number.isNaN(t.getTime()) && typeof eur === "number" && !Number.isNaN(eur)) {
      out.push({ t, eur });
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
    return {
      equityCurve: reviveCurve(obj.equityCurve),
      benchmarkCurve: reviveCurve(obj.benchmarkCurve),
      lastEquity: typeof obj.lastEquity === "number" ? obj.lastEquity : null,
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
