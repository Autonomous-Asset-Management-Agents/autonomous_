// #3145 bug 2 (02.09.): the Overview shows the same return twice — the "autonomous_" KPI card
// takes it from the ENGINE (perf_metrics.compute_cashflow_adjusted_returns), the hero and the
// chart compute it in the CONSOLE (shared/twr.ts chainLink). Andre saw −0.45 % on the card next
// to a positive hero. Two implementations of one number can only stay equal if a shared vector
// pins them: this file asserts the console half, ai_trading_bot/tests/unit/
// test_baseline_is_time_zero_3145.py the engine half, both against the SAME json.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { chainLink } from "@/console/shared/twr";

const fx = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "ai_trading_bot/tests/fixtures/twr_parity_3145.json"),
    "utf8",
  ),
);

describe("engine ⇄ console parity (#3145)", () => {
  // The console chain-links the flows it RECEIVES, and the engine serves the equity-effective
  // ones (api_routes._return_metrics_from_points writes them onto the points). Chaining the
  // gross amounts instead lands 0.0056 pp away — that gap was bug 2.
  const points = fx.equity.map((eur: number, i: number) => ({
    eur,
    cashflow: fx.effective_cashflows[i],
  }));

  it("the console's chain-link returns the engine's number for the shared vector", () => {
    const { pctSeries } = chainLink(points);
    expect(pctSeries[pctSeries.length - 1]).toBeCloseTo(fx.expected_twr_pct, 9);
  });

  it("the console's market P&L equals the engine's strategy P&L", () => {
    const { marketPnl } = chainLink(points);
    expect(marketPnl).toBeCloseTo(fx.expected_market_pnl_engine, 6);
  });

  it("chaining the GROSS flows instead would reintroduce the drift (guard on the vector)", () => {
    const gross = fx.equity.map((eur: number, i: number) => ({ eur, cashflow: fx.cashflows[i] }));
    const s = chainLink(gross).pctSeries;
    expect(Math.abs(s[s.length - 1] - fx.expected_twr_pct)).toBeGreaterThan(1e-6);
  });

  it("money and percent agree in sign — checkable by hand from the vector", () => {
    const { pctSeries, marketPnl } = chainLink(points);
    expect(Math.sign(marketPnl)).toBe(Math.sign(pctSeries[pctSeries.length - 1]));
    // 11,812.43 − 6,142.65 − 5,785.60 = −115.82 raw; the engine reclassifies a 0.35 transfer
    // fee as a funding cost rather than strategy loss, so the shown P&L is −115.47.
    expect(marketPnl).toBeCloseTo(-115.47, 6);
  });
});
