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
import { render, screen, act, waitFor, fireEvent, within } from "@testing-library/react";
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
    getSetupState: vi.fn().mockResolvedValue({}),
    recordBrokerAuthorization: vi.fn(),
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
import * as bridge from "@/lib/desktopBridge";
import * as api from "@/lib/api";

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

// #5155: Alpaca's authorization prompt at the live switch — only when live keys are typed in the
// modal (that connects an account). It is written on the chain BEFORE validateAlpaca and liveEnable;
// anything but ALLOW stops with the modal open, inputs kept, no liveEnable and no restart.
describe("LiveConsentModal — Alpaca authorization prompt for typed live keys (#5155)", () => {
  const record = vi.mocked(bridge.recordBrokerAuthorization);
  const validate = vi.mocked(bridge.validateAlpaca);
  const save = vi.mocked(bridge.saveSecret);
  const restart = vi.mocked(bridge.restartEngine);
  const enable = vi.mocked(api.liveEnable);

  beforeEach(() => {
    record.mockReset().mockResolvedValue({ ok: true });
    validate.mockReset().mockResolvedValue({ ok: true, status: 200, equity: 50000 });
    save.mockReset().mockResolvedValue({ ok: true, error: null });
    restart.mockClear();
    enable.mockClear();
    useStore.setState({
      allowLive: true,
      paperTrading: true,
      switchReconciling: false,
      modeSwitch: null,
      requestLiveConsent: false,
      switchError: null,
    });
  });

  async function openAndEnable(keyId: string, secret: string) {
    render(<LiveConsentModal />);
    act(() => {
      useStore.setState({ requestLiveConsent: true });
    });
    await screen.findByText(/enable live trading\?/i);
    fireEvent.change(screen.getByLabelText("live-key-id"), { target: { value: keyId } });
    fireEvent.change(screen.getByLabelText("live-secret"), { target: { value: secret } });
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
  }

  it("typed keys: the prompt lies over the modal and is clickable; nothing validated while it is open", async () => {
    await openAndEnable("AKLIVEKEY9876", "livesecret");
    const dialog = await screen.findByRole("dialog");
    // Rendered inside the modal's portal AFTER its content (both z-50 → the later one is on top).
    const backdrop = screen.getByText(/enable live trading\?/i).closest(".fixed")!;
    expect(backdrop.contains(dialog)).toBe(true);
    expect(backdrop.lastElementChild!.contains(dialog)).toBe(true);
    expect(within(dialog).getByRole("button", { name: "ALLOW" })).not.toBeDisabled();
    expect(validate).not.toHaveBeenCalled();
    expect(enable).not.toHaveBeenCalled();
  });

  it("ALLOW: slot live, written before validateAlpaca and before liveEnable", async () => {
    await openAndEnable("AKLIVEKEY9876", "livesecret");
    fireEvent.click(await screen.findByRole("button", { name: "ALLOW" }));
    await waitFor(() => expect(enable).toHaveBeenCalled());
    expect(record).toHaveBeenCalledWith(expect.objectContaining({ answer: "allow", slot: "live", keyMasked: "••••9876" }));
    expect(JSON.stringify(record.mock.calls[0][0])).not.toContain("AKLIVEKEY9876");
    expect(record.mock.invocationCallOrder[0]).toBeLessThan(validate.mock.invocationCallOrder[0]);
    expect(record.mock.invocationCallOrder[0]).toBeLessThan(enable.mock.invocationCallOrder[0]);
    expect(save).toHaveBeenCalledWith("ALPACA_LIVE_API_KEY", "AKLIVEKEY9876");
  });

  it("DENY: no validateAlpaca, no liveEnable, no restart — modal open, inputs kept", async () => {
    await openAndEnable("AKLIVEKEY9876", "livesecret");
    fireEvent.click(await screen.findByRole("button", { name: "DENY" }));
    await waitFor(() => expect(record).toHaveBeenCalledWith(expect.objectContaining({ answer: "deny", slot: "live" })));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(validate).not.toHaveBeenCalled();
    expect(save).not.toHaveBeenCalled();
    expect(enable).not.toHaveBeenCalled();
    expect(restart).not.toHaveBeenCalled();
    expect(screen.getByText(/enable live trading\?/i)).toBeTruthy();
    expect((screen.getByLabelText("live-key-id") as HTMLInputElement).value).toBe("AKLIVEKEY9876");
    expect(screen.getByRole("button", { name: /enable live trading/i })).not.toBeDisabled();
  });

  it("a failed answer write is fail-closed: error says nothing was saved, no liveEnable", async () => {
    record.mockResolvedValue({ ok: false });
    await openAndEnable("AKLIVEKEY9876", "livesecret");
    fireEvent.click(await screen.findByRole("button", { name: "ALLOW" }));
    await waitFor(() => expect(useStore.getState().switchError).toMatch(/nothing was saved/i));
    expect(validate).not.toHaveBeenCalled();
    expect(enable).not.toHaveBeenCalled();
    expect(restart).not.toHaveBeenCalled();
    expect(screen.getByText(/enable live trading\?/i)).toBeTruthy();
  });

  it("no keys typed (stored live keys): no prompt — liveEnable as before", async () => {
    render(<LiveConsentModal />);
    act(() => {
      useStore.setState({ requestLiveConsent: true });
    });
    await screen.findByText(/enable live trading\?/i);
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    await waitFor(() => expect(screen.getByRole("button", { name: /enable live trading/i })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
    await waitFor(() => expect(enable).toHaveBeenCalled());
    expect(record).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
