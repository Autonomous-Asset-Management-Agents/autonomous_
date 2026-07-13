// #1402 / #1425 — in-app Alpaca key re-entry (desktop/OSS), PAPER and LIVE. Mirrors
// SetupWizard.submitAlpaca: validate against Alpaca → save BOTH keys to the OS keychain → offer an
// engine restart. Paper and live use SEPARATE keychain slots. Secrets masked, never logged.
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const isDesktop = vi.fn();
const validateAlpaca = vi.fn();
const saveSecret = vi.fn();
const startEngine = vi.fn();
const stopEngine = vi.fn();
vi.mock("@/lib/desktopBridge", () => ({
  isDesktop: () => isDesktop(),
  validateAlpaca: (k: string, s: string, live?: boolean) => validateAlpaca(k, s, live),
  saveSecret: (k: string, v: string) => saveSecret(k, v),
  startEngine: () => startEngine(),
  stopEngine: () => stopEngine(),
}));

import { BrokerKeysCard } from "../console/desktop/BrokerKeysCard";

function enter(k = "PKID", s = "SECRET", mode: "paper" | "live" = "paper") {
  const manageBtn = screen.queryByRole("button", { name: "Manage" });
  if (manageBtn) fireEvent.click(manageBtn);

  fireEvent.change(screen.getByLabelText(`alpaca-${mode}-key-id`), { target: { value: k } });
  fireEvent.change(screen.getByLabelText(`alpaca-${mode}-secret`), { target: { value: s } });
  fireEvent.click(screen.getByRole("button", { name: /validate & save/i }));
}

describe("BrokerKeysCard (#1402/#1425 — in-app Alpaca key re-entry)", () => {
  beforeEach(() => {
    isDesktop.mockReset().mockReturnValue(true);
    validateAlpaca.mockReset();
    saveSecret.mockReset().mockResolvedValue({ ok: true });
    startEngine.mockReset().mockResolvedValue(undefined);
    stopEngine.mockReset().mockResolvedValue(undefined);
  });

  it("cloud build: renders nothing (Enterprise manages creds via GCP)", () => {
    isDesktop.mockReturnValue(false);
    const { container } = render(<BrokerKeysCard />);
    expect(container.firstChild).toBeNull();
  });

  it("progressive disclosure: defaults to collapsed, toggles on 'Manage'", () => {
    render(<BrokerKeysCard />);
    // Inputs are hidden initially
    expect(screen.queryByLabelText("alpaca-paper-key-id")).toBeNull();
    // Click Manage
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    // Inputs should be visible
    expect(screen.getByLabelText("alpaca-paper-key-id")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
    // Click Close
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByLabelText("alpaca-paper-key-id")).toBeNull();
  });

  it("paper: validates (live=false), saves BOTH paper keys, offers restart", async () => {
    validateAlpaca.mockResolvedValue({ ok: true, status: 200 });
    render(<BrokerKeysCard mode="paper" />);
    enter("PKID", "SECRET");
    await waitFor(() => expect(validateAlpaca).toHaveBeenCalledWith("PKID", "SECRET", false));
    expect(saveSecret).toHaveBeenCalledWith("ALPACA_API_KEY", "PKID");
    expect(saveSecret).toHaveBeenCalledWith("ALPACA_SECRET_KEY", "SECRET");
    expect(screen.getByRole("button", { name: /restart engine/i })).toBeTruthy();
  });

  it("live: validates against the LIVE API and saves to the SEPARATE live slots", async () => {
    validateAlpaca.mockResolvedValue({ ok: true, status: 200 });
    render(<BrokerKeysCard mode="live" />);
    enter("LKID", "LSEC", "live");
    await waitFor(() => expect(validateAlpaca).toHaveBeenCalledWith("LKID", "LSEC", true));
    expect(saveSecret).toHaveBeenCalledWith("ALPACA_LIVE_API_KEY", "LKID");
    expect(saveSecret).toHaveBeenCalledWith("ALPACA_LIVE_SECRET_KEY", "LSEC");
    // live card is clearly marked + warns it does not start live trading
    expect(screen.getByText(/real money/i)).toBeTruthy();
  });

  it("invalid keys: shows the HTTP error and never saves", async () => {
    validateAlpaca.mockResolvedValue({ ok: false, status: 401 });
    render(<BrokerKeysCard />);
    enter();
    await waitFor(() => expect(screen.getByText(/rejected these keys \(HTTP 401\)/i)).toBeTruthy());
    expect(saveSecret).not.toHaveBeenCalled();
  });

  it("unreachable Alpaca (status 0): shows a connection error, never saves", async () => {
    validateAlpaca.mockResolvedValue({ ok: false, status: 0 });
    render(<BrokerKeysCard />);
    enter();
    await waitFor(() => expect(screen.getByText(/couldn't reach alpaca/i)).toBeTruthy());
    expect(saveSecret).not.toHaveBeenCalled();
  });

  it("keychain save failure surfaces an error", async () => {
    validateAlpaca.mockResolvedValue({ ok: true, status: 200 });
    saveSecret.mockResolvedValue({ ok: false });
    render(<BrokerKeysCard />);
    enter();
    await waitFor(() => expect(screen.getByText(/saving to the keychain failed/i)).toBeTruthy());
  });

  it("the secret input is masked (type=password)", () => {
    render(<BrokerKeysCard />);
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    expect((screen.getByLabelText("alpaca-paper-secret") as HTMLInputElement).type).toBe("password");
  });

  it("restart after save stops then starts the engine", async () => {
    validateAlpaca.mockResolvedValue({ ok: true, status: 200 });
    render(<BrokerKeysCard />);
    enter();
    fireEvent.click(await screen.findByRole("button", { name: /restart engine/i }));
    await waitFor(() => expect(startEngine).toHaveBeenCalled());
    expect(stopEngine).toHaveBeenCalled();
  });

  it("a bridge/IPC rejection surfaces an error and never leaves a stuck button", async () => {
    validateAlpaca.mockRejectedValue(new Error("ipc down"));
    render(<BrokerKeysCard />);
    enter();
    await waitFor(() => expect(screen.getByText(/something went wrong/i)).toBeTruthy());
    expect(saveSecret).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /validate & save/i })).not.toBeDisabled();
  });
});
