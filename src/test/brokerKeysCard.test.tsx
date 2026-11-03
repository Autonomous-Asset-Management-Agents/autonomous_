// #1402 / #1425 — in-app Alpaca key re-entry (desktop/OSS), PAPER and LIVE. Mirrors
// SetupWizard.submitAlpaca: validate against Alpaca → save BOTH keys to the OS keychain → offer an
// engine restart. Paper and live use SEPARATE keychain slots. Secrets masked, never logged.
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const isDesktop = vi.fn();
const validateAlpaca = vi.fn();
const saveSecret = vi.fn();
const restartEngine = vi.fn();
const getKeychainStatus = vi.fn();
vi.mock("@/lib/desktopBridge", () => ({
  isDesktop: () => isDesktop(),
  validateAlpaca: (k: string, s: string, live?: boolean) => validateAlpaca(k, s, live),
  saveSecret: (k: string, v: string) => saveSecret(k, v),
  restartEngine: () => restartEngine(),
  getKeychainStatus: () => getKeychainStatus(),
}));

import { BrokerKeysCard } from "../console/desktop/BrokerKeysCard";

function enter(k = "PKID", s = "SECRET", mode: "paper" | "live" = "paper") {
  // Key fields are always visible now (no Manage/Close accordion).
  fireEvent.change(screen.getByLabelText(`alpaca-${mode}-key-id`), { target: { value: k } });
  fireEvent.change(screen.getByLabelText(`alpaca-${mode}-secret`), { target: { value: s } });
  fireEvent.click(screen.getByRole("button", { name: /validate & save/i }));
}

describe("BrokerKeysCard (#1402/#1425 — in-app Alpaca key re-entry)", () => {
  beforeEach(() => {
    isDesktop.mockReset().mockReturnValue(true);
    validateAlpaca.mockReset();
    saveSecret.mockReset().mockResolvedValue({ ok: true });
    restartEngine.mockReset().mockResolvedValue(undefined);
    getKeychainStatus.mockReset().mockResolvedValue({});
  });

  it("cloud build: renders nothing (Enterprise manages creds via GCP)", () => {
    isDesktop.mockReturnValue(false);
    const { container } = render(<BrokerKeysCard />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the key fields directly — no Manage/Close accordion", () => {
    render(<BrokerKeysCard />);
    // Fields are visible immediately, no toggle needed
    expect(screen.getByLabelText("alpaca-paper-key-id")).toBeTruthy();
    expect(screen.getByLabelText("alpaca-paper-secret")).toBeTruthy();
    // The collapse controls are gone
    expect(screen.queryByRole("button", { name: "Manage" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
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
    expect((screen.getByLabelText("alpaca-paper-secret") as HTMLInputElement).type).toBe("password");
  });

  it("restart after save uses the atomic engine restart (INC-1b)", async () => {
    validateAlpaca.mockResolvedValue({ ok: true, status: 200 });
    render(<BrokerKeysCard />);
    enter();
    fireEvent.click(await screen.findByRole("button", { name: /restart engine/i }));
    await waitFor(() => expect(restartEngine).toHaveBeenCalled());
  });

  it("INC-5/F5: shows a 'Keys stored' badge when the paper slot is already set", async () => {
    getKeychainStatus.mockResolvedValue({ ALPACA_API_KEY: true });
    render(<BrokerKeysCard mode="paper" />);
    await waitFor(() => expect(screen.getByText(/keys stored/i)).toBeTruthy());
  });

  it("INC-5/F5: shows NO badge when the slot is empty (fresh install / missing keys)", async () => {
    getKeychainStatus.mockResolvedValue({ ALPACA_API_KEY: false });
    render(<BrokerKeysCard mode="paper" />);
    await waitFor(() => expect(getKeychainStatus).toHaveBeenCalled());
    expect(screen.queryByText(/keys stored/i)).toBeNull();
  });

  it("INC-5/F5: the live card reads the SEPARATE live slot (ALPACA_LIVE_API_KEY)", async () => {
    // A set paper slot must NOT light the live badge — the two accounts are stored separately.
    getKeychainStatus.mockResolvedValue({ ALPACA_API_KEY: true, ALPACA_LIVE_API_KEY: false });
    render(<BrokerKeysCard mode="live" />);
    await waitFor(() => expect(getKeychainStatus).toHaveBeenCalled());
    expect(screen.queryByText(/keys stored/i)).toBeNull();
  });

  it("INC-5/F5: a keychain-status failure degrades silently (no badge, no crash)", async () => {
    getKeychainStatus.mockRejectedValue(new Error("bridge down"));
    render(<BrokerKeysCard mode="paper" />);
    await waitFor(() => expect(getKeychainStatus).toHaveBeenCalled());
    expect(screen.queryByText(/keys stored/i)).toBeNull();
    // the form still renders (card is not broken by the status probe)
    expect(screen.getByLabelText("alpaca-paper-key-id")).toBeTruthy();
  });

  // #2715: the wording changed from "leave blank to keep" to "re-enter to replace". This form's
  // only job is to SAVE credentials, so a blank submit is correctly refused — the old wording
  // promised a blank-submit path the button forbids (the same contradiction that, in
  // LlmProviderCard, actually blocked switching to a cloud LLM provider). Copy only; the guard is
  // unchanged and pinned by the test below.
  it("presence marker: shows a masked 'stored' placeholder on BOTH inputs when the slot is set — never the real value", async () => {
    getKeychainStatus.mockResolvedValue({ ALPACA_API_KEY: true });
    render(<BrokerKeysCard mode="paper" />);
    const keyId = (await screen.findByLabelText("alpaca-paper-key-id")) as HTMLInputElement;
    const secret = screen.getByLabelText("alpaca-paper-secret") as HTMLInputElement;
    await waitFor(() => expect(keyId.placeholder).toMatch(/stored \(re-enter to replace\)/i));
    // masked bullets marker on BOTH inputs
    expect(keyId.placeholder).toContain("•");
    expect(secret.placeholder).toMatch(/stored \(re-enter to replace\)/i);
    expect(secret.placeholder).toContain("•");
    // purely visual — the real key value is NEVER placed into either field
    expect(keyId.value).toBe("");
    expect(secret.value).toBe("");
    // …and the promise the old wording made must NOT be honoured here: a blank submit stays
    // refused, so the #2715 fix can never leak across and write empty broker credentials.
    const submit = screen.getByRole("button", { name: /validate & save/i }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });

  it("presence marker: NO masked placeholder when the slot is empty (fresh install)", async () => {
    getKeychainStatus.mockResolvedValue({ ALPACA_API_KEY: false });
    render(<BrokerKeysCard mode="paper" />);
    await waitFor(() => expect(getKeychainStatus).toHaveBeenCalled());
    const keyId = screen.getByLabelText("alpaca-paper-key-id") as HTMLInputElement;
    const secret = screen.getByLabelText("alpaca-paper-secret") as HTMLInputElement;
    expect(keyId.placeholder).not.toMatch(/stored/i);
    expect(secret.placeholder).not.toMatch(/stored/i);
  });

  it("presence marker: the live card reads the SEPARATE live slot for the placeholder", async () => {
    // paper slot set must NOT put the marker on the LIVE inputs (separate accounts).
    getKeychainStatus.mockResolvedValue({ ALPACA_API_KEY: true, ALPACA_LIVE_API_KEY: false });
    render(<BrokerKeysCard mode="live" />);
    await waitFor(() => expect(getKeychainStatus).toHaveBeenCalled());
    const keyId = screen.getByLabelText("alpaca-live-key-id") as HTMLInputElement;
    expect(keyId.placeholder).not.toMatch(/stored/i);
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
