import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Settings } from "../console/desktop/pages/Settings";
import * as api from "@/lib/api";
import { useStore } from "@/console/store/useStore";

/**
 * Settings UX re-port (#1050): the desktop "Engine, broker & safety" screen —
 * decision routing, execution-mode preference, the engine lifecycle card
 * (desktop only), the broker panel and the emergency kill switch. The engine
 * card is driven through the desktopBridge surface; the kill switch hits the
 * real POST /stop via the api module (mocked here).
 */
vi.mock("@/lib/api", () => ({
  fetchPendingConstraints: vi.fn().mockResolvedValue({ pending: null, approved: {} }),
  approvePortfolioConstraints: vi.fn().mockResolvedValue({ status: "approved" }),
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  getApiBase: () => "http://localhost:8001",
  stop: vi.fn().mockResolvedValue({ status: "success" }),
  // HitlPolicyCard (LIVE-1 T2) loads the real policy on mount — stub it for the Settings render.
  getHitlPolicy: vi.fn().mockResolvedValue({
    HITL_ENABLED: true,
    HITL_MAX_VALUE_PER_TRADE: 0,
    HITL_MAX_VALUE_PER_DAY: 0,
    HITL_AUTONOMOUS_UNLIMITED: false,
    HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: true,
    HITL_EXPIRY_SECONDS: 900,
  }),
  updateHitlPolicy: vi.fn().mockResolvedValue({}),
  // LiveTradingSwitchCard (#1425) reads the active account on mount + can switch it.
  fetchHealth: vi.fn().mockResolvedValue({ paper_trading: true }),
  liveEnable: vi.fn().mockResolvedValue(undefined),
  liveDisable: vi.fn().mockResolvedValue(undefined),
  // #2863: Settings no longer calls startLive() (the SafetyControlCard was removed) — but this
  // mock fully REPLACES @/lib/api, so the stub stays to keep the kill-switch path (killSwitch.ts)
  // defined for anything in the render tree that imports it.
  startLive: vi.fn().mockResolvedValue({ status: "success" }),
  // Kill-switch "sell all" variant — POST /panic-sell (halt + liquidate).
  panicSell: vi.fn().mockResolvedValue({ status: "success", message: "3 positions sold" }),
}));

vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();

  interface MockWindow {
    aaagents?: {
      isDesktop?: boolean;
      startEngine?: () => Promise<void>;
      stopEngine?: () => Promise<void>;
      getEngineStatus?: () => Promise<unknown>;
      getEngineLogs?: () => Promise<unknown>;
      onEngineStatus?: (cb: unknown) => () => void;
      onEngineLog?: (cb: unknown) => () => void;
      exportTelemetry?: () => Promise<unknown>;
      getVersion?: () => Promise<string | null>;
    };
  }

  const getAgents = () => (window as unknown as MockWindow).aaagents;

  return {
    ...actual,
    isDesktop: () => !!getAgents()?.isDesktop,
    startEngine: async () => { await getAgents()?.startEngine?.(); },
    stopEngine: async () => { await getAgents()?.stopEngine?.(); },
    getEngineStatus: async () => {
      const r = (await getAgents()?.getEngineStatus?.()) as { status: string } | undefined;
      return (r?.status as EngineStatus) ?? "unavailable";
    },
    getEngineLogs: async () => (await getAgents()?.getEngineLogs?.()) ?? [],
    onEngineStatus: (cb: unknown) => getAgents()?.onEngineStatus?.(cb) ?? (() => {}),
    onEngineLog: (cb: unknown) => getAgents()?.onEngineLog?.(cb) ?? (() => {}),
    exportTelemetry: async () => (await getAgents()?.exportTelemetry?.()) ?? { ok: false, error: "bridge-offline" },
    getAppVersion: async () => (await getAgents()?.getVersion?.()) ?? null,
  };
});

const setBridge = (impl: Record<string, unknown> | undefined) => {
  (window as unknown as { aaagents?: unknown }).aaagents = impl;
};

const desktopBridge = (over: Record<string, unknown> = {}) => ({
  isDesktop: true,
  startEngine: vi.fn().mockResolvedValue(undefined),
  stopEngine: vi.fn().mockResolvedValue(undefined),
  getEngineStatus: vi.fn().mockResolvedValue({ status: "stopped" }),
  getEngineLogs: vi.fn().mockResolvedValue([]),
  onEngineStatus: () => () => {},
  onEngineLog: () => () => {},
  ...over,
});

describe("console Settings page", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    setBridge(undefined);
    window.location.hash = "";
    window.localStorage.clear();
    vi.clearAllMocks();
    // Latency ping uses fetch — stub it so it resolves quietly.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));
  });
  afterEach(() => {
    setBridge(undefined);
    vi.unstubAllGlobals();
  });

  it("cloud build: hides the engine card but still renders the page", async () => {
    render(<Settings />);
    // Engine controls are desktop-only → absent in the cloud build…
    expect(screen.queryByRole("button", { name: /start engine/i })).toBeNull();
    // …but the rest of the desktop UX renders.
    expect(screen.getByText("Full Autonomous")).toBeTruthy();
    expect(screen.getByText(/Autonomous Execution/i)).toBeTruthy();

    // wait for the HitlPolicyCard async load → honest state line (INC-7, no fake "approval: ON" pill)
    await waitFor(() => expect(screen.getByText(/human approval required/i)).toBeTruthy());
  });

  it("desktop build: seeds status + logs and Start engine invokes the bridge", async () => {
    const bridge = desktopBridge({
      getEngineStatus: vi.fn().mockResolvedValue({ status: "stopped" }),
      getEngineLogs: vi.fn().mockResolvedValue(["booting…", "ready"]),
    });
    setBridge(bridge);

    window.location.hash = "#system";
    render(<Settings />);
    await waitFor(() => expect(screen.getByText("ready")).toBeTruthy()); // replayed log
    expect(screen.getByText("OFFLINE")).toBeTruthy(); // seeded status pill

    fireEvent.click(screen.getByRole("button", { name: /start engine/i }));
    expect(bridge.startEngine).toHaveBeenCalledOnce();
  });

  it("disables Start while running and Stop while stopped", async () => {
    setBridge(desktopBridge({ getEngineStatus: vi.fn().mockResolvedValue({ status: "running" }) }));
    window.location.hash = "#system";
    render(<Settings />);
    await waitFor(() => expect(screen.getByText("RUNNING")).toBeTruthy());
    expect(screen.getByRole("button", { name: /start engine/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /stop engine/i })).not.toBeDisabled();
  });

  // (#2863) The Emergency-Safety / Kill-Switch card was removed from the Settings Trading
  // block — the Kill-Switch is permanently present in the left sidebar (covered by
  // killSwitch.test.tsx). Its Settings-only emergency-stop-modal test is dropped here.

  it("autonomy: the mode selector drives the REAL engine policy, not localStorage (INC-7)", async () => {
    render(<Settings />);
    // Wait for HitlPolicyCard to load the real policy (mode tiles become interactive).
    await waitFor(() =>
      expect((screen.getByLabelText("mode-auto") as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(screen.getByLabelText("mode-auto"));
    // Choosing "Full Autonomous" POSTs the REAL HITL_AUTONOMOUS_UNLIMITED — no cosmetic localStorage.
    await waitFor(() => expect(api.updateHitlPolicy).toHaveBeenCalled());
    const body = (api.updateHitlPolicy as unknown as { mock: { calls: unknown[][] } }).mock
      .calls[0][0] as Record<string, unknown>;
    expect(body.HITL_AUTONOMOUS_UNLIMITED).toBe(true);
    expect(window.localStorage.getItem("aaa.settings.executionMode")).toBeNull();
  });


  it("hash routing: deep-links directly to tabs externally", async () => {
    setBridge(desktopBridge());
    // Simulate an external link opening settings#system
    window.location.hash = "#system";
    render(<Settings />);

    // The "System" tab now shows the vendor-independent LLM picker (#1705)
    await waitFor(() => expect(screen.getByText(/vendor-independent/i)).toBeTruthy());

    // Change hash externally (e.g. from a desktopBridge IPC call or another link)
    window.location.hash = "#notifications";
    fireEvent(window, new Event("hashchange"));

    // The "Notifications" tab renders the Daily Report card. It's a Senior feature, so a
    // default (non-Senior) session sees the locked upsell.
    await waitFor(() =>
      expect(screen.getByText(/Automated daily reports are a Senior feature/i)).toBeTruthy(),
    );
  });
});
