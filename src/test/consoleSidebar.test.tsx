import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Sidebar } from "../console/desktop/Sidebar";
import { useStore } from "../console/store/useStore";

/**
 * Sidebar UX re-port (#1050): the nav rail + the bundle's system-status footer.
 * Engine state is wired through useEngine (window.aaagents bridge); the
 * Specialists/Senate/LLM/GPU figures main's engine doesn't expose render "—".
 */
const setBridge = (impl: Record<string, unknown> | undefined) => {
  (window as unknown as { aaagents?: unknown }).aaagents = impl;
};

describe("console Sidebar", () => {
  beforeEach(() => {
    setBridge(undefined);
    useStore.setState({ desktopPage: "chat" });
  });
  afterEach(() => setBridge(undefined));

  it("renders the chat launcher + every nav entry", () => {
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: /chat/i })).toBeTruthy();
    for (const label of ["Overview", "Decisions", "Positions", "Reports", "Audit chain", "Settings"]) {
      expect(screen.getByRole("button", { name: new RegExp(label, "i") })).toBeTruthy();
    }
  });

  it("renders the system-status footer with honest '—' for unexposed figures", () => {
    render(<Sidebar />);
    expect(screen.getByText("Specialists")).toBeTruthy();
    expect(screen.getByText("Senate")).toBeTruthy();
    expect(screen.getByText("LLM")).toBeTruthy();
    expect(screen.getByText("GPU")).toBeTruthy();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
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
