// #3168: the switch screen must NARRATE the ~40-60 s engine restart, and it must never inherit
// the previous boot's log lines (whose "uvicorn running" would read "almost ready…" the instant
// the switch begins). The overlay therefore subscribes to the LIVE engine-log stream at mount
// instead of reading the buffered log.
import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

const emitters: ((line: string) => void)[] = [];
vi.mock("@/lib/desktopBridge", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  // The live stream only — no buffered replay, which is the point of the fix.
  onEngineLog: (cb: (line: string) => void) => {
    emitters.push(cb);
    return () => {
      const i = emitters.indexOf(cb);
      if (i >= 0) emitters.splice(i, 1);
    };
  },
}));

import { ModeSwitchOverlay } from "../console/desktop/ModeSwitchOverlay";
import { useStore } from "../console/store/useStore";

const emit = (line: string) => act(() => emitters.forEach((cb) => cb(line)));
const reset = {
  modeSwitch: null,
  engineServing: false,
  paperTrading: null,
  switchReconciling: false,
  lastSyncAt: null,
  syncedPaper: null,
};

describe("ModeSwitchOverlay — restart narration (#3168)", () => {
  beforeEach(() => {
    emitters.length = 0;
    useStore.setState(reset);
  });
  afterEach(() => {
    vi.useRealTimers();
    useStore.setState(reset);
  });

  it("starts on the direction line and advances with the engine's boot log", () => {
    useStore.setState({ modeSwitch: { to: "live" }, engineServing: false, paperTrading: true });
    render(<ModeSwitchOverlay />);
    expect(screen.getByText(/arming live trading — restarting the engine/i)).toBeTruthy();

    emit("loading model lstm_v3.pt");
    expect(screen.getByText(/loading AI models/i)).toBeTruthy();

    emit("alpaca: get_account ok");
    expect(screen.getByText(/connecting to your broker/i)).toBeTruthy();

    emit("Uvicorn running on http://127.0.0.1:8001");
    expect(screen.getByText(/almost ready/i)).toBeTruthy();
  });

  it("the previous boot's lines cannot leak in — nothing is shown until a line actually arrives", () => {
    // No emissions = no log lines for THIS boot, even though the app has been running for
    // hours and its buffered engine log is full of "uvicorn running".
    useStore.setState({ modeSwitch: { to: "paper" }, engineServing: false, paperTrading: false });
    render(<ModeSwitchOverlay />);
    expect(screen.getByText(/returning to paper — restarting the engine/i)).toBeTruthy();
    expect(screen.queryByText(/almost ready/i)).toBeNull();
  });

  it("stops narrating the boot once the account has actually flipped", () => {
    useStore.setState({ modeSwitch: { to: "live" }, engineServing: false, paperTrading: true });
    render(<ModeSwitchOverlay />);
    emit("Uvicorn running");
    expect(screen.getByText(/almost ready/i)).toBeTruthy();
    // The reconcile loop confirms live before the 10 s health poll notices.
    act(() => useStore.setState({ paperTrading: false }));
    expect(screen.getByText(/syncing your live account/i)).toBeTruthy();
  });
});
