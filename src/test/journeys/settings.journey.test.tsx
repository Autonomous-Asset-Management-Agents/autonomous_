/**
 * Journey: Settings — engine lifecycle & safety — UX E2E #1050.
 *
 * Drives the engine control surface through the real desktop shell: navigate to
 * Settings → read the seeded status + log replay → Start the engine and watch
 * the status flip live over IPC → a freshly streamed log line lands in the pane
 * → button disabled-states track the lifecycle.
 *
 * The re-ported safety controls (execution-mode preference, emergency kill
 * switch → POST /stop) are covered at the component level in
 * `consoleSettings.test.tsx`; this journey owns the engine lifecycle flow.
 *
 * See src/test/journeys/README.md → "J3 Settings".
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { DesktopApp } from "@/console/desktop/DesktopApp";
import { useStore } from "@/console/store/useStore";
import { makeBridge, installBridge, resetBridge } from "../fixtures/mockBridge";
import * as fx from "../fixtures/consoleFixtures";

const gotoSystemSettings = () => {
  fireEvent.click(screen.getByRole("button", { name: /settings/i }));
  fireEvent.click(screen.getByRole("button", { name: /System/i }));
};

describe("Journey · Settings (engine lifecycle & safety)", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = (() => {}) as never;
    useStore.setState({ desktopPage: "chat" });
  });
  afterEach(() => resetBridge());

  it("shows the seeded status and replays the engine log on open", async () => {
    installBridge(makeBridge({ engineStatus: "stopped", logs: fx.engineLogs }).bridge);
    render(<DesktopApp />);
    gotoSystemSettings();

    await waitFor(() => expect(screen.getByRole("heading", { name: /System/i })).toBeTruthy());
    await waitFor(() => expect(screen.getByText(/engine offline/i)).toBeTruthy());
    expect(screen.getByText(fx.engineLogs[fx.engineLogs.length - 1])).toBeTruthy();
  });

  it("Start flips the status to running over IPC and tracks button state", async () => {
    const { bridge } = makeBridge({ engineStatus: "stopped" });
    installBridge(bridge);
    render(<DesktopApp />);
    gotoSystemSettings();

    await waitFor(() => expect(screen.getByRole("heading", { name: /System/i })).toBeTruthy());
    await waitFor(() => expect(screen.getByText(/engine offline/i)).toBeTruthy());
    const start = screen.getByRole("button", { name: /start engine/i });
    const stop = screen.getByRole("button", { name: /stop engine/i });
    expect(start).not.toBeDisabled();
    expect(stop).toBeDisabled();

    fireEvent.click(start); // startEngine() resolves + emits status:"running"

    await waitFor(() => expect(screen.getByText(/engine running/i)).toBeTruthy());
    expect(screen.getByRole("button", { name: /start engine/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /stop engine/i })).not.toBeDisabled();
    expect(bridge.startEngine).toHaveBeenCalledOnce();
  });

  it("a streamed log line appears live in the pane", async () => {
    const { bridge, emitLog } = makeBridge({ engineStatus: "running", logs: [] });
    installBridge(bridge);
    render(<DesktopApp />);
    gotoSystemSettings();

    await waitFor(() => expect(screen.getByRole("heading", { name: /System/i })).toBeTruthy());
    await waitFor(() => expect(screen.getByText(/engine running/i)).toBeTruthy());
    act(() => emitLog("[trade] BUY 10 AAPL @ 200.00 — filled"));
    await waitFor(() => expect(screen.getByText(/BUY 10 AAPL @ 200\.00 — filled/i)).toBeTruthy());
  });

  it("an engine error surfaces with its diagnostic detail", async () => {
    const { bridge, emitStatus } = makeBridge({ engineStatus: "running" });
    installBridge(bridge);
    render(<DesktopApp />);
    gotoSystemSettings();

    await waitFor(() => expect(screen.getByRole("heading", { name: /System/i })).toBeTruthy());
    await waitFor(() => expect(screen.getByText(/engine running/i)).toBeTruthy());
    act(() => emitStatus({ status: "error", detail: "engine exited (code 1) — port 8001 busy" }));
    await waitFor(() => expect(screen.getByText(/engine error/i)).toBeTruthy());
    expect(screen.getByText(/port 8001 busy/i)).toBeTruthy();
  });
});
