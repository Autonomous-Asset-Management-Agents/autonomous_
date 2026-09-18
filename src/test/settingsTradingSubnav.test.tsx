/**
 * UXC-1 S3 (#3156, owner decision 03.09. — Variante E): the Trading settings split
 * into two REAL pages driven by the sidebar sub-navigation (hash-routed):
 *   #trading        → General (account, safety, trading controls, reporting)
 *   #trading-agents → Agents (the round-table card)
 * No in-page segmented switcher any more.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
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
});
