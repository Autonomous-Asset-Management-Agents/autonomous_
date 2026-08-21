// #2845: the weekly view showed "+53 %" that was a DEPOSIT. The #2717 fixes made the KPIs
// deposit-neutral but never reached the chart: EquityChart's %-mode plotted raw rebased equity
// and its delta label was the raw endpoint difference. Under test:
//   1. twrSeries mirrors the engine's chain-link semantics (perf_metrics.py) — a deposit-only
//      move is 0 %, sub-periods with a non-positive base are skipped;
//   2. the chart renders cashflow MARKERS so an equity step is visibly a deposit, not growth;
//   3. source tripwires: %-mode values and the delta label come from twrSeries, and the raw
//      endpoint formula is gone.
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { twrSeries } from "@/console/shared/twr";
import { EquityChart } from "@/console/shared/EquityChart";

describe("twrSeries (#2845) — engine-equal chain-linking", () => {
  it("a pure deposit is 0 % growth (the +53 % bug)", () => {
    // 10k -> 15.3k, but 5.3k of it landed as a deposit that day: TWR must stay 0 %.
    const s = twrSeries([{ eur: 10_000 }, { eur: 15_300, cashflow: 5_300 }]);
    expect(s).toHaveLength(2);
    expect(s[1]).toBeCloseTo(0, 10);
  });

  it("chains market growth across a mid-series deposit", () => {
    // Day1: 10k -> 11k (+10 %). Day2: deposit 9k lands, equity 22k => market 13k/11k (+18.18 %).
    // Chain: 1.10 * (13/11) - 1 = 30 %.
    const s = twrSeries([
      { eur: 10_000 },
      { eur: 11_000 },
      { eur: 22_000, cashflow: 9_000 },
    ]);
    expect(s[1]).toBeCloseTo(10, 6);
    expect(s[2]).toBeCloseTo(30, 6);
  });

  it("skips sub-periods with a non-positive base (funding an empty account is not infinite gain)", () => {
    const s = twrSeries([{ eur: 0 }, { eur: 10_000, cashflow: 10_000 }, { eur: 10_500 }]);
    expect(s[1]).toBeCloseTo(0, 10); // skipped, chain unchanged
    expect(s[2]).toBeCloseTo(5, 6); // then a real +5 % market move
  });

  it("edge shapes: empty -> [], single point -> [0], missing cashflow counts as 0", () => {
    expect(twrSeries([])).toEqual([]);
    expect(twrSeries([{ eur: 5 }])).toEqual([0]);
    expect(twrSeries([{ eur: 100 }, { eur: 110 }])[1]).toBeCloseTo(10, 6);
  });
});

describe("EquityChart (#2845) — deposit-neutral chart", () => {
  const DEPOSIT_WEEK = [
    { t: new Date("2026-08-10T16:00:00Z"), eur: 10_000 },
    { t: new Date("2026-08-11T16:00:00Z"), eur: 15_300, cashflow: 5_300 },
    { t: new Date("2026-08-12T16:00:00Z"), eur: 15_300 },
  ];

  it("renders a cashflow marker at the deposit point (both modes)", () => {
    const a = render(<EquityChart data={DEPOSIT_WEEK} />);
    expect(a.container.querySelectorAll('[data-testid="cashflow-marker"]').length).toBe(1);
    a.unmount();
    const b = render(<EquityChart data={DEPOSIT_WEEK} percent />);
    expect(b.container.querySelectorAll('[data-testid="cashflow-marker"]').length).toBe(1);
  });

  it("renders no marker when there is no cashflow", () => {
    const { container } = render(
      <EquityChart data={[{ t: new Date(), eur: 100 }, { t: new Date(), eur: 110 }]} />
    );
    expect(container.querySelectorAll('[data-testid="cashflow-marker"]').length).toBe(0);
  });

  it("labels the marker with the signed cash-flow amount (deposit + / withdrawal −)", () => {
    const dep = render(<EquityChart data={DEPOSIT_WEEK} />);
    expect(dep.container.textContent).toContain("+€5,300");
    dep.unmount();
    const wd = render(
      <EquityChart
        data={[
          { t: new Date("2026-08-10T16:00:00Z"), eur: 10_000 },
          { t: new Date("2026-08-11T16:00:00Z"), eur: 9_600, cashflow: -400 },
          { t: new Date("2026-08-12T16:00:00Z"), eur: 9_600 },
        ]}
      />
    );
    expect(wd.container.textContent).toContain("−€400");
  });

  it("money mode draws a VERTICAL step at the deposit; %-mode stays deposit-neutral (no step)", () => {
    // deposit is at index 1 → x = (1/2)*1000 = 500; a vertical step = two vertices at x=500.
    const STEP = /L500\.00,[\d.]+ L500\.00,[\d.]+/;
    const money = render(<EquityChart data={DEPOSIT_WEEK} />);
    const hasStepMoney = [...money.container.querySelectorAll("path")].some((p) =>
      STEP.test(p.getAttribute("d") ?? "")
    );
    expect(hasStepMoney, "€-mode should jump vertically at a deposit, not slope diagonally").toBe(true);
    money.unmount();
    const pct = render(<EquityChart data={DEPOSIT_WEEK} percent />);
    const hasStepPct = [...pct.container.querySelectorAll("path")].some((p) =>
      STEP.test(p.getAttribute("d") ?? "")
    );
    expect(hasStepPct, "%-mode plots the deposit-neutral TWR — no vertical step").toBe(false);
  });

  it("source tripwire: %-mode series AND the delta label use twrSeries; the raw endpoint formula is gone", () => {
    const src = readFileSync(resolve(process.cwd(), "src/console/shared/EquityChart.tsx"), "utf8");
    expect(src).toContain("twrSeries(");
    expect(src, "the raw (last-first)/first delta must be gone — it counts deposits as return").not.toMatch(
      /data\[hi\]\.eur\s*-\s*data\[0\]\.eur/
    );
  });
});
