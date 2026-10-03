import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

/**
 * #3321 / C1 — the Balances/KPI bands and the Top-positions table are extracted from Overview.tsx
 * into shared, prop-driven components so the console dashboard and the public live-demo render the
 * IDENTICAL surface. These pin the extracted structure/values and the behavioural seams
 * (renderSymbol / onViewAll) that keep the demo read-only.
 */
import { StatTile } from "../console/shared/StatTile";
import { BalancesBand } from "../console/shared/BalancesBand";
import { KpiBand } from "../console/shared/KpiBand";
import { PositionsTable } from "../console/shared/PositionsTable";

const money = (
  n: number,
  opts?: { compact?: boolean; sign?: boolean },
): string => {
  const sign = opts?.sign && n >= 0 ? "+" : "";
  const body = opts?.compact ? `${(n / 1000).toFixed(1)}k` : n.toFixed(2);
  return `${sign}$${body}`;
};

describe("StatTile", () => {
  it("renders label, value and hint; tone colors the value; labelLower opts out of uppercase", () => {
    render(
      <StatTile
        label="Cash"
        value="$1.0k"
        hint="settled"
        tone="text-bull"
        labelLower
        baseline
      />,
    );
    expect(screen.getByText("Cash")).toBeTruthy();
    const val = screen.getByText("$1.0k");
    expect(val.className).toContain("text-bull");
    expect(val.className).toContain("flex items-baseline"); // baseline
    expect(screen.getByText("settled")).toBeTruthy();
  });

  it("defaults the value color to white and no baseline flex when omitted", () => {
    render(<StatTile label="Invested" value="$2.0k" hint="x" />);
    const val = screen.getByText("$2.0k");
    expect(val.className).toContain("text-white/92");
    expect(val.className).not.toContain("flex items-baseline");
  });
});

describe("BalancesBand", () => {
  it("renders the three liquidity tiles with formatted values and hints", () => {
    const { container } = render(
      <BalancesBand
        cashEUR={43245}
        currentEquity={144361}
        investedEUR={101116}
        investedPct={70.1}
        positionsCount={10}
        netDeposits={44000}
        startEquity={100000}
        iso="USD"
        money={money}
      />,
    );
    expect(screen.getByText("Cash / buying power")).toBeTruthy();
    expect(screen.getByText("Invested")).toBeTruthy();
    expect(screen.getByText("Net deposits")).toBeTruthy();
    expect(screen.getByText("$43.2k")).toBeTruthy(); // cash compact
    // Split text nodes → assert on the concatenated textContent.
    expect(container.textContent).toContain("of book · 10 positions");
    expect(screen.getByText("+$44.0k")).toBeTruthy(); // net deposits signed
  });

  it("shows an em dash for a null cash value", () => {
    render(
      <BalancesBand
        cashEUR={null}
        currentEquity={null}
        investedEUR={0}
        investedPct={0}
        positionsCount={0}
        netDeposits={null}
        startEquity={null}
        iso="USD"
        money={money}
      />,
    );
    // Cash tile value is the em dash.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });
});

describe("KpiBand", () => {
  it("renders the four since-inception cards with signed tones", () => {
    render(
      <KpiBand
        totalReturnPct={44.36}
        benchInception={12.5}
        maxDD={-8.2}
        sharpe={1.23}
        sinceHint="since inception"
        sharpeHint="realised curve"
      />,
    );
    expect(screen.getByText("autonomous_")).toBeTruthy();
    expect(screen.getByText("S&P 500")).toBeTruthy();
    expect(screen.getByText("Max drawdown")).toBeTruthy();
    expect(screen.getByText("Sharpe (annualized)")).toBeTruthy();
    expect(screen.getByText("1.23")).toBeTruthy();
    // A positive return is toned bull.
    const ret = screen.getByText("+44.36%");
    expect(ret.className).toContain("text-bull");
  });

  it("dashes a null Sharpe and a non-negative drawdown", () => {
    render(
      <KpiBand
        totalReturnPct={null}
        benchInception={null}
        maxDD={0}
        sharpe={null}
        sinceHint="since inception"
        sharpeHint="after ~20 live days"
      />,
    );
    expect(screen.getAllByText("—").length).toBe(4);
    expect(screen.getByText("after ~20 live days")).toBeTruthy();
  });
});

describe("PositionsTable", () => {
  const positions = [
    {
      symbol: "AMT",
      name: "American Tower",
      avgEntry: 178,
      last: 181,
      marketValue: 10162,
      unrealizedPct: 1.65,
    },
    {
      symbol: "FDX",
      name: "FedEx",
      avgEntry: 250,
      last: 246,
      marketValue: 9819,
      unrealizedPct: -1.7,
    },
  ];

  it("renders the header count and each position; uses renderSymbol; toned pnl", () => {
    const { container } = render(
      <PositionsTable
        positions={positions}
        money={money}
        investedPct={70.1}
        renderSymbol={(s) => <a data-testid={`sym-${s}`}>{s}</a>}
      />,
    );
    expect(container.textContent).toContain("2 open · ");
    expect(container.textContent).toContain("% invested");
    expect(screen.getByTestId("sym-AMT")).toBeTruthy();
    const up = screen.getByText("+1.65%");
    expect(up.className).toContain("text-bull");
    // fmtPct may render a Unicode minus for negatives → match the magnitude, not the sign glyph.
    const down = screen.getByText((t) => /1\.70%/.test(t));
    expect(down.className).toContain("text-bear");
  });

  it("omits the View-all button when no onViewAll is given (read-only demo), shows it otherwise", () => {
    const { rerender } = render(
      <PositionsTable positions={positions} money={money} investedPct={0} />,
    );
    expect(screen.queryByRole("button", { name: /view all/i })).toBeNull();
    const onViewAll = vi.fn();
    rerender(
      <PositionsTable
        positions={positions}
        money={money}
        investedPct={0}
        onViewAll={onViewAll}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /view all/i }));
    expect(onViewAll).toHaveBeenCalledOnce();
  });

  it("shows the empty state with no positions", () => {
    render(<PositionsTable positions={[]} money={money} investedPct={0} />);
    expect(screen.getByText("No open positions.")).toBeTruthy();
  });
});

describe("responsive layout (#3321 mobile)", () => {
  it("stacks the Balances band on phones (grid-cols-1 → sm:grid-cols-3) and uses 2 cols for KPIs", () => {
    const { container: b } = render(
      <BalancesBand
        cashEUR={1}
        currentEquity={1}
        investedEUR={1}
        investedPct={1}
        positionsCount={1}
        netDeposits={1}
        startEquity={1}
        iso="USD"
        money={money}
      />,
    );
    const bGrid = (b.firstElementChild as HTMLElement).className;
    expect(bGrid).toContain("grid-cols-1");
    expect(bGrid).toContain("sm:grid-cols-3");

    const { container: k } = render(
      <KpiBand
        totalReturnPct={1}
        benchInception={1}
        maxDD={-1}
        sharpe={1}
        sinceHint="x"
        sharpeHint="y"
      />,
    );
    const kGrid = (k.firstElementChild as HTMLElement).className;
    expect(kGrid).toContain("grid-cols-2");
    expect(kGrid).toContain("lg:grid-cols-4");
  });
});
