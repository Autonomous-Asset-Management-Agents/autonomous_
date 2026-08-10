import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Sidebar } from "../console/desktop/Sidebar";
import { tradingLabel } from "../console/live/trading";
import { useStore } from "../console/store/useStore";
import { fetchHealth } from "../lib/api";
import type { SpecialistReport } from "../console/types";
import type { ConsoleRoundTableDecision } from "../console/live/roundTable";

/**
 * Sidebar UX (#1050): the nav rail + the system-status footer. The footer now
 * shows the live engine state, an "Agents" count (specialist reports + senate
 * members) and a "Live Trading" state (strategy_running). The footer polls
 * /health via useHealthPolling and /api/entitlement/status via
 * useEntitlementPolling — stub the api so the test is deterministic.
 */
vi.mock("../lib/api", () => ({
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchHealth: vi.fn().mockResolvedValue(null),
  fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
  getHitlPolicy: vi.fn().mockResolvedValue({ HITL_AUTONOMOUS_UNLIMITED: true }),
}));

// The Upgrade CTA (GTM-1 #1915) claims via the desktop bridge — stub it so the
// click path is deterministic (default: the free beta grant succeeds).
const claimBeta = vi.fn().mockResolvedValue({ status: "claimed", tier: "PRO" });
vi.mock("../lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/desktopBridge")>();
  return { ...actual, claimBeta: () => claimBeta() };
});

const setBridge = (impl: Record<string, unknown> | undefined) => {
  (window as unknown as { aaagents?: unknown }).aaagents = impl;
};

describe("console Sidebar", () => {
  beforeEach(() => {
    setBridge(undefined);
    claimBeta.mockClear();
    useStore.setState({
      desktopPage: "chat", specialistReports: [], roundTable: [], strategyRunning: null,
      // RC2-7: reset the machine-state fields so the footer engine pill is deterministic per test.
      systemHalted: false, paperTrading: null,
      tier: null, canUpgrade: null,
    });
  });
  afterEach(() => setBridge(undefined));

  it("renders the chat launcher + every nav entry", () => {
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: /chat/i })).toBeTruthy();
    for (const label of ["Overview", "Decisions", "Positions", "Reports", "Audit chain", "Settings"]) {
      expect(screen.getByRole("button", { name: new RegExp(label, "i") })).toBeTruthy();
    }
  });

  it("footer shows Agents (specialists + senate) + Live Trading; no LLM/GPU", () => {
    useStore.setState({
      specialistReports: Array(3).fill({}) as unknown as SpecialistReport[],
      roundTable: [{ senators: Array(5).fill({}) }] as unknown as ConsoleRoundTableDecision[],
      strategyRunning: true,
    });
    render(<Sidebar />);
    expect(screen.getByText("Agents")).toBeTruthy();
    expect(screen.getByText("8")).toBeTruthy(); // 3 specialist reports + 5 senators
    expect(screen.getByText("Live Trading")).toBeTruthy();
    expect(screen.getByText("Paper")).toBeTruthy(); // strategy_running true, OSS is paper-only
    // removed rows
    expect(screen.queryByText("LLM")).toBeNull();
    expect(screen.queryByText("GPU")).toBeNull();
    expect(screen.queryByText("Specialists")).toBeNull();
    expect(screen.queryByText("Senate")).toBeNull();
  });

  it("Agents is '—' and Live Trading is 'Idle' when the trading loop is off", () => {
    useStore.setState({ specialistReports: [], roundTable: [], strategyRunning: false });
    render(<Sidebar />);
    expect(screen.getByText("Agents")).toBeTruthy();
    expect(screen.getByText("Idle")).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy(); // Agents value
  });

  it("footer shows 'Live' when the engine actually booted live (INC-2: honest state)", () => {
    // Single source of truth: the pill reads /health.paper_trading (store.paperTrading),
    // NOT a hardcoded false — a genuine live engine must not be mislabeled 'Paper'.
    useStore.setState({
      specialistReports: [],
      roundTable: [],
      strategyRunning: true,
      paperTrading: false,
    });
    render(<Sidebar />);
    expect(screen.getByText("Live")).toBeTruthy();
    expect(screen.queryByText("Paper")).toBeNull();
  });

  it("tradingLabel maps strategy_running + edition to the honest state/colour", () => {
    expect(tradingLabel(null, false)).toEqual({ text: "—", live: false });
    expect(tradingLabel(false, false)).toEqual({ text: "Idle", live: false });
    expect(tradingLabel(true, false)).toEqual({ text: "Paper", live: false });
    expect(tradingLabel(true, true)).toEqual({ text: "Live", live: true });
  });

  it("cloud build (no shell): engine reads as active (browser preview)", () => {
    render(<Sidebar />);
    expect(screen.getByText(/engine active/i)).toBeTruthy();
  });

  it("desktop build: a stopped engine reads as stopped", async () => {
    setBridge({
      isDesktop: true,
      startEngine: () => {},
      stopEngine: () => {},
      getEngineStatus: () => Promise.resolve({ status: "stopped" }),
      getEngineLogs: () => Promise.resolve([]),
      onEngineStatus: () => () => {},
      onEngineLog: () => () => {},
    });
    render(<Sidebar />);
    await screen.findByText(/system stopped/i);
  });

  // ── RC2-7: the footer pill mirrors the Overview #6 engine control (machine state, not process) ──
  const runningEngineBridge = () => ({
    isDesktop: true,
    startEngine: () => {},
    stopEngine: () => {},
    getEngineStatus: () => Promise.resolve({ status: "running" }),
    getEngineLogs: () => Promise.resolve([]),
    onEngineStatus: () => () => {},
    onEngineLog: () => () => {},
  });

  it("desktop + process up but the loop is NOT started ⇒ 'System ready' (⇔ Overview Start Engine)", async () => {
    setBridge(runningEngineBridge());
    useStore.setState({ strategyRunning: false, systemHalted: false });
    render(<Sidebar />);
    await screen.findByText(/system ready/i);
    expect(screen.queryByText(/engine active/i)).toBeNull();
  });

  it("desktop + the loop is running ⇒ 'Engine active' (⇔ Overview Kill Switch)", async () => {
    setBridge(runningEngineBridge());
    useStore.setState({ strategyRunning: true, systemHalted: false });
    render(<Sidebar />);
    await screen.findByText(/engine active/i);
    expect(screen.queryByText(/system ready/i)).toBeNull();
    expect(screen.queryByText(/engine halted/i)).toBeNull();
  });

  it("desktop + system halted ⇒ 'Engine halted' (⇔ Overview Start Engine), even if strategy_running", async () => {
    setBridge(runningEngineBridge());
    useStore.setState({ strategyRunning: true, systemHalted: true });
    render(<Sidebar />);
    await screen.findByText(/engine halted/i);
    expect(screen.queryByText(/engine active/i)).toBeNull();
  });

  // ── Startup-race guard (P1): the Start control must NOT fire POST /start-live while the engine is
  //    still initializing. The engine process can be up (status "running") while /health reports
  //    status:"starting" (BotEngine not built yet) → useHealthPolling records engineServing=false.
  //    Clicking then produced an AttributeError → HTTP 500 → the desktop UI showed "failed to fetch".
  //    The button must be DISABLED with a "starting" hint until /health confirms a serving engine.
  it("Start live trading is DISABLED with a 'starting' hint while /health reports status:starting", async () => {
    vi.mocked(fetchHealth).mockResolvedValue({
      status: "starting",
      strategy_running: false,
      system_halted: false,
      paper_trading: true,
    });
    setBridge(runningEngineBridge());
    useStore.setState({ tier: "PRO", strategyRunning: false, systemHalted: false, engineServing: false });
    render(<Sidebar />);
    // While booting the control is a disabled "STARTING…" button, not the live/paper action.
    const btn = await screen.findByRole("button", { name: /^starting$/i });
    await waitFor(() => expect(btn).toBeDisabled());
    expect(screen.getByText(/engine is starting/i)).toBeTruthy();
  });

  it("Start live trading is ENABLED once /health confirms a serving engine (status:healthy)", async () => {
    vi.mocked(fetchHealth).mockResolvedValue({
      status: "healthy",
      strategy_running: false,
      system_halted: false,
      paper_trading: true,
    });
    setBridge(runningEngineBridge());
    useStore.setState({ tier: "PRO", strategyRunning: false, systemHalted: false, engineServing: false });
    render(<Sidebar />);
    // Serving + paper → the actionable "Start Live Trading" button (no longer disabled).
    const btn = await screen.findByRole("button", { name: /start live trading/i });
    await waitFor(() => expect(btn).not.toBeDisabled());
  });

  // ── Upgrade CTA (GTM-1 #1915) ──────────────────────────────────────────────
  it("Junior (BASIC, canUpgrade) shows the free-beta Upgrade CTA, hides Chat", () => {
    useStore.setState({ tier: "BASIC", canUpgrade: true });
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: /^upgrade$/i })).toBeTruthy();
    // Chat is ENT-only now → hidden for the gated Junior tier.
    expect(screen.queryByRole("button", { name: /^chat$/i })).toBeNull();
  });

  it("Senior (PRO) sees the discreet 'Plans' entry (not the UPGRADE pill, not Chat) → opens the modal", () => {
    useStore.setState({ tier: "PRO", canUpgrade: false });
    render(<Sidebar />);
    expect(screen.queryByRole("button", { name: /^upgrade$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^chat$/i })).toBeNull();
    const plans = screen.getByRole("button", { name: /^plans$/i });
    fireEvent.click(plans);
    expect(screen.getByText(/plans & editions/i)).toBeTruthy();
  });

  it("clicking the CTA opens the tier upgrade modal (the Senior claim lives inside it)", () => {
    useStore.setState({ tier: "BASIC", canUpgrade: true });
    render(<Sidebar />);
    // The modal is not mounted until the CTA is clicked.
    expect(screen.queryByText(/upgrade your plan/i)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^upgrade$/i }));
    expect(screen.getByText(/upgrade your plan/i)).toBeTruthy();
    // The in-app claim path (Senior) is present in the modal; its behaviour is
    // covered by upgradeModal.test.tsx (#1915 never-fail-silently).
    expect(screen.getByRole("button", { name: /claim senior/i })).toBeTruthy();
  });

  // ── #2441 #1/#4: the footer button is keyed on the MODE (paperTrading), not the loop state ──
  it("Junior (BASIC) shows NO engine-control button (no 'Start Live Trading', no Kill Switch)", () => {
    useStore.setState({ tier: "BASIC", strategyRunning: true, systemHalted: false, paperTrading: false });
    render(<Sidebar />);
    expect(screen.queryByRole("button", { name: /start live trading/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /kill switch/i })).toBeNull();
  });

  it("Senior, PAPER (running or idle) ⇒ 'Start Live Trading' raises the go-live consent signal (no Settings detour)", () => {
    // Paper runs autonomously; the footer offers the go-live switch regardless of loop state.
    useStore.setState({ tier: "PRO", canUpgrade: false, strategyRunning: true, systemHalted: false, paperTrading: true, requestLiveConsent: false, desktopPage: "overview" });
    render(<Sidebar />);
    const btn = screen.getByRole("button", { name: /start live trading/i });
    expect(screen.queryByRole("button", { name: /kill switch/i })).toBeNull();
    fireEvent.click(btn);
    // #2465: the global LiveConsentModal opens OVER the current page — no navigation to Settings.
    expect(useStore.getState().requestLiveConsent).toBe(true);
    expect(useStore.getState().desktopPage).toBe("overview");
  });

  it("Senior, LIVE running ⇒ Kill Switch (not 'Start Live Trading')", () => {
    useStore.setState({ tier: "PRO", canUpgrade: false, strategyRunning: true, systemHalted: false, paperTrading: false });
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: /kill switch/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /start live trading/i })).toBeNull();
  });

  it("Senior, LIVE idle (fresh boot) ⇒ Kill Switch (arming is the INC-2 popup's job)", () => {
    useStore.setState({ tier: "PRO", canUpgrade: false, strategyRunning: false, systemHalted: false, paperTrading: false });
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: /kill switch/i })).toBeTruthy();
  });

  it("Senior, halted ⇒ 'Start Live Trading' (no Kill Switch on a halted engine)", () => {
    useStore.setState({ tier: "PRO", canUpgrade: false, strategyRunning: false, systemHalted: true, paperTrading: false });
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: /start live trading/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /kill switch/i })).toBeNull();
  });
});
