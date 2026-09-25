// #1425 (LIVE-1 T2) — Paper⇄Live account switcher. Switching to live requires a deliberate Art-14
// acknowledgment → POST /api/live/enable (WORM) → engine restart. Never bypasses the gate.
import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const isDesktop = vi.fn();
const restartEngine = vi.fn();
const fetchHealth = vi.fn();
const liveEnable = vi.fn();
const liveDisable = vi.fn();
const updateHitlPolicy = vi.fn();
const getKeychainStatus = vi.fn();

const validateAlpaca = vi.fn();
const saveSecret = vi.fn();
vi.mock("@/lib/desktopBridge", () => ({
  isDesktop: () => isDesktop(),
  restartEngine: () => restartEngine(),
  getKeychainStatus: () => getKeychainStatus(),
  validateAlpaca: (k: string, s: string, l?: boolean) => validateAlpaca(k, s, l),
  saveSecret: (k: string, v: string) => saveSecret(k, v),
}));
vi.mock("@/lib/api", () => ({
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  fetchHealth: () => fetchHealth(),
  liveEnable: (a: string, n: string) => liveEnable(a, n),
  liveDisable: (a: string, n: string) => liveDisable(a, n),
  // #2277 INC-3: the card now emits reconcile telemetry (fail-open) after a switch.
  postSwitchReconcile: vi.fn().mockResolvedValue(true),
  // #2454: the consent persists the chosen live autonomy before the WORM enable.
  updateHitlPolicy: (body: unknown) => updateHitlPolicy(body),
}));

import { LiveTradingSwitchCard } from "../console/desktop/LiveTradingSwitchCard";
import { LiveConsentModal } from "../console/desktop/LiveConsentModal";
import { useStore } from "../console/store/useStore";

// #2465: the go-live consent + switch logic moved to the global LiveConsentModal. Render both
// together so the existing card-driven flow tests exercise the same end-to-end path
// (Live tile → requestLiveConsent signal → global modal opens → toLive).
function Harness() {
  return (
    <>
      <LiveTradingSwitchCard />
      <LiveConsentModal />
    </>
  );
}

describe("LiveTradingSwitchCard (#1425)", () => {
  beforeEach(() => {
    isDesktop.mockReset().mockReturnValue(true);
    restartEngine.mockReset().mockResolvedValue(undefined);
    fetchHealth.mockReset().mockResolvedValue({ paper_trading: true });
    liveEnable.mockReset().mockResolvedValue(undefined);
    liveDisable.mockReset().mockResolvedValue(undefined);
    updateHitlPolicy.mockReset().mockResolvedValue({});
    // #2441: default the keychain to HAVING live keys so the existing enable tests proceed.
    getKeychainStatus.mockReset().mockResolvedValue({ ALPACA_LIVE_API_KEY: true, ALPACA_LIVE_SECRET_KEY: true });
    validateAlpaca.mockReset().mockResolvedValue({ ok: true, status: 200, equity: 50000 });
    saveSecret.mockReset().mockResolvedValue({ ok: true });
    // #8: reset the deliberate-switch flag between tests (shared module-level store).
    // #2441: also reset the go-live signal + entitlement so tests don't leak state.
    useStore.setState({ modeSwitch: null, engineServing: false, requestLiveConsent: false, allowLive: null, paperTrading: null, liveEquity: null });
  });

  it("cloud build: renders nothing", () => {
    isDesktop.mockReturnValue(false);
    const { container } = render(<Harness />);
    expect(container.firstChild).toBeNull();
  });

  it("#2441: the requestLiveConsent signal launches TODAY'S go-live confirm and clears the flag", async () => {
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy()); // /health resolved → paper
    // The external "Start Live Trading" button asks to start the flow (store signal).
    act(() => useStore.getState().setRequestLiveConsent(true));
    // It opens the EXISTING WORM/ACK confirm — no new flow invented.
    await waitFor(() => expect(screen.getByText(/enable live trading\?/i)).toBeTruthy());
    // One-shot: the flag is consumed so it can't re-fire on the next render.
    expect(useStore.getState().requestLiveConsent).toBe(false);
  });

  it("#2441: the signal is a no-op for a Junior (allow_live=false) — never opens a live confirm", async () => {
    useStore.setState({ allowLive: false });
    render(<Harness />);
    act(() => useStore.getState().setRequestLiveConsent(true));
    await waitFor(() => expect(useStore.getState().requestLiveConsent).toBe(false)); // consumed
    expect(screen.queryByText(/enable live trading\?/i)).toBeNull();
  });

  it("paper account: shows the Paper|Live toggle", async () => {
    render(<Harness />);
    await waitFor(() => expect(screen.getByRole("button", { name: /^live$/i })).toBeTruthy());
    expect(screen.getByRole("button", { name: /^paper$/i })).toBeTruthy();
  });

  it("switch to live REQUIRES the Art-14 ack AND the advance-approval waiver, then enables + restarts", async () => {
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));

    // Enable is gated on BOTH consents (Art-14 acknowledgment + advance-approval waiver, #1804)
    const enableBtn = screen.getByRole("button", { name: /enable live trading/i });
    expect(enableBtn).toBeDisabled();
    fireEvent.click(screen.getByLabelText("ack-live"));
    expect(enableBtn).toBeDisabled(); // still gated on the advance-approval waiver
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    expect(enableBtn).not.toBeDisabled();

    fireEvent.click(enableBtn);
    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
    // BOTH consents recorded on the WORM chain (Art-14 + advance-approval), plus a nonce
    expect(liveEnable.mock.calls[0][0]).toMatch(/art\.? 14/i);
    expect(liveEnable.mock.calls[0][0]).toMatch(/advance-approval/i);
    expect(liveEnable.mock.calls[0][1]).toBeTruthy();
    // engine atomically restarted (INC-1b) so the shell re-reads the WORM chain
    await waitFor(() => expect(restartEngine).toHaveBeenCalled());
  });

  it("#2700: the go-live confirm offers TWO modes and no value field", async () => {
    // The 0-25% slider (#2671) and the per-trade amount (#2454) are both gone. Neither could be
    // trusted: with the live keys already in the keychain the wizard never re-validated them, so
    // liveEquity stayed null, `equity x 25%` collapsed to 0, and a deliberately chosen "full
    // autonomy" silently became all-manual — 13 held orders on 2026-08-06.
    useStore.setState({ liveEquity: 50000 });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /approve every order/i })).toBeTruthy());
    expect(screen.getByRole("button", { name: /approve every order/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /full autonomy/i })).toBeTruthy();
    // No value input anywhere — nothing to convert, nothing to collapse.
    expect(screen.queryByLabelText("hitl-per-trade")).toBeNull();
    expect(screen.queryByRole("button", { name: /10% of equity/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /custom/i })).toBeNull();
  });

  it("#2824: Full autonomy is preselected — RED-marked so the default is unmistakable", async () => {
    // Product decision 13.08. (#2824, revises the #2700 default): autonomy IS the product's
    // intended operating mode, so it is preselected — but marked in the Enable-button red
    // (#ff5a52) so the operator consciously sees what the default arms. Both acks + the
    // waiver still gate the switch; a picked mode still persists explicitly (#2700).
    useStore.setState({ liveEquity: 50000 });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /full autonomy/i })).toBeTruthy());
    const full = screen.getByRole("button", { name: /full autonomy/i });
    expect(full.getAttribute("aria-pressed")).toBe("true");
    // SOLID kill-switch red (Sidebar.tsx kill switch / Enable button), NOT a translucent
    // tint - glow-style selected states were already rejected in the #2035 de-glow round.
    expect(full.className).toContain("bg-[#ff5a52]");
    expect(full.className).not.toMatch(/ff5a52\]\/\d/); // no /20-style alpha tint
    expect(full.className).toContain("text-white");
    // The manual mode stays one click away, unpressed and neutral (no red).
    const manual = screen.getByRole("button", { name: /approve every order/i });
    expect(manual.getAttribute("aria-pressed")).toBe("false");
    expect(manual.className).not.toContain("ff5a52");
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
    await waitFor(() => expect(updateHitlPolicy).toHaveBeenCalled());
    // Untouched => Mode C (full autonomy) — exactly what the red preselection announced.
    expect(updateHitlPolicy.mock.calls[0][0]).toMatchObject({ HITL_AUTONOMOUS_UNLIMITED: true });
    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
  });

  it("#2824: explicitly choosing 'Approve every order' still persists all-manual", async () => {
    useStore.setState({ liveEquity: 50000 });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /approve every order/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /approve every order/i }));
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
    await waitFor(() => expect(updateHitlPolicy).toHaveBeenCalled());
    // All-manual, and protective sells stay exempt so a stop-out is never queued.
    expect(updateHitlPolicy.mock.calls[0][0]).toMatchObject({
      HITL_AUTONOMOUS_UNLIMITED: false,
      HITL_MAX_VALUE_PER_TRADE: 0,
      HITL_MAX_VALUE_PER_DAY: 0,
      HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: true,
    });
    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
  });

  it("#2700: the policy is written BEFORE the WORM enable and independent of equity", async () => {
    // liveEquity deliberately null — the old mapping produced 0 here and silently changed the mode.
    useStore.setState({ liveEquity: null });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /approve every order/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /full autonomy/i }));
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
    await waitFor(() => expect(updateHitlPolicy).toHaveBeenCalled());
    expect(updateHitlPolicy.mock.calls[0][0]).toMatchObject({ HITL_AUTONOMOUS_UNLIMITED: true });
    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
  });

  it("#4: no live keys AND none entered → 'Enable live trading' stays disabled", async () => {
    getKeychainStatus.mockResolvedValue({}); // no live keys saved
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    await waitFor(() => expect(screen.getByText(/required \(none saved yet\)/i)).toBeTruthy());
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    // Both consents ticked but no keys typed → Enable blocked (can't strand the engine live+idle).
    expect(screen.getByRole("button", { name: /enable live trading/i })).toBeDisabled();
  });

  it("#4: keys typed in the modal are VALIDATED + SAVED by the Enable submit BEFORE the WORM enable", async () => {
    getKeychainStatus.mockResolvedValue({}); // first-timer: no live keys yet
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    await screen.findByLabelText("live-key-id");
    fireEvent.change(screen.getByLabelText("live-key-id"), { target: { value: "PKLIVEKEY" } });
    fireEvent.change(screen.getByLabelText("live-secret"), { target: { value: "livesecret" } });
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
    // The submit orchestrates a) validate → b) save → … → d) liveEnable, in order.
    await waitFor(() => expect(validateAlpaca).toHaveBeenCalledWith("PKLIVEKEY", "livesecret", true));
    await waitFor(() => expect(saveSecret).toHaveBeenCalledWith("ALPACA_LIVE_API_KEY", "PKLIVEKEY"));
    expect(saveSecret).toHaveBeenCalledWith("ALPACA_LIVE_SECRET_KEY", "livesecret");
    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
    // save happened BEFORE the WORM enable (mandated sequence a/b before d)
    expect(saveSecret.mock.invocationCallOrder[0]).toBeLessThan(liveEnable.mock.invocationCallOrder[0]);
  });

  it("#4: invalid live keys → inline error, modal stays open, NO liveEnable/restart (error-lifecycle)", async () => {
    getKeychainStatus.mockResolvedValue({});
    validateAlpaca.mockResolvedValue({ ok: false, status: 401, equity: null });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    await screen.findByLabelText("live-key-id");
    fireEvent.change(screen.getByLabelText("live-key-id"), { target: { value: "BAD" } });
    fireEvent.change(screen.getByLabelText("live-secret"), { target: { value: "BAD" } });
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
    // (shown in the modal; the Harness also mounts the Settings card which mirrors switchError)
    await waitFor(() => expect(screen.getAllByText(/rejected these live keys/i).length).toBeGreaterThan(0));
    // The mandated stop: no save, no WORM enable, no restart, modal still open.
    expect(saveSecret).not.toHaveBeenCalled();
    expect(liveEnable).not.toHaveBeenCalled();
    expect(restartEngine).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /enable live trading/i })).toBeTruthy();
  });

  it("#2441 pre-validation: with live keys saved, Enable proceeds to the WORM enable", async () => {
    getKeychainStatus.mockResolvedValue({ ALPACA_LIVE_API_KEY: true, ALPACA_LIVE_SECRET_KEY: true });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /approve every order/i })).toBeTruthy());
    expect(screen.queryByText(/no live alpaca keys are saved/i)).toBeNull();
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
  });

  it("#2700: choosing Full autonomy persists Mode C (unlimited)", async () => {
    useStore.setState({ liveEquity: 50000 });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /approve every order/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /full autonomy/i }));
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
    await waitFor(() => expect(updateHitlPolicy).toHaveBeenCalled());
    expect(updateHitlPolicy.mock.calls[0][0]).toMatchObject({ HITL_AUTONOMOUS_UNLIMITED: true });
  });

  it("live account: the Paper toggle disables live (switch back to paper)", async () => {
    fetchHealth.mockResolvedValue({ paper_trading: false });
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: /^paper$/i }));
    await waitFor(() => expect(liveDisable).toHaveBeenCalled());
    expect(restartEngine).toHaveBeenCalled();
  });

  // LSR R2 (#2253): a live arming that reconciles to PAPER with a recorded reason is an HONEST
  // failure — the card must surface it, never silently sit on Paper (I-4, fixes #01/#09).
  it("live arming that degrades to paper WITH a reason shows an explicit error, not silent paper", async () => {
    // engine booted PAPER and reported why (terminal Alpaca-401 degrade + compensating WORM disable)
    fetchHealth.mockResolvedValue({
      paper_trading: true,
      live_enable_failed_reason: "degraded-live: Alpaca auth rejected (HTTP 401)",
    });
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());

    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));

    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
    // The honest failure is surfaced — not a silent snap back to Paper.
    await waitFor(() =>
      expect(screen.getByText(/Live not enabled:/i)).toBeTruthy(),
    );
    expect(screen.getByText(/HTTP 401/i)).toBeTruthy();
  });

  // FIX #3: the active "account" dot is green (#00c27a) when Paper is active (paper-safe) and red
  // (#ff5a52) when Live is active (real money) — mirrors the StatusDot on/off tones.
  it("paper-active shows a GREEN active dot; live-active shows a RED active dot", async () => {
    // Paper active → green dot
    fetchHealth.mockResolvedValue({ paper_trading: true });
    const { unmount } = render(<Harness />);
    const paperTile = await screen.findByRole("button", { name: /^paper$/i });
    await waitFor(() => expect(within(paperTile).getByText(/^active$/i)).toBeTruthy());
    expect(paperTile.innerHTML).toContain("bg-[#00c27a]");
    expect(paperTile.innerHTML).not.toContain("bg-[#ff5a52]");
    unmount();

    // Live active → red dot
    fetchHealth.mockResolvedValue({ paper_trading: false });
    render(<Harness />);
    const liveTile = await screen.findByRole("button", { name: /^live$/i });
    await waitFor(() => expect(within(liveTile).getByText(/^active$/i)).toBeTruthy());
    expect(liveTile.innerHTML).toContain("bg-[#ff5a52]");
    expect(liveTile.innerHTML).not.toContain("bg-[#00c27a]");
  });

  it("a clean switch back to paper does NOT show a false live-failure error", async () => {
    // paper_trading:true WITHOUT a reason (an ordinary switch-to-paper) must stay quiet.
    fetchHealth.mockResolvedValue({ paper_trading: false });
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: /^paper$/i }));
    await waitFor(() => expect(liveDisable).toHaveBeenCalled());
    expect(screen.queryByText(/Live not enabled:/i)).toBeNull();
  });

  // #8: the switch raises the calm ModeSwitchOverlay (App.tsx renders it and suppresses
  // /offline while it's set). The card sets it at the start of the switch; the overlay
  // owns the SUCCESS clear, so it stays set through a healthy restart.
  it("arming live sets modeSwitch {to:'live'} and keeps it through a healthy restart", async () => {
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
    await waitFor(() => expect(restartEngine).toHaveBeenCalled());
    // Overlay owns the success clear (heartbeat → wordmark), so the flag persists here.
    // (modeSwitch also carries a `startedAt` marker that drives the overlay's data-freshness gate.)
    expect(useStore.getState().modeSwitch?.to).toBe("live");
    expect(typeof useStore.getState().modeSwitch?.startedAt).toBe("number");
  });

  it("switching to paper sets modeSwitch {to:'paper'}", async () => {
    fetchHealth.mockResolvedValue({ paper_trading: false });
    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: /^paper$/i }));
    await waitFor(() => expect(liveDisable).toHaveBeenCalled());
    expect(useStore.getState().modeSwitch?.to).toBe("paper");
    expect(typeof useStore.getState().modeSwitch?.startedAt).toBe("number");
  });

  // A genuinely failed switch must CLEAR the overlay so the honest error/offline shows.
  it("a live-enable failure clears modeSwitch so the honest error can surface", async () => {
    liveEnable.mockRejectedValue(new Error("engine refused"));
    render(<Harness />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
    // The error surfaces (in the card body and the still-open confirm modal — hence getAllByText).
    await waitFor(() =>
      expect(screen.getAllByText(/Couldn't enable live trading/i).length).toBeGreaterThan(0),
    );
    expect(useStore.getState().modeSwitch).toBeNull();
  });
});
