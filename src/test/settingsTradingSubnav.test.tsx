/**
 * UXC-1 S3 (#3156, owner decision 03.09. — Variante E): the Trading settings split
 * into two REAL pages driven by the sidebar sub-navigation (hash-routed):
 *   #trading        → General (account, safety, trading controls, reporting)
 *   #trading-agents → Agents (the round-table card)
 * No in-page segmented switcher any more.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";

// #4891: the strategy cards read through these; E1–E5 keep them rejecting (cards in error state).
const strategyApi = vi.hoisted(() => ({
  listStrategies: vi.fn(),
  listStrategyAssignments: vi.fn(),
  deleteStrategy: vi.fn(),
  assignPaperStrategy: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  assignPaperStrategy: (...a: unknown[]) => strategyApi.assignPaperStrategy(...a),
  listStrategies: (...a: unknown[]) => strategyApi.listStrategies(...a),
  listStrategyAssignments: (...a: unknown[]) => strategyApi.listStrategyAssignments(...a),
  deleteStrategy: (...a: unknown[]) => strategyApi.deleteStrategy(...a),
  fetchPendingConstraints: vi.fn().mockResolvedValue({ pending: null, approved: {} }),
  getApiBase: () => "http://localhost:8001",
  stop: vi.fn().mockResolvedValue({ status: "success" }),
  getHitlPolicy: vi.fn().mockResolvedValue({
    HITL_ENABLED: true,
    HITL_MAX_VALUE_PER_TRADE: 0,
    HITL_MAX_VALUE_PER_DAY: 0,
    HITL_AUTONOMOUS_UNLIMITED: false,
    HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: true,
    HITL_EXPIRY_SECONDS: 900,
  }),
  updateHitlPolicy: vi.fn().mockResolvedValue({}),
  getPerformanceBaseline: vi.fn().mockResolvedValue({ baseline_date: null }),
  setPerformanceBaseline: vi.fn().mockResolvedValue({ baseline_date: null }),
  fetchHealth: vi.fn().mockResolvedValue({ paper_trading: true }),
  liveEnable: vi.fn().mockResolvedValue(undefined),
  liveDisable: vi.fn().mockResolvedValue(undefined),
  fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
  getTradingSettings: vi.fn().mockResolvedValue({
    agents: [],
    implied_vol_forecast_enabled: true,
    settings: {},
    meta: [],
    guardrails: {},
  }),
  postTradingSettings: vi.fn(),
  startLive: vi.fn().mockResolvedValue({ status: "success" }),
  panicSell: vi.fn().mockResolvedValue({ status: "success" }),
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
}));

vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return {
    ...actual,
    isDesktop: () => true,
    getAppVersion: vi.fn().mockResolvedValue("0.0.0-test"),
    getSetupState: vi.fn().mockResolvedValue({}),
    saveSetupState: vi.fn().mockResolvedValue(undefined),
    getKeychainStatus: vi.fn().mockResolvedValue({}),
    restartEngine: vi.fn().mockResolvedValue(undefined),
    exportTelemetry: vi.fn(),
  };
});

import { Settings } from "../console/desktop/pages/Settings";

describe("Trading settings sub-navigation (Variante E)", () => {
  beforeEach(() => {
    window.location.hash = "";
    strategyApi.listStrategies.mockRejectedValue(new Error("not stubbed"));
    strategyApi.listStrategyAssignments.mockRejectedValue(new Error("not stubbed"));
    strategyApi.deleteStrategy.mockRejectedValue(new Error("not stubbed"));
  });
  afterEach(() => {
    window.location.hash = "";
    vi.clearAllMocks();
  });

  it("E1: #trading renders the General page — trading controls yes, round table no", async () => {
    window.location.hash = "trading";
    render(<Settings />);
    expect(await screen.findByText("Trading controls")).toBeTruthy();
    expect(screen.queryByText("Round table")).toBeNull();
  });

  it("E2: #trading-agents renders the Agents page — round table yes, trading controls no", async () => {
    window.location.hash = "trading-agents";
    render(<Settings />);
    expect(await screen.findByText("Round table")).toBeTruthy();
    expect(screen.queryByText("Trading controls")).toBeNull();
  });

  it("E3: a hashchange to trading-agents switches the page (deep-linkable)", async () => {
    window.location.hash = "trading";
    render(<Settings />);
    await screen.findByText("Trading controls");

    window.location.hash = "trading-agents";
    fireEvent(window, new Event("hashchange"));
    expect(await screen.findByText("Round table")).toBeTruthy();
    expect(screen.queryByText("Trading controls")).toBeNull();
  });

  it("E4: no in-page General/Agents segmented switcher exists any more", async () => {
    window.location.hash = "trading";
    render(<Settings />);
    await screen.findByText("Trading controls");
    expect(screen.queryByRole("button", { name: /^agents$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^general$/i })).toBeNull();
  });

  it("E5 (#4864, E1 Variante A): #trading shows the Strategies card above the live account strategy", async () => {
    window.location.hash = "trading";
    render(<Settings />);
    const list = await screen.findByText("Strategies", { selector: ".eyebrow" });
    const assign = await screen.findByText("Live account strategy", { selector: ".eyebrow" });
    expect(list.compareDocumentPosition(assign) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("E6 (#4891): passes the live assignment as assignments to StrategyList", async () => {
    strategyApi.listStrategies.mockResolvedValue({
      strategien: [
        { name: "A", werte: {} },
        { name: "B", werte: {} },
      ],
      max_strategien: 3,
    });
    strategyApi.listStrategyAssignments.mockResolvedValue([
      { konto: "live", strategie: "A", strategie_hash: "sha256:a", timestamp: "2026-10-29T10:00:00+00:00" },
    ]);
    window.location.hash = "trading";
    render(<Settings />);

    const rowOf = (name: string) =>
      screen.getAllByRole("row").find((r) => r.querySelector("td")?.textContent === name) as HTMLElement;
    await waitFor(() => expect(within(rowOf("A")).getByText("Live account")).toBeTruthy());
    expect(within(rowOf("B")).getByText("—")).toBeTruthy();
    expect(await screen.findByText(/Assigned: A/)).toBeTruthy();

    fireEvent.click(within(rowOf("A")).getByRole("button", { name: "Delete" }));
    expect(
      screen.getByText("This strategy is assigned to an account. Assign another strategy to that account first."),
    ).toBeTruthy();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(strategyApi.deleteStrategy).not.toHaveBeenCalled();
  });

  describe("paper accounts (#4953)", () => {
    const zuweisung = (konto: string, strategie: string) => ({
      konto, strategie, strategie_hash: "sha256:a", timestamp: "2026-10-30T10:00:00+00:00",
    });
    const rowOf = (name: string) =>
      screen.getAllByRole("row").find((r) => r.querySelector("td")?.textContent === name) as HTMLElement;

    beforeEach(() => {
      strategyApi.listStrategies.mockResolvedValue({
        strategien: [{ name: "A", werte: {} }, { name: "B", werte: {} }],
        max_strategien: 3,
      });
      (window as unknown as { aaagents: unknown }).aaagents = {
        isDesktop: true,
        listPaperSlots: vi.fn().mockResolvedValue([
          { slot: "paper1", connected: true, accountMasked: "••••1111", engineStatus: "running" },
        ]),
        getPaperSlotSetup: vi.fn().mockResolvedValue({}),
        savePaperSlotSetup: vi.fn().mockResolvedValue({ ok: true }),
        restartPaperSlotEngine: vi.fn().mockResolvedValue({ ok: true }),
      };
    });
    afterEach(() => {
      delete (window as unknown as { aaagents?: unknown }).aaagents;
    });

    it("E7: Assigned to names the paper accounts", async () => {
      strategyApi.listStrategyAssignments.mockResolvedValue([
        zuweisung("live", "A"), zuweisung("paper1", "A"), zuweisung("paper3", "B"),
      ]);
      window.location.hash = "trading";
      render(<Settings />);
      await waitFor(() => expect(within(rowOf("A")).getByText("Live account, Paper account 1")).toBeTruthy());
      expect(within(rowOf("B")).getByText("Paper account 3")).toBeTruthy();
    });

    it("E8: Assigned to is read again after an assignment to a slot", async () => {
      let stored = [zuweisung("live", "B")];
      strategyApi.listStrategyAssignments.mockImplementation(() => Promise.resolve(stored));
      strategyApi.assignPaperStrategy.mockImplementation(async () => {
        stored = [...stored, zuweisung("paper1", "A")];
        return { success: true, strategie: "A", applied: {}, changes: [], detail: "" };
      });
      window.location.hash = "trading";
      render(<Settings />);
      const slot = await screen.findByRole("group", { name: "Paper account 1" });
      await waitFor(() => expect(within(rowOf("A")).getByText("—")).toBeTruthy());

      fireEvent.change(within(slot).getByLabelText("Strategy for this account"), { target: { value: "A" } });
      fireEvent.click(within(slot).getByRole("button", { name: "Review changes" }));
      await screen.findByRole("dialog", { name: "Assign strategy to this paper account?" });
      fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));

      await waitFor(() => expect(within(rowOf("A")).getByText("Paper account 1")).toBeTruthy());
    });
  });
});
