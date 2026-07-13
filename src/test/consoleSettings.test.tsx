import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Settings } from "../console/desktop/pages/Settings";
import * as api from "@/lib/api";

/**
 * Settings UX re-port (#1050): the desktop "Engine, broker & safety" screen —
 * decision routing, execution-mode preference, the engine lifecycle card
 * (desktop only), the broker panel and the emergency kill switch. The engine
 * card is driven through the desktopBridge surface; the kill switch hits the
 * real POST /stop via the api module (mocked here).
 */
vi.mock("@/lib/api", () => ({
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
}));

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
    expect(screen.getByText(/Execution mode/i)).toBeTruthy();
    expect(screen.getByText(/Autonomous Execution/i)).toBeTruthy();

    // wait for async effect in HitlPolicyCard to finish
    await waitFor(() => expect(screen.getByText(/Human approval: ON/i)).toBeTruthy());
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

  it("execution mode: defaults to Full Autonomous; choosing Human-in-the-loop persists it (#1653)", async () => {
    render(<Settings />);
    // Default is autonomous (#1653, matching #1442) — nothing written until the operator switches.
    expect(window.localStorage.getItem("aaa.settings.executionMode")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Human-in-the-loop/i }));
    expect(window.localStorage.getItem("aaa.settings.executionMode")).toBe("hitl");
    expect(screen.getByText(/Human-in-the-loop preferred/i)).toBeTruthy();

    // wait for async effect in HitlPolicyCard to finish
    await waitFor(() => expect(screen.getByText(/Human approval: ON/i)).toBeTruthy());
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

    // The "Notifications" tab content should be visible
    await waitFor(() => expect(screen.getByText(/No channels configured for daily updates/i)).toBeTruthy());
  });
});
