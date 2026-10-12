import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { ModeSwitchOverlay } from "../console/desktop/ModeSwitchOverlay";
import { useStore } from "../console/store/useStore";

/**
 * Operator finding #8: switching Paper↔Live restarts the engine (~60s to serve on a
 * slow machine). Instead of flashing the red "Backend Offline" screen, we show the
 * calm autonomous-boot experience — a green heartbeat that resolves into the typed
 * `autonomous` wordmark once the engine serves. Never red, never "offline"/"error",
 * never an AlertCircle.
 */
describe("ModeSwitchOverlay (#8)", () => {
  beforeEach(() => {
    useStore.setState({ modeSwitch: null, engineServing: false, paperTrading: null, switchReconciling: false, lastSyncAt: null, syncedPaper: null });
  });
  afterEach(() => {
    vi.useRealTimers();
    useStore.setState({ modeSwitch: null, engineServing: false, paperTrading: null, switchReconciling: false, lastSyncAt: null, syncedPaper: null });
  });

  it("renders nothing when no switch is in progress", () => {
    const { container } = render(<ModeSwitchOverlay />);
    expect(container.firstChild).toBeNull();
  });

  it("live switch: calm green heartbeat + reassuring copy, never offline/error/AlertCircle", () => {
    useStore.setState({ modeSwitch: { to: "live" }, engineServing: false });
    const { container } = render(<ModeSwitchOverlay />);

    // The heartbeat — the machine's pulse coming back — not a spinner-that-reads-broken.
    expect(screen.getByTestId("mode-switch-heartbeat")).toBeTruthy();
    // Dynamic status: while the engine is not yet serving, it is restarting into live mode.
    expect(screen.getByText(/restarting the engine/i)).toBeTruthy();
    expect(screen.getByText(/live/i)).toBeTruthy();

    // The calm face: none of the offline/error language or icon.
    expect(container.innerHTML).not.toMatch(/offline/i);
    expect(container.innerHTML).not.toMatch(/error/i);
    expect(container.innerHTML).not.toMatch(/lucide/i); // AlertCircle ships as a lucide-* class

    // The wordmark only appears once the engine serves again — not during the heartbeat.
    expect(screen.queryByText(/autonomous/)).toBeNull();
  });

  it("paper switch: neutral, reassuring copy (no real money at risk)", () => {
    useStore.setState({ modeSwitch: { to: "paper" }, engineServing: false });
    const { container } = render(<ModeSwitchOverlay />);
    // Dynamic status: while the engine is not yet serving, it is restarting back into paper.
    expect(screen.getByText(/returning to paper/i)).toBeTruthy();
    expect(screen.getByText(/restarting the engine/i)).toBeTruthy();
    expect(container.innerHTML).not.toMatch(/offline/i);
    expect(container.innerHTML).not.toMatch(/error/i);
  });

  describe("dynamic status (progress + current activity, like the boot splash)", () => {
    it("engine serving + reconcile in flight → shows the reconcile activity", () => {
      useStore.setState({
        modeSwitch: { to: "live" },
        engineServing: true,
        paperTrading: true, // /health hasn't flipped yet
        switchReconciling: true, // reconcile running
      });
      render(<ModeSwitchOverlay />);
      expect(screen.getByText(/reconciling your open positions/i)).toBeTruthy();
      // Not settled → still the heartbeat, not the wordmark.
      expect(screen.getByTestId("mode-switch-heartbeat")).toBeTruthy();
    });

    it("engine serving + reconcile done but account not yet the target → shows the account switch", () => {
      useStore.setState({
        modeSwitch: { to: "live" },
        engineServing: true,
        paperTrading: true, // still paper — target is live (paper_trading:false)
        switchReconciling: false,
      });
      render(<ModeSwitchOverlay />);
      expect(screen.getByText(/switching your account to live/i)).toBeTruthy();
    });

    it("engine serving + account is target but snapshot still stale → shows the data sync", () => {
      useStore.setState({
        modeSwitch: { to: "live", startedAt: 1000 },
        engineServing: true,
        paperTrading: false, // target reached
        switchReconciling: false,
        lastSyncAt: 500, // < startedAt → snapshot still the old account's → not fresh
        syncedPaper: null,
      });
      render(<ModeSwitchOverlay />);
      expect(screen.getByText(/syncing your live account/i)).toBeTruthy();
    });
  });

  it("resolves the heartbeat into the autonomous wordmark once the engine serves", async () => {
    vi.useFakeTimers();
    useStore.setState({ modeSwitch: { to: "live" }, engineServing: false });
    await act(async () => {
      render(<ModeSwitchOverlay />);
    });
    // #2465: the pulse reveals ONLY when fully settled — engine serving AND the reconcile has
    // confirmed the TARGET account (live → paper_trading:false) AND reconcile finished.
    await act(async () => {
      useStore.setState({ engineServing: true, paperTrading: false, switchReconciling: false });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200); // run the typewriter to completion
    });
    expect(screen.getByText(/autonomous/)).toBeTruthy();
  });

  it("#2465: keeps the pulse up while the engine serves but the account is NOT yet the target (no early reveal)", async () => {
    vi.useFakeTimers();
    // Going live: the engine answers /health again, but it still reports paper_trading:true (reconcile
    // pending / a degraded live boot). The pulse must NOT reveal — no intermediate state may leak.
    useStore.setState({ modeSwitch: { to: "live" }, engineServing: true, paperTrading: true, switchReconciling: false });
    await act(async () => {
      render(<ModeSwitchOverlay />);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200);
    });
    expect(screen.getByTestId("mode-switch-heartbeat")).toBeTruthy(); // still the heartbeat
    expect(screen.queryByText(/autonomous/)).toBeNull(); // NOT revealed
    expect(useStore.getState().modeSwitch).not.toBeNull(); // overlay still up
  });

  it("clears the switch flag after the reveal so the live app shows through", async () => {
    vi.useFakeTimers();
    // #2465: fully settled paper switch (engine serving + reconcile done + paper_trading:true).
    useStore.setState({ modeSwitch: { to: "paper" }, engineServing: true, paperTrading: true, switchReconciling: false });
    await act(async () => {
      render(<ModeSwitchOverlay />);
    });
    // hold + fade → the overlay clears itself (heartbeat → wordmark → reveal complete).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500 + 420 + 50);
    });
    expect(useStore.getState().modeSwitch).toBeNull();
  });

  it("kill-switch/toPaper: keeps the pulse up while the account snapshot is STILL the old account (no fresh sync since switch start)", async () => {
    vi.useFakeTimers();
    // The engine has restarted to paper and /health reports paper_trading:true, but the portfolio
    // poll has NOT yet re-synced for the paper account — lastSyncAt is from BEFORE the switch began
    // (the stale LIVE €224 snapshot is still in the store). Revealing now would flash live data on
    // the paper page. The pulse must stay until fresh target-account data has landed.
    useStore.setState({
      modeSwitch: { to: "paper", startedAt: 1000 },
      engineServing: true,
      paperTrading: true,
      switchReconciling: false,
      lastSyncAt: 500, // < startedAt → the shown equity/positions are still the old account's
    });
    await act(async () => {
      render(<ModeSwitchOverlay />);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200);
    });
    expect(screen.getByTestId("mode-switch-heartbeat")).toBeTruthy(); // still the heartbeat
    expect(screen.queryByText(/autonomous/)).toBeNull(); // NOT revealed
    expect(useStore.getState().modeSwitch).not.toBeNull(); // overlay still up
  });

  it("kill-switch/toPaper: reveals once the TARGET account's portfolio has synced (lastSyncAt > startedAt, syncedPaper=true)", async () => {
    vi.useFakeTimers();
    useStore.setState({
      modeSwitch: { to: "paper", startedAt: 1000 },
      engineServing: true,
      paperTrading: true,
      switchReconciling: false,
      lastSyncAt: 500,
      syncedPaper: null,
    });
    await act(async () => {
      render(<ModeSwitchOverlay />);
    });
    // The PAPER portfolio poll lands AFTER the switch began → fresh paper €153k is now in the
    // store, tagged as the paper account (syncedPaper=true).
    await act(async () => {
      useStore.setState({ lastSyncAt: 2000, syncedPaper: true });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200); // run the typewriter to completion
    });
    expect(screen.getByText(/autonomous/)).toBeTruthy();
  });

  it("Bug B/toPaper: a STALE in-flight LIVE poll (lastSyncAt > startedAt but syncedPaper=false) must NOT reveal", async () => {
    vi.useFakeTimers();
    // The engine has restarted to paper and /health reports paper_trading:true, and a portfolio
    // poll DID land after the switch began (lastSyncAt > startedAt) — but it was an in-flight poll
    // against the DYING live engine, so it carries the LIVE account's data (syncedPaper=false).
    // The account-agnostic gate would have revealed this stale live €224 on the paper page for a
    // beat; the account-aware gate must hold the pulse until genuine PAPER data lands.
    useStore.setState({
      modeSwitch: { to: "paper", startedAt: 1000 },
      engineServing: true,
      paperTrading: true,
      switchReconciling: false,
      lastSyncAt: 2000, // landed AFTER startedAt → satisfies the OLD (account-agnostic) gate
      syncedPaper: false, // …but it is the LIVE account's snapshot → the new gate rejects it
    });
    await act(async () => {
      render(<ModeSwitchOverlay />);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200);
    });
    expect(screen.getByTestId("mode-switch-heartbeat")).toBeTruthy(); // still the heartbeat
    expect(screen.queryByText(/autonomous/)).toBeNull(); // NOT revealed
    expect(useStore.getState().modeSwitch).not.toBeNull(); // overlay still up
  });

  it("Bug B/toLive: a STALE in-flight PAPER poll (syncedPaper=true) must NOT reveal; only live data (syncedPaper=false) reveals", async () => {
    vi.useFakeTimers();
    // Going live: /health flips to paper_trading:false, and a poll lands after startedAt — but it
    // is the old PAPER account's in-flight snapshot (syncedPaper=true). Revealing now would flash
    // the paper numbers on the live page. Hold until the live account's data lands.
    useStore.setState({
      modeSwitch: { to: "live", startedAt: 1000 },
      engineServing: true,
      paperTrading: false,
      switchReconciling: false,
      lastSyncAt: 2000,
      syncedPaper: true, // stale paper snapshot
    });
    await act(async () => {
      render(<ModeSwitchOverlay />);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200);
    });
    expect(screen.getByTestId("mode-switch-heartbeat")).toBeTruthy();
    expect(screen.queryByText(/autonomous/)).toBeNull();

    // Now the LIVE account's portfolio lands (syncedPaper=false) → reveal.
    await act(async () => {
      useStore.setState({ lastSyncAt: 3000, syncedPaper: false });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200);
    });
    expect(screen.getByText(/autonomous/)).toBeTruthy();
  });

  it("safety timeout: a switch that never serves clears itself so /offline can resume", async () => {
    vi.useFakeTimers();
    useStore.setState({ modeSwitch: { to: "live" }, engineServing: false });
    await act(async () => {
      render(<ModeSwitchOverlay />);
    });
    // Engine never comes back — after the generous safety window the overlay gives up.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(90_000 + 50);
    });
    expect(useStore.getState().modeSwitch).toBeNull();
  });

  it("Bug B/no-wedge: an untagged (error) poll does NOT clobber a previously-synced target tag", () => {
    // A tagged live-account poll lands, then a /portfolio-summary error poll (no paper_trading)
    // interleaves. The error poll must bump freshness but keep the target tag — otherwise the
    // reveal would re-block for a cycle, or wedge to the 90s timeout if every poll errored.
    useStore.setState({ syncedPaper: null, lastSyncAt: null });
    useStore.getState().markSynced(false); // tagged live-account poll
    expect(useStore.getState().syncedPaper).toBe(false);
    useStore.getState().markSynced(null); // untagged / error poll
    expect(useStore.getState().syncedPaper).toBe(false); // NOT clobbered back to null
    expect(useStore.getState().lastSyncAt).not.toBeNull(); // freshness still advanced
  });
});
