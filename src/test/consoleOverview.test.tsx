import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { Overview } from "../console/desktop/pages/Overview";
import { Sidebar } from "../console/desktop/Sidebar";
import { useStore } from "../console/store/useStore";
import * as api from "../lib/api";
import type { ConsoleRoundTableDecision } from "../console/live/roundTable";
import type { ConsoleOpenOrder } from "../console/live/openOrders";

// Overview mounts three polls (portfolio, equity, round-table); stub the fetches
// so the test renders from seeded store state deterministically.
vi.mock("../lib/api", () => ({
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  fetchPortfolioSummary: vi.fn().mockResolvedValue(null),
  fetchBenchmarkEquity: vi.fn().mockResolvedValue(null),
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
  stop: vi.fn().mockResolvedValue({ status: "success" }),
  startLive: vi.fn().mockResolvedValue({ status: "success" }),
  panicSell: vi.fn().mockResolvedValue({ status: "success", message: "3 positions sold" }),
  fetchPortfolioIntraday: vi.fn().mockResolvedValue([]),
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchHealth: vi.fn().mockResolvedValue(null),
  fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
  getHitlPolicy: vi.fn().mockResolvedValue({ HITL_AUTONOMOUS_UNLIMITED: true }),
}));

// No-op the open-orders poll so the Order-book tile is driven purely by the seeded
// store state under test (otherwise a poll resolving mid-test rewrites openOrders).
vi.mock("../console/live/useOpenOrdersPolling", () => ({ useOpenOrdersPolling: () => {} }));

const day = (n: number) => new Date(2026, 5, n, 16);

const decision = (over: Partial<ConsoleRoundTableDecision> = {}): ConsoleRoundTableDecision => ({
  symbol: "AAPL",
  action: "BUY",
  passed: true,
  conviction: 0.62,
  sector: "Tech",
  votesFor: 5,
  votesAbstain: 2,
  votesAgainst: 1,
  vetoReason: "",
  ts: day(12).toISOString(),
  senators: [],
  ...over,
});

describe("console Overview page", () => {
  beforeEach(() => {
    useStore.setState({
      positions: [],
      cashEUR: null,
      currentEquity: null,
      lastEquity: null,
      equityCurve: [],
      benchmarkCurve: [],
      roundTable: [],
      lastSyncAt: null,
      openOrders: [],
      // #2190: the order-book tile is live-only; default the suite to live so the
      // existing order-book assertions hold. A dedicated test covers the paper case.
      paperTrading: false,
      // INC-7: default idle-state unknown so the live-idle Start control stays hidden
      // unless a test opts in explicitly.
      strategyRunning: null,
      // RC2-6: reset the machine-halt flag each test so the merged engine control is deterministic
      // (engineRunning = strategyRunning===true && systemHalted!==true).
      systemHalted: false,
    });
  });

  const openOrder = (over: Partial<ConsoleOpenOrder> = {}): ConsoleOpenOrder => ({
    id: "o1",
    symbol: "AAPL",
    side: "buy",
    qty: 0.2057,
    type: "limit",
    limitPrice: 320,
    stopPrice: null,
    status: "new",
    submittedAt: new Date("2026-07-15T16:39:00Z"),
    ...over,
  });

  it("renders the greeting + empty hero with no data", () => {
    render(<Overview />);
    expect(screen.getByText(/good (morning|afternoon|evening)/i)).toBeTruthy();
    // equity hero shows the em-dash placeholder before any data
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
    // empty equity curve reads as an honest "collecting" state, not a blank/broken chart
    expect(screen.getByText(/collecting equity history/i)).toBeTruthy();
  });

  it("shows a live relative 'Last sync' once a poll has landed", () => {
    useStore.setState({ lastSyncAt: Date.now() - 5_000 });
    render(<Overview />);
    expect(screen.getByText(/5s ago/i)).toBeTruthy();
  });

  it("renders the full dashboard chrome (market pill, KPI cards, two-up sections)", () => {
    render(<Overview />);
    // Hero pill + the since-inception summary row ("Since inception" also appears
    // as the Max-drawdown card hint, so it legitimately matches more than once)
    expect(screen.getByText(/Market/i)).toBeTruthy();
    // #overview-ia: Balances band (liquidity/composition) — a row of surface-flat tiles.
    expect(screen.getByText(/Cash \/ buying power/i)).toBeTruthy();
    // "Invested" also appears in the Top-positions header ("N% invested") → allow >1.
    expect(screen.getAllByText(/Invested/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Net deposits/i)).toBeTruthy();
    // The four coherent return/risk KPI cards — no Daily-P/L card (hero only), no Cash card (moved up).
    expect(screen.getAllByText(/Since inception/i).length).toBeGreaterThanOrEqual(1);
    // S&P is now a since-inception return (comparable to the portfolio's) — label appears on the
    // KPI card and (when a benchmark curve is present) the chart legend → allow >1.
    expect(screen.getAllByText(/S&P 500/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Max drawdown/i)).toBeTruthy();
    expect(screen.getByText(/Sharpe/i)).toBeTruthy();
    expect(screen.queryByText(/Daily P \/ L/i)).toBeNull();
    expect(screen.queryByText(/Cash \/ margin/i)).toBeNull();
    // Two-up Decision-queue + Top-positions sections (with empty states)
    expect(screen.getByText(/Decision queue/i)).toBeTruthy();
    expect(screen.getByText(/Top positions/i)).toBeTruthy();
    expect(screen.getByText(/No decisions yet/i)).toBeTruthy();
    expect(screen.getByText(/No open positions/i)).toBeTruthy();
  });

  it("renders equity, daily P/L and the range toggles from seeded data", () => {
    useStore.setState({
      currentEquity: 102_000,
      lastEquity: 101_000,
      cashEUR: 50_000,
      positions: [
        { symbol: "AAPL", name: "AAPL", qty: 1, avgEntry: 1, last: 1, marketValue: 1, unrealizedEUR: 0, unrealizedPct: 0, weight: 0, heldDays: 1 },
      ],
      equityCurve: [
        { t: day(10), eur: 100_000 },
        { t: day(11), eur: 101_000 },
        { t: day(12), eur: 102_000 },
      ],
    });
    render(<Overview />);
    expect(screen.getByText(/102,000\.00/)).toBeTruthy(); // current equity
    // #overview-ia: the hero change follows the selected range — default 1W → "past week" (was "today").
    expect(screen.getAllByText(/past week/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: "1M" })).toBeTruthy(); // range toggle
    expect(screen.getByText(/\$50\.00k/)).toBeTruthy(); // cash now in the Balances tile (compact, account currency)
  });

  it("#2588: chart axis labels use the ACCOUNT currency ($ on the USD paper account), not €", () => {
    // The store's accountCurrency defaults to USD (the Alpaca paper account), but the
    // EquityChart's axis/tooltip default to "€" unless Overview passes the real symbol —
    // the axis read "€129.4k" on a $-denominated account.
    useStore.setState({
      currentEquity: 102_000,
      equityCurve: [
        { t: day(10), eur: 100_000 },
        { t: day(11), eur: 101_000 },
        { t: day(12), eur: 102_000 },
      ],
    });
    render(<Overview />);
    // Axis min/max labels are compact "<sym><v>.<d>k" — must be $-prefixed…
    expect(screen.getAllByText(/^\$\d+(\.\d)?k$/).length).toBeGreaterThanOrEqual(2);
    // …and the hardcoded € default must be gone everywhere on the page.
    expect(screen.queryByText(/€/)).toBeNull();
  });

  it("renders a round-table decision row in the decision queue", () => {
    useStore.setState({ roundTable: [decision({ symbol: "NVDA", action: "BUY" })] });
    render(<Overview />);
    expect(screen.getByText("NVDA")).toBeTruthy();
    expect(screen.getByText("BUY")).toBeTruthy();
    expect(screen.getByText(/1 decision this session/i)).toBeTruthy();
  });

  it("shows an Order book tile (below the kill switch) with an empty state when no orders are open", () => {
    render(<Overview />);
    const heading = screen.getByText("Order book");
    const tile = heading.closest("div.surface") as HTMLElement;
    expect(within(tile).getByText(/no open orders/i)).toBeTruthy();
    // it is the current open orders only — never the fill history
    expect(screen.queryByText(/order history/i)).toBeNull();
  });

  it("lists current open orders (fractional qty) in the Order book tile and links View all → Orders page", () => {
    useStore.setState({
      openOrders: [openOrder({ id: "o1", symbol: "AAPL", qty: 0.2057, side: "buy", limitPrice: 320 })],
    });
    render(<Overview />);
    const tile = screen.getByText("Order book").closest("div.surface") as HTMLElement;
    expect(within(tile).getByText("AAPL")).toBeTruthy();
    expect(within(tile).getByText("0.2057")).toBeTruthy(); // fractional, never rounded to 0 (#2587: en-US decimal point)
    expect(within(tile).getByText(/1 working order\b/i)).toBeTruthy();
    // "View all" navigates to the Orders page (nav id "activities")
    fireEvent.click(within(tile).getByRole("button", { name: /view all/i }));
    expect(useStore.getState().desktopPage).toBe("activities");
  });

  it("#2190: hides the Order book tile in paper trading (market orders fill instantly)", () => {
    // paper (or unknown) → the always-empty order book is hidden entirely.
    useStore.setState({ paperTrading: true, openOrders: [] });
    render(<Overview />);
    expect(screen.queryByText("Order book")).toBeNull();
  });

  it("HITL: an open order in the Overview tile can be cancelled (2-step arm→confirm) and is removed", async () => {
    useStore.setState({ openOrders: [openOrder({ id: "o1" })] });
    const cancelOrder = vi.fn(async (_id: string) => ({ status: "success" }));
    render(<Overview cancelOrder={cancelOrder} />);
    const tile = screen.getByText("Order book").closest("div.surface") as HTMLElement;
    fireEvent.click(within(tile).getByRole("button", { name: /^cancel$/i })); // arm
    fireEvent.click(within(tile).getByRole("button", { name: /^confirm$/i })); // confirm
    await waitFor(() => expect(cancelOrder).toHaveBeenCalledWith("o1"));
    await waitFor(() => expect(useStore.getState().openOrders).toHaveLength(0));
  });

  it("#1/#4: Senior LIVE + idle shows Kill Switch (arming is the INC-2 popup's job)", async () => {
    useStore.setState({ tier: "PRO", paperTrading: false, strategyRunning: false, systemHalted: false });
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: /^kill switch$/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /start live trading/i })).toBeNull();
  });

  it("#2/#3: an idle LIVE engine shows NO Overview banner (the modal Continue-live-trading covers it)", () => {
    useStore.setState({ paperTrading: false, strategyRunning: false, systemHalted: false });
    render(<Overview />);
    expect(screen.queryByText(/is idle/i)).toBeNull();
    expect(screen.queryByText(/start engine/i)).toBeNull();
  });

  it("#3: an idle PAPER engine shows NO idle prompt (paper auto-starts; state is in the sidebar/header)", () => {
    useStore.setState({ paperTrading: true, strategyRunning: false, systemHalted: false });
    render(<Overview />);
    expect(screen.queryByText(/is idle/i)).toBeNull();
  });

  it("RC2-PR4: no idle prompt while the engine is actively trading either", () => {
    useStore.setState({ paperTrading: false, strategyRunning: true, systemHalted: false });
    render(<Overview />);
    expect(screen.queryByText(/is idle/i)).toBeNull();
  });

  it("merged engine controls: actively trading Senior shows Kill Switch", () => {
    useStore.setState({ tier: "PRO", paperTrading: false, strategyRunning: true, systemHalted: false });
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: /^kill switch$/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /start live trading/i, hidden: true })).toBeNull();
  });

  it("#1/#4: Senior in PAPER shows 'Start Live Trading' (the go-live switch)", () => {
    useStore.setState({ tier: "PRO", paperTrading: true, strategyRunning: false, systemHalted: false });
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: /start live trading/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^kill switch$/i })).toBeNull();
  });



  it("PR-3/#2618: live + empty equity curve + known equity shows the 'live track record building' note", () => {
    useStore.setState({ paperTrading: false, currentEquity: 120_000, equityCurve: [] });
    render(<Overview />);
    expect(screen.getByText(/live track record is building/i)).toBeTruthy();
  });

  it("#2618: the live-metrics note STAYS while live history is still thin (< ~20 live days)", () => {
    // Only a couple of live days -> S&P comparison / Sharpe are not yet meaningful (the
    // trailing S&P point can be a carry-forward reading as a fake 0.00 %), so the note must
    // remain up. (Old behaviour hid it as soon as any point existed, which left the fabricated
    // 0.00 % visible.)
    useStore.setState({
      paperTrading: false,
      currentEquity: 120_000,
      equityCurve: [
        { t: day(10), eur: 100_000 },
        { t: day(11), eur: 101_000 },
      ],
    });
    render(<Overview />);
    expect(screen.getByText(/live track record is building/i)).toBeTruthy();
  });

  it("#2618: the live-metrics note is hidden once enough live history accrues (>= 20 distinct days)", () => {
    useStore.setState({
      paperTrading: false,
      currentEquity: 120_000,
      equityCurve: Array.from({ length: 20 }, (_, i) => ({ t: day(i + 1), eur: 100_000 + i * 100 })),
    });
    render(<Overview />);
    expect(screen.queryByText(/live track record is building/i)).toBeNull();
  });

  it("PR-3: the live-metrics note never shows in paper (empty curve + known equity)", () => {
    useStore.setState({ paperTrading: true, currentEquity: 120_000, equityCurve: [] });
    render(<Overview />);
    expect(screen.queryByText(/live track record is building/i)).toBeNull();
  });

  it("PR-3: the live-metrics note is disjoint from the reconnect banner (null equity)", () => {
    useStore.setState({ paperTrading: false, currentEquity: null, equityCurve: [] });
    render(<Overview />);
    // null equity -> the reconnect banner path, never the live-metrics note
    expect(screen.queryByText(/live track record is building/i)).toBeNull();
    expect(screen.getByText(/reconnecting/i)).toBeTruthy();
  });

  it("RC2-PR4: even with the idle prompt shown, an idle LIVE engine has the Kill Switch control", () => {
    useStore.setState({ tier: "PRO", paperTrading: false, strategyRunning: false, systemHalted: false });
    render(<Sidebar />);
    expect(screen.getAllByRole("button", { name: /^kill switch$/i })).toHaveLength(1);
  });

  it("kill switch: confirming raises the emergency switch-to-paper signal (#2467)", async () => {
    // RC2-6: the Kill Switch only renders while the engine is actively trading LIVE for Senior desktops.
    useStore.setState({ tier: "PRO", strategyRunning: true, systemHalted: false, paperTrading: false, requestSwitchToPaper: false });
    render(<Sidebar />);
    // Open modal
    fireEvent.click(screen.getByRole("button", { name: /^kill switch$/i }));
    expect(screen.getByText("Emergency Stop")).toBeTruthy();
    // "Halt only" is preselected → confirming runs the audited toPaper path (positions kept).
    fireEvent.click(screen.getByRole("button", { name: /confirm emergency stop/i }));
    // #2467: the REAL kill runs via the global LiveConsentModal's audited toPaper path (trip +
    // mass-cancel + force_paper + WORM → restart → Paper). The Sidebar only raises the one-shot
    // signal — never the old soft /stop.
    await waitFor(() => expect(useStore.getState().requestSwitchToPaper).toBe(true));
    expect(vi.mocked(api.stop)).not.toHaveBeenCalled();
  });
});
