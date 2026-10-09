/**
 * #2708 D1 — a live-arming intent must never survive the transition it was raised in.
 *
 * The go-live consent modal opens off the one-shot `requestLiveConsent` store signal. The HIGH
 * real-money bug: a signal raised while the engine is mid-restart (`paperTrading == null`, /health
 * silent) was NOT consumed, so it re-fired when the switch to paper settled (`paper` → `true`) and
 * the consent modal opened BY ITSELF on the paper page — the operator confirmed it believing it
 * belonged to the transition, and the account went LIVE. These tests pin that the signal is consumed
 * unconditionally and the modal never opens as a consequence of a switch (or while one is in flight),
 * while a normal go-live still opens it.
 */
import { render, screen, act, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useStore } from "@/console/store/useStore";

vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return {
    ...actual,
    isDesktop: () => true,
    getKeychainStatus: vi
      .fn()
      .mockResolvedValue({ ALPACA_LIVE_API_KEY: true, ALPACA_LIVE_SECRET_KEY: true }),
    restartEngine: vi.fn().mockResolvedValue(undefined),
    validateAlpaca: vi.fn(),
    saveSecret: vi.fn(),
  };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchHealth: vi.fn().mockResolvedValue(null),
    liveEnable: vi.fn().mockResolvedValue(undefined),
    liveDisable: vi.fn().mockResolvedValue(undefined),
    updateHitlPolicy: vi.fn().mockResolvedValue(undefined),
    postSwitchReconcile: vi.fn().mockResolvedValue(undefined),
  };
});

import { LiveConsentModal } from "@/console/desktop/LiveConsentModal";

// The modal renders its "Enable Live Trading?" heading only when it is open (confirming && paper).
const modalIsOpen = () => screen.queryByText(/enable live trading\?/i);

describe("LiveConsentModal — a live-arming intent never survives a transition (#2708 D1)", () => {
  beforeEach(() => {
    useStore.setState({
      allowLive: true,
      paperTrading: null,
      switchReconciling: false,
      modeSwitch: null,
      requestLiveConsent: false,
      switchError: null,
    });
  });

  it("drops an intent raised while the account state is unknown (no modal after paper settles)", async () => {
    render(<LiveConsentModal />);
    // The go-live signal is raised while the engine is mid-restart (paper == null).
    act(() => {
      useStore.setState({ requestLiveConsent: true });
    });
    // The switch to paper then settles.
    act(() => {
      useStore.setState({ paperTrading: true });
    });
    await waitFor(() => expect(useStore.getState().requestLiveConsent).toBe(false)); // consumed
    expect(modalIsOpen()).toBeNull(); // must NOT have opened by itself
  });

  it("does not open while a mode switch is in flight", async () => {
    useStore.setState({ paperTrading: true, switchReconciling: true });
    render(<LiveConsentModal />);
    act(() => {
      useStore.setState({ requestLiveConsent: true });
    });
    await waitFor(() => expect(useStore.getState().requestLiveConsent).toBe(false));
    expect(modalIsOpen()).toBeNull();
  });

  it("opens on a normal go-live (paper, no switch in flight)", async () => {
    useStore.setState({ paperTrading: true, switchReconciling: false, modeSwitch: null });
    render(<LiveConsentModal />);
    act(() => {
      useStore.setState({ requestLiveConsent: true });
    });
    await screen.findByText(/enable live trading\?/i);
  });
});
