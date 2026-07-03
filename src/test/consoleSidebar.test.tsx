import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Sidebar } from "../console/desktop/Sidebar";
import { tradingLabel } from "../console/live/trading";
import { useStore } from "../console/store/useStore";
import type { SpecialistReport } from "../console/types";
import type { ConsoleRoundTableDecision } from "../console/live/roundTable";

/**
 * Sidebar UX (#1050): the nav rail + the system-status footer. The footer now
 * shows the live engine state, an "Agents" count (specialist reports + senate
 * members) and a "Live Trading" state (strategy_running). The footer polls
 * /health via useHealthPolling — stub the api so the test is deterministic.
 */
vi.mock("../lib/api", () => ({ fetchHealth: vi.fn().mockResolvedValue(null) }));

const setBridge = (impl: Record<string, unknown> | undefined) => {
  (window as unknown as { aaagents?: unknown }).aaagents = impl;
};

describe("console Sidebar", () => {
  beforeEach(() => {
    setBridge(undefined);
    useStore.setState({ desktopPage: "chat", specialistReports: [], roundTable: [], strategyRunning: null });
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

  it("tradingLabel maps strategy_running + edition to the honest state/colour", () => {
    expect(tradingLabel(null, false)).toEqual({ text: "—", live: false });
    expect(tradingLabel(false, false)).toEqual({ text: "Idle", live: false });
    expect(tradingLabel(true, false)).toEqual({ text: "Paper", live: false });
    expect(tradingLabel(true, true)).toEqual({ text: "Live", live: true });
  });

  it("cloud build (no shell): engine reads as running (browser preview)", () => {
    render(<Sidebar />);
    expect(screen.getByText(/engine running/i)).toBeTruthy();
  });

  it("desktop build: a stopped engine reads as offline", async () => {
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
    await screen.findByText(/engine offline/i);
  });
});
