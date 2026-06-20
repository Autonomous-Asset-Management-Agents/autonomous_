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
    window.localStorage.clear();
    vi.clearAllMocks();
    // Latency ping uses fetch — stub it so it resolves quietly.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));
  });
  afterEach(() => {
    setBridge(undefined);
    vi.unstubAllGlobals();
  });

  it("cloud build: hides the engine card but still renders the page", () => {
    render(<Settings />);
    // Engine controls are desktop-only → absent in the cloud build…
    expect(screen.queryByRole("button", { name: /start engine/i })).toBeNull();
    // …but the rest of the desktop UX renders.
    expect(screen.getByText(/Execution mode/i)).toBeTruthy();
    expect(screen.getByText(/Emergency kill switch/i)).toBeTruthy();
    expect(screen.getByText(/Decision routing/i)).toBeTruthy();
  });

  it("desktop build: seeds status + logs and Start engine invokes the bridge", async () => {
    const bridge = desktopBridge({
      getEngineStatus: vi.fn().mockResolvedValue({ status: "stopped" }),
      getEngineLogs: vi.fn().mockResolvedValue(["booting…", "ready"]),
    });
    setBridge(bridge);

    render(<Settings />);
    await waitFor(() => expect(screen.getByText("ready")).toBeTruthy()); // replayed log
    expect(screen.getByText("OFFLINE")).toBeTruthy(); // seeded status pill

    fireEvent.click(screen.getByRole("button", { name: /start engine/i }));
    expect(bridge.startEngine).toHaveBeenCalledOnce();
  });

  it("disables Start while running and Stop while stopped", async () => {
    setBridge(desktopBridge({ getEngineStatus: vi.fn().mockResolvedValue({ status: "running" }) }));
    render(<Settings />);
    await waitFor(() => expect(screen.getByText("RUNNING")).toBeTruthy());
    expect(screen.getByRole("button", { name: /start engine/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /stop engine/i })).not.toBeDisabled();
  });

  it("execution mode: choosing Full Autonomous persists the preference", () => {
    render(<Settings />);
    // Default is the safe HITL (no key written yet).
    expect(window.localStorage.getItem("aaa.settings.executionMode")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Full Autonomous/i }));
    expect(window.localStorage.getItem("aaa.settings.executionMode")).toBe("auto");
    expect(screen.getByText(/Full Autonomous preferred/i)).toBeTruthy();
  });

  it("kill switch: arm then confirm calls the halt API", async () => {
    render(<Settings />);
    fireEvent.click(screen.getByRole("button", { name: /arm kill switch/i }));
    fireEvent.click(screen.getByRole("button", { name: /confirm — halt now/i }));
    await waitFor(() => expect(screen.getByText(/Engine halted/i)).toBeTruthy());
    expect(vi.mocked(api.stop)).toHaveBeenCalledOnce();
  });
});
