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

  it("#3054-fix: a settlement-lagged deposit stays bounded (the −27,833% week bug)", () => {
    // The deposit is dated on the PRE-jump point (349.94); equity only reflects it on the next
    // point (6142.36). Without the engine's carry-forward guard, eur−flow = 349.94−5833 goes
    // hugely negative and the chained TWR explodes. Guarded: the flow defers to the jump.
    const s = twrSeries([
      { eur: 300 },
      { eur: 349.94, cashflow: 5833 },
      { eur: 6142.36 },
    ]);
    expect(Math.abs(s[2])).toBeLessThan(100);
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

  it("#3068: suppresses sub-$1 cashflow markers (no more '-$0' clutter)", () => {
    const { container } = render(
      <EquityChart
        data={[
          { t: new Date("2026-08-10T16:00:00Z"), eur: 10_000 },
          { t: new Date("2026-08-11T16:00:00Z"), eur: 10_000.4, cashflow: 0.4 },
          { t: new Date("2026-08-12T16:00:00Z"), eur: 10_001 },
        ]}
      />
    );
    expect(container.querySelectorAll('[data-testid="cashflow-marker"]').length).toBe(0);
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

  it("rc3 R3: an over-stacked cashflow (flow > actual step) draws NO step vertex — no 350$ down-spike", () => {
    // Real 28.08. shape: prev 6,143 → 11,928 (+5,786 step) but the point carries a
    // doubled cf of 11,578. The old pre-step vertex eur−cf = 350 painted a cliff to the
    // chart floor. Inconsistent flow ⇒ plain line (marker still shown).
    const data = [
      { t: new Date("2026-08-26T16:00:00Z"), eur: 6143 },
      { t: new Date("2026-08-27T16:00:00Z"), eur: 11928, cashflow: 11578 },
    ];
    const { container } = render(<EquityChart data={data} />);
    const STEP = /L1000\.00,[\d.]+ L1000\.00,[\d.]+/; // double vertex at the point's x
    const hasStep = [...container.querySelectorAll("path")].some((p) =>
      STEP.test(p.getAttribute("d") ?? "")
    );
    expect(hasStep, "over-stacked flow must not draw the step cliff").toBe(false);
  });

  it("rc3 QA: the REAL settled-deposit day renders one truthful step + matching label", () => {
    // Post-R1 data shape (28.08.): flat ~6,143 then the settle point 11,928.25 carrying its
    // OWN flow (5,785.60). The rendered chart must show: exactly one marker labelled with
    // the deposit amount, and a vertical step whose lower vertex is the PRE-deposit level
    // (~6,142.65) — never below the previous point (the $350 cliff), never a stacked label.
    const base = Array.from({ length: 8 }, (_, i) => ({
      t: new Date(Date.UTC(2026, 7, 27, 13, i * 30)),
      eur: 6143 + i * 0.1,
    }));
    const data = [...base, {
      t: new Date(Date.UTC(2026, 7, 27, 18, 0)),
      eur: 11928.25,
      cashflow: 5785.6,
    }];
    const { container } = render(<EquityChart data={data} currency="$" />);
    expect(container.querySelectorAll('[data-testid="cashflow-marker"]').length).toBe(1);
    expect(container.textContent).toContain("+$5,786"); // label == deposit == step height
    expect(container.textContent).not.toContain("11,578"); // never the stacked sum
    // the step's lower vertex (eur − cf = 6,142.65) must sit ABOVE the curve's minimum —
    // i.e. no vertex y beyond the flat line's band (y of 6,143 ≈ y of 6,142.65).
    const stepPath = [...container.querySelectorAll("path")]
      .map((p) => p.getAttribute("d") ?? "")
      .find((d) => /L1000\.00,[\d.]+ L1000\.00,[\d.]+/.test(d));
    expect(stepPath, "the settle point must render as a vertical step").toBeTruthy();
    const ys = [...stepPath!.matchAll(/L1000\.00,([\d.]+)/g)].map((m) => parseFloat(m[1]));
    // viewBox h=100: flat band lies near the bottom (~y 94); the pre-step vertex must be
    // within that band (not below y=100 territory the $350 cliff produced).
    expect(Math.max(...ys)).toBeLessThan(100);
    expect(Math.abs(Math.max(...ys) - 94)).toBeLessThan(4);
  });

  it("source tripwire: %-mode series AND the delta label use twrSeries; the raw endpoint formula is gone", () => {
    const src = readFileSync(resolve(process.cwd(), "src/console/shared/EquityChart.tsx"), "utf8");
    expect(src).toContain("twrSeries(");
    expect(src, "the raw (last-first)/first delta must be gone — it counts deposits as return").not.toMatch(
      /data\[hi\]\.eur\s*-\s*data\[0\]\.eur/
    );
  });
});
