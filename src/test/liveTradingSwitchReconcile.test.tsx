/**
 * The USER-FACING half of "does live⇄paper switch cleanly?": after a switch, does the card show the
 * account the engine ACTUALLY booted — and when arming live doesn't take, does the operator see it?
 *
 * INC-2 (#2213) made the toggle reconcile from /health after a restart instead of optimistically
 * claiming success. But the existing tests (liveTradingSwitchCard.test.tsx / tradingAccountCard.test)
 * hold fetchHealth mocked to ONE constant across the restart and only assert that liveEnable/restart
 * were CALLED — never that the label flips. So the mislabel hazards ship with no behavioural check.
 *
 * GREEN here locks the reconcile contract: the state line settles to whatever /health reports, and
 * only from an AUTHORITATIVE (serving, non-"starting") read. Audit #05/#16 (LSR R4, #2251) is now a
 * real assertion — reconcilePaper polls a just-restarted engine past the null/"starting" boot window
 * instead of stamping the stale pre-switch label; a dedicated case proves the #08↔#05 integration
 * (the /health "starting" body now carries paper_trading, and the console polls over it). The audit
 * #01/#09/#18 silent-degrade spec lives in liveTradingSwitchCard.test.tsx (fixed by LSR R2, #2262).
 */
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useStore } from "@/console/store/useStore";

vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return {
    ...actual,
    isDesktop: () => true,
    claimBeta: vi.fn(),
    validateAlpaca: vi.fn(),
    saveSecret: vi.fn(),
    getKeychainStatus: vi.fn().mockResolvedValue({ ALPACA_LIVE_API_KEY: true, ALPACA_LIVE_SECRET_KEY: true }),
    restartEngine: vi.fn().mockResolvedValue(undefined),
  };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
    fetchHealth: vi.fn(),
    liveEnable: vi.fn().mockResolvedValue(undefined),
    liveDisable: vi.fn().mockResolvedValue(undefined),
  };
});

import { LiveTradingSwitchCard } from "@/console/desktop/LiveTradingSwitchCard";
import { LiveConsentModal } from "@/console/desktop/LiveConsentModal";
// #2465: switch logic moved to the global LiveConsentModal — render both (tile → signal → modal).
function Harness() {
  return (
    <>
      <LiveTradingSwitchCard />
      <LiveConsentModal />
    </>
  );
}
import { fetchHealth, liveEnable, liveDisable } from "@/lib/api";
import { restartEngine } from "@/lib/desktopBridge";

/** Drive the confirm modal all the way to "Enable live trading". */
async function armLive() {
  fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
  fireEvent.click(screen.getByLabelText("ack-live"));
  fireEvent.click(screen.getByLabelText("ack-advance-approval"));
  fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
}

describe("LiveTradingSwitchCard — reconcile reflects the booted account (INC-2)", () => {
  beforeEach(() => {
    vi.mocked(fetchHealth).mockReset();
    vi.mocked(liveEnable).mockReset().mockResolvedValue(undefined);
    vi.mocked(restartEngine).mockReset().mockResolvedValue(undefined);
    useStore.setState({ allowLive: true, paperTrading: null, engineServing: false, requestLiveConsent: false, requestSwitchToPaper: false, switchReconciling: false, switchConfirmFailed: false, switchError: null, modeSwitch: null });
  });

  it("arming live: the state line flips to Live once /health reports paper_trading=false", async () => {
    useStore.setState({ allowLive: true, paperTrading: true });
    vi.mocked(fetchHealth)
      .mockResolvedValueOnce({ paper_trading: true }) // mount seed (still paper)
      .mockResolvedValueOnce({ paper_trading: false }); // post-restart reconcile → live

    render(<Harness />);
    await armLive();

    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText(/orders execute with real money/i)).toBeTruthy(),
    );
  });

  // Quick-win: each reconcile /health call must be bounded by a SHORT per-call timeout, not the
  // 45s generic FETCH_TIMEOUT_MS. Under event-loop starvation /health can take up to 26s (OTel
  // 0.3.0); at the generic cap a single stalled call freezes the switch confirmation for up to 45s.
  it("reconcile polls /health with a short per-call timeout so a stalled /health can't freeze the switch", async () => {
    useStore.setState({ allowLive: true, paperTrading: true });
    vi.mocked(fetchHealth)
      .mockResolvedValueOnce({ paper_trading: true }) // mount seed
      .mockResolvedValue({ status: "healthy", paper_trading: false }); // reconcile settles live

    render(<Harness />);
    await armLive();
    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText(/orders execute with real money/i)).toBeTruthy(),
    );

    // At least one reconcile /health call must pass an explicit short timeout (> 0, ≤ 10s) —
    // proving the poll no longer inherits the 45s generic cap.
    const calledWithShortTimeout = vi
      .mocked(fetchHealth)
      .mock.calls.some((c) => typeof c[0] === "number" && c[0] > 0 && c[0] <= 10000);
    expect(calledWithShortTimeout).toBe(true);
  });

  it("switching back to paper: the state line flips to Paper once /health reports paper_trading=true", async () => {
    useStore.setState({ allowLive: true, paperTrading: false });
    vi.mocked(fetchHealth)
      .mockResolvedValueOnce({ paper_trading: false }) // mount seed (still live)
      .mockResolvedValueOnce({ paper_trading: true }); // post-restart reconcile → paper

    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: /^paper$/i }));

    await waitFor(() => expect(liveDisable).toHaveBeenCalled());
    // The state line (not the Paper tile's always-present description) settles to paper. The comma
    // phrase is unique to the state line — the tile body reads "…virtual money — no real capital…".
    await waitFor(() =>
      expect(screen.getByText(/simulated trading, no real capital at risk/i)).toBeTruthy(),
    );
  });

  // ── #05/#16 (LSR R4, #2251): reconcile from a SERVING engine, not a stale/starting /health ──────
  // Single-shot reconcilePaper read /health ONCE right after restart(); on connection-refused
  // fetchHealth returns null, `if (!h) return` skipped the write, and the label kept its PRE-switch
  // value → the card reassured "Paper — no real capital at risk" while the engine was arming LIVE.
  // R4 polls with bounded backoff until /health is authoritative before settling the label, so the
  // stale reassurance is gone. (#2265 shipped this as the it.fails acceptance test; R4 drops `.fails`.)
  it(
    "arming live with /health briefly unreachable must NOT keep showing 'no real capital at risk'",
    async () => {
      useStore.setState({ allowLive: true, paperTrading: true });
      vi.mocked(fetchHealth)
        .mockResolvedValueOnce({ paper_trading: true }) // mount seed
        .mockResolvedValueOnce(null) // first post-restart poll: engine still booting → unreachable
        .mockResolvedValue({ paper_trading: false }); // engine now serving live (a retry-fix converges here)

      render(<Harness />);
      await armLive();

      await waitFor(() => expect(liveEnable).toHaveBeenCalled());
      await waitFor(() => expect(restartEngine).toHaveBeenCalled());
      await waitFor(() => expect(screen.queryByLabelText("ack-live")).toBeNull()); // arming finished
      // The comma phrase targets the STATE LINE only (the Paper tile's description is always present).
      expect(screen.queryByText(/simulated trading, no real capital at risk/i)).toBeNull();
    },
  );

  // Extra coverage nobody else has — the #08↔#05 integration: the /health "starting" body now carries
  // paper_trading (backend R4), and reconcilePaper must poll PAST it (status:"starting" is NOT
  // authoritative) rather than settle on it. Proves the transient "confirming" state AND that the
  // label settles to Live only once /health actually serves.
  it("arming live: polls past a null then a 'starting' /health, shows a transient confirm (never stale paper), settles Live only once authoritative", async () => {
    useStore.setState({ allowLive: true, paperTrading: true });
    vi.mocked(fetchHealth)
      .mockResolvedValueOnce({ paper_trading: true }) // mount seed (paper before the switch)
      .mockResolvedValueOnce(null) // post-restart: engine not yet listening (connection refused)
      .mockResolvedValueOnce({ status: "starting" }) // bound but still booting (carries no live truth yet)
      .mockResolvedValue({ status: "healthy", paper_trading: false }); // serving → live

    render(<Harness />);
    await armLive();

    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
    await waitFor(() => expect(restartEngine).toHaveBeenCalled());
    // During the null/"starting" boot window: a transient confirm — NOT the reassuring stale paper
    // state line (the comma phrase is unique to the state line; the Paper tile description is always on).
    await waitFor(() => expect(screen.getByText(/confirming the account/i)).toBeTruthy());
    expect(screen.queryByText(/simulated trading, no real capital at risk/i)).toBeNull();
    // Settles to Live ONLY once /health is authoritative (serving, status !== "starting").
    await waitFor(() =>
      expect(screen.getByText(/orders execute with real money/i)).toBeTruthy(),
    );
  });

  // Mirror (live → paper) — a transient path the immediate-authoritative "switching back to paper"
  // test above does NOT cover: the boot window must not flash the stale "real money" LIVE label either
  // (the #16 mislabel is bidirectional). Distinct stale text, distinct fixture — not a duplicate.
  it("switching to paper: polls past a null then a 'starting' /health, never flashes the stale 'real money' line, settles Paper only once authoritative", async () => {
    useStore.setState({ allowLive: true, paperTrading: false });
    vi.mocked(fetchHealth)
      .mockResolvedValueOnce({ paper_trading: false }) // mount seed (live before the switch)
      .mockResolvedValueOnce(null) // post-restart: engine not yet listening
      .mockResolvedValueOnce({ status: "starting" }) // bound but still booting
      .mockResolvedValue({ status: "healthy", paper_trading: true }); // serving → paper

    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: /^paper$/i }));

    await waitFor(() => expect(liveDisable).toHaveBeenCalled());
    await waitFor(() => expect(restartEngine).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/confirming the account/i)).toBeTruthy());
    expect(screen.queryByText(/orders execute with real money/i)).toBeNull();
    await waitFor(() =>
      expect(screen.getByText(/simulated trading, no real capital at risk/i)).toBeTruthy(),
    );
  });

  // LSR R4 (#2251) — a reconcile give-up must NOT be terminal. If the engine restart runs LONGER than
  // reconcilePaper's ~4s cap the tile bounds out to a neutral "account unconfirmed" (bias-safe, never a
  // false paper label). But the engine then serves and the 10s background /health poll writes the real
  // account + a serving signal — the tile MUST recover, not sit forever on "engine isn't responding"
  // while it trades live money. (Real timers: the give-up genuinely takes the full ~4s backoff.)
  it(
    "a reconcile give-up ('account unconfirmed') recovers to the real account once the background poll confirms a serving engine",
    async () => {
      useStore.setState({ allowLive: true, paperTrading: true, engineServing: true });
      vi.mocked(fetchHealth)
        .mockResolvedValueOnce({ paper_trading: true }) // mount seed (paper before the switch)
        .mockResolvedValue(null); // engine never serves within the reconcile cap → give-up

      render(<Harness />);
      await armLive();

      vi.useFakeTimers();
      // Reconcile bounds out (~41.2s of backoff) to the neutral give-up — NOT a stale paper reassurance.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(41200); // Sum of RECONCILE_BACKOFF_MS (20 attempts)
      });
      expect(screen.getByText(/account unconfirmed/i)).toBeTruthy();
      expect(screen.queryByText(/simulated trading, no real capital at risk/i)).toBeNull();

      // The always-on /health poll now confirms a SERVING live engine (writes store paper + signal).
      act(() => {
        useStore.setState({ paperTrading: false, engineServing: true });
      });

      // The tile RECOVERS to the real account — never stuck on "engine isn't responding".
      expect(screen.queryByText(/account unconfirmed/i)).toBeNull();
      expect(screen.getByText(/orders execute with real money/i)).toBeTruthy();
      vi.useRealTimers();
    },
    20000,
  );

  // NOTE: the audit #01/#09/#18 spec (silent degrade-to-paper must surface a reason) is intentionally
  // NOT here — LSR R2 (#2262) fixed it (reconcilePaper(true) surfaces "Live not enabled: <reason>"
  // from /health.live_enable_failed_reason) AND shipped its own regression test in
  // liveTradingSwitchCard.test.tsx. Re-adding it here would duplicate that.
});

describe("LiveTradingSwitchCard Constants (Archon §3)", () => {
  it("test_reconcile_timeout_constants_sufficient: attempts >= 10, total backoff covers a cold boot, per-call cap is short", async () => {
    // Read the file as text to statically analyze the constants without exporting them
    const fs = await import("fs");
    const path = await import("path");
    const content = fs.readFileSync(path.join(__dirname, "../console/desktop/LiveConsentModal.tsx"), "utf8");

    const attemptsMatch = content.match(/const RECONCILE_MAX_ATTEMPTS = (\d+);/);
    expect(attemptsMatch).not.toBeNull();
    const attempts = parseInt(attemptsMatch![1]);
    expect(attempts).toBeGreaterThanOrEqual(10);

    const backoffMatch = content.match(/const RECONCILE_BACKOFF_MS = \[([^\]]+)\];/);
    expect(backoffMatch).not.toBeNull();
    // .filter drops the empty tail from a prettier trailing comma on the multi-line array literal.
    const backoffMs = backoffMatch![1]
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0)
      .map((s) => parseInt(s));
    expect(backoffMs.length).toBe(attempts - 1);

    // Window must cover a cold engine boot (model load + DB bootstrap) that answers /health
    // "starting" quickly the whole time — the old ~17.2s window false-failed such boots.
    const totalBackoff = backoffMs.reduce((a, b) => a + b, 0);
    expect(totalBackoff).toBeGreaterThanOrEqual(30000);

    // Per-call cap must be short (bounds a stalled /health) but not so short it aborts a legit slow read.
    const pollTimeoutMatch = content.match(/const RECONCILE_POLL_TIMEOUT_MS = (\d+);/);
    expect(pollTimeoutMatch).not.toBeNull();
    const pollTimeout = parseInt(pollTimeoutMatch![1]);
    expect(pollTimeout).toBeGreaterThanOrEqual(2000);
    expect(pollTimeout).toBeLessThanOrEqual(10000);
  });
});
