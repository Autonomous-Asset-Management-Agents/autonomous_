import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";

/**
 * #4513 — the KPI band is three comparison tiles (Performance · Max drawdown · Sharpe), each with
 * "Your portfolio" left and "S&P 500" right, in the existing StatTile design. Performance comes
 * from the equity curve (deposit-neutral), never from the engine's twr_pct (#3911).
 */
import { KpiBand } from "../console/shared/KpiBand";
import { Overview } from "../console/desktop/pages/Overview";
import { useStore } from "../console/store/useStore";
import { computeMaxDrawdown } from "../console/live/drawdown";
import { computeSharpe } from "../console/live/metrics";

vi.mock("../lib/api", () => ({
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  fetchPortfolioSummary: vi.fn().mockResolvedValue(null),
  fetchBenchmarkEquity: vi.fn().mockResolvedValue(null),
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
  fetchPortfolioIntraday: vi.fn().mockResolvedValue([]),
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchHealth: vi.fn().mockResolvedValue(null),
  fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
  getHitlPolicy: vi.fn().mockResolvedValue({ HITL_AUTONOMOUS_UNLIMITED: true }),
}));
vi.mock("../console/live/useOpenOrdersPolling", () => ({ useOpenOrdersPolling: () => {} }));

const PAIRS = {
  performance: { portfolio: 42.09, benchmark: 12.84 },
  maxDrawdown: { portfolio: -11.07, benchmark: -8.52 },
  sharpe: { portfolio: 2.37, benchmark: 1.46 },
};

const tileOf = (title: string) => {
  const eyebrow = screen.getByText(title);
  return eyebrow.parentElement as HTMLElement;
};

describe("#4513 KpiBand — three comparison tiles", () => {
  it("renders exactly three tiles: Performance, Max drawdown, Sharpe (annualized)", () => {
    const { container } = render(
      <KpiBand {...PAIRS} sinceHint="since 20 Feb 2026" sharpeHint="realised curve" />,
    );
    const grid = container.firstElementChild as HTMLElement;
    expect(grid.children.length).toBe(3);
    expect(grid.className).toContain("lg:grid-cols-3");
    expect(screen.getByText("Performance")).toBeTruthy();
    expect(screen.getByText("Max drawdown")).toBeTruthy();
    expect(screen.getByText("Sharpe (annualized)")).toBeTruthy();
  });

  it("shows 'Your portfolio' left and 'S&P 500' right in every tile, as sublabels", () => {
    render(<KpiBand {...PAIRS} sinceHint="since 20 Feb 2026" sharpeHint="realised curve" />);
    for (const title of ["Performance", "Max drawdown", "Sharpe (annualized)"]) {
      const tile = within(tileOf(title));
      const left = tile.getByText("Your portfolio");
      const right = tile.getByText("S&P 500");
      expect(left.className).toContain("sublabel");
      expect(right.className).toContain("sublabel");
      // left precedes right in document order
      expect(left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("formats and tones both sides with the existing num/bull/bear classes", () => {
    render(<KpiBand {...PAIRS} sinceHint="since 20 Feb 2026" sharpeHint="realised curve" />);
    const perf = within(tileOf("Performance"));
    expect(perf.getByText("+42.09%").className).toContain("text-bull");
    expect(perf.getByText("+12.84%").className).toContain("text-bull");
    const dd = within(tileOf("Max drawdown"));
    expect(dd.getByText("−11.07%").className).toContain("text-bear");
    expect(dd.getByText("−8.52%").className).toContain("text-bear");
    const sh = within(tileOf("Sharpe (annualized)"));
    expect(sh.getByText("2.37")).toBeTruthy();
    expect(sh.getByText("1.46")).toBeTruthy();
  });

  it("renders every tile through StatTile (surface-flat, eyebrow, num)", () => {
    const { container } = render(
      <KpiBand {...PAIRS} sinceHint="since 20 Feb 2026" sharpeHint="realised curve" />,
    );
    const tiles = Array.from((container.firstElementChild as HTMLElement).children);
    for (const t of tiles) {
      expect(t.className).toContain("surface-flat");
      expect(t.querySelector(".eyebrow")).toBeTruthy();
      expect(t.querySelector(".num")).toBeTruthy();
    }
  });

  it("dashes missing values per side (live history building: Sharpe left —, right S&P value)", () => {
    render(
      <KpiBand
        performance={{ portfolio: null, benchmark: null }}
        maxDrawdown={{ portfolio: 0, benchmark: null }}
        sharpe={{ portfolio: null, benchmark: 1.46 }}
        sinceHint="since inception"
        sharpeHint="after ~20 live days"
      />,
    );
    const sh = within(tileOf("Sharpe (annualized)"));
    expect(sh.getByText("—")).toBeTruthy();
    expect(sh.getByText("1.46")).toBeTruthy();
    expect(screen.getAllByText("—").length).toBe(5);
    expect(screen.getByText("after ~20 live days")).toBeTruthy();
  });

  it("uses the brandLabel prop for the left side (live demo: autonomous_)", () => {
    render(
      <KpiBand
        {...PAIRS}
        sinceHint="since 20 Feb 2026"
        sharpeHint="realised curve"
        brandLabel="autonomous_"
      />,
    );
    expect(screen.getAllByText("autonomous_").length).toBe(3);
    expect(screen.queryByText("Your portfolio")).toBeNull();
  });
});

// ── Overview wiring ─────────────────────────────────────────────────────────
const day = (i: number) => new Date(Date.UTC(2026, 1, 20 + i, 21));

// Portfolio 100,000 → 142,086 (no deposits) with one dip; S&P rises with its own dip.
const PORTFOLIO = Array.from({ length: 30 }, (_, i) => {
  const eur = i === 29 ? 142_086 : 100_000 + i * 1_500 - (i === 10 ? 9_000 : 0);
  return { t: day(i), eur, cashflow: 0 };
});
const BENCH = Array.from({ length: 30 }, (_, i) => ({
  t: day(i),
  eur: 100_000 + i * 400 - (i === 15 ? 6_000 : 0),
}));

describe("#4513 Overview — KPI band wiring", () => {
  beforeEach(() => {
    useStore.setState({
      positions: [],
      cashEUR: null,
      currentEquity: 142_086,
      lastEquity: null,
      equityCurve: PORTFOLIO,
      benchmarkCurve: BENCH,
      roundTable: [],
      openOrders: [],
      lastSyncAt: null,
      paperTrading: true,
      strategyRunning: null,
      systemHalted: false,
      baselineDate: null,
      // #3911: the engine's frozen twr_pct — must NOT reach the tile.
      twrPct: -1.32,
    });
  });

  it("Performance left is the curve return (+42.09%), not twr_pct (−1.32%)", () => {
    render(<Overview />);
    const perf = within(tileOf("Performance"));
    expect(perf.getByText("+42.09%")).toBeTruthy();
    expect(perf.queryByText("−1.32%")).toBeNull();
    expect(perf.getByText("+11.60%")).toBeTruthy(); // S&P 100,000 → 111,600
  });

  it("Max drawdown and Sharpe use the same function over the same window on both sides", () => {
    render(<Overview />);
    const dd = within(tileOf("Max drawdown"));
    const ddP = computeMaxDrawdown(PORTFOLIO);
    const ddB = computeMaxDrawdown(BENCH);
    expect(dd.getByText(`−${Math.abs(ddP).toFixed(2)}%`)).toBeTruthy();
    expect(dd.getByText(`−${Math.abs(ddB).toFixed(2)}%`)).toBeTruthy();
    const sh = within(tileOf("Sharpe (annualized)"));
    expect(sh.getByText((computeSharpe(PORTFOLIO) as number).toFixed(2))).toBeTruthy();
    expect(sh.getByText((computeSharpe(BENCH) as number).toFixed(2))).toBeTruthy();
    expect(within(tileOf("Performance")).getByText("Your portfolio")).toBeTruthy();
  });

  it("live with < ~20 live days: Sharpe left '—', right the S&P value", () => {
    useStore.setState({
      paperTrading: false,
      equityCurve: PORTFOLIO.slice(0, 5),
      benchmarkCurve: BENCH,
    });
    render(<Overview />);
    const sh = within(tileOf("Sharpe (annualized)"));
    expect(sh.getByText("—")).toBeTruthy();
    expect(sh.getByText((computeSharpe(BENCH) as number).toFixed(2))).toBeTruthy();
  });
});
