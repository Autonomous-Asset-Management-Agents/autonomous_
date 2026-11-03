import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * G4-1 (#1050): ConsoleApp's first-run gate. Desktop-only — if the OS keychain
 * has no secrets, the setup wizard replaces the console until setup completes.
 * Cloud (no bridge) skips the check entirely and renders the console.
 * G5-3b: a "Skip / explore in demo mode" path lets the user see the dashboard
 * before entering keys (the engine boots keyless via G5-3a).
 * B9: the keychain alone is not "setup done". The gate also reads setup.json and opens the console
 * only when the wizard wrote its own done-mark (setup_completed), or, for installs from before the
 * mark, when LLM_PROVIDER is present. Otherwise the wizard resumes with keysPresent so it can jump
 * to the first unfinished step.
 */
const isDesktop = vi.fn();
const hasBridge = vi.fn();
const hasKeychain = vi.fn();
const getSetupState = vi.fn();
const startEngine = vi.fn();
// B4: the engine-log stream the provisioning state (useProvisionStatus) follows. Replay is empty;
// tests push live lines through emitLog exactly as the shell streams them.
const logCbs = new Set<(line: string) => void>();
const emitLog = (line: string) => logCbs.forEach((cb) => cb(line));
vi.mock("@/lib/desktopBridge", () => ({
  isDesktop: () => isDesktop(),
  hasBridge: () => hasBridge(),
  hasKeychain: () => hasKeychain(),
  getSetupState: () => getSetupState(),
  startEngine: () => startEngine(),
  getEngineLogs: async () => [],
  onEngineLog: (cb: (line: string) => void) => {
    logCbs.add(cb);
    return () => {
      logCbs.delete(cb);
    };
  },
}));
vi.mock("@/console/desktop/DesktopApp", () => ({ DesktopApp: () => <div>DESKTOP-APP</div> }));
vi.mock("@/console/setup/SetupWizard", () => ({
  SetupWizard: ({
    onComplete,
    onSkip,
    keysPresent,
  }: {
    onComplete: () => void;
    onSkip?: () => void;
    keysPresent?: boolean;
  }) => (
    <div>
      SETUP-WIZARD
      <span>{keysPresent ? "KEYS-PRESENT" : "KEYS-ABSENT"}</span>
      <button onClick={onComplete}>wizard-complete</button>
      {onSkip ? <button onClick={onSkip}>skip-demo</button> : null}
    </div>
  ),
}));

const SETUP_DONE = { name: "Georg", LLM_PROVIDER: "ollama", setup_completed: true };
// The boot splash gates the dashboard; for the gate test it reveals immediately
// so the existing "reaches the dashboard" assertions hold. The splash's own
// behaviour is covered in consoleBoot.test.tsx.
vi.mock("@/console/splash/BootSplash", async () => {
  const { useEffect } = await import("react");
  return {
    BootSplash: ({ onDone, hold, provision }: { onDone: () => void; hold?: boolean; provision?: { error: string | null } }) => {
      useEffect(() => { if (!hold) onDone(); }, [onDone, hold]);
      return (
        <div>
          {hold ? "BOOT-SPLASH-HELD" : null}
          {provision?.error ? <p>PROVISION-ERROR: {provision.error}</p> : null}
        </div>
      );
    },
  };
});

import ConsoleApp from "../console/ConsoleApp";

describe("ConsoleApp first-run gate (G4-1)", () => {
  beforeEach(() => {
    isDesktop.mockReset().mockReturnValue(true);
    hasBridge.mockReset();
    hasKeychain.mockReset();
    getSetupState.mockReset().mockResolvedValue({});
    startEngine.mockReset().mockResolvedValue(undefined);
    logCbs.clear();
  });

  it("cloud build: renders the console, never the wizard, no keychain check", async () => {
    hasBridge.mockReturnValue(false);
    render(<ConsoleApp />);
    expect(await screen.findByText("DESKTOP-APP")).toBeTruthy();
    expect(screen.queryByText("SETUP-WIZARD")).toBeNull();
    expect(hasKeychain).not.toHaveBeenCalled();
    expect(getSetupState).not.toHaveBeenCalled();
  });

  it("desktop with secrets and a finished wizard: renders the console", async () => {
    hasBridge.mockReturnValue(true);
    hasKeychain.mockResolvedValue(true);
    getSetupState.mockResolvedValue(SETUP_DONE);
    render(<ConsoleApp />);
    expect(await screen.findByText("DESKTOP-APP")).toBeTruthy();
    expect(screen.queryByText("SETUP-WIZARD")).toBeNull();
  });

  it("desktop without secrets: renders the setup wizard (gates the console)", async () => {
    hasBridge.mockReturnValue(true);
    hasKeychain.mockResolvedValue(false);
    render(<ConsoleApp />);
    expect(await screen.findByText("SETUP-WIZARD")).toBeTruthy();
    expect(screen.getByText("KEYS-ABSENT")).toBeTruthy();
    expect(screen.queryByText("DESKTOP-APP")).toBeNull();
  });

  // B9: keys in the keychain, but setup.json holds only the name (the customer abandoned the model
  // step and restarted). Before: straight to the dashboard, LLM never configured. Now: the wizard,
  // told that the keys are there so it can resume on the model step.
  it("B9: desktop with secrets but an unfinished wizard: shows the wizard with keys present", async () => {
    hasBridge.mockReturnValue(true);
    hasKeychain.mockResolvedValue(true);
    getSetupState.mockResolvedValue({ name: "Georg" });
    render(<ConsoleApp />);
    expect(await screen.findByText("SETUP-WIZARD")).toBeTruthy();
    expect(screen.getByText("KEYS-PRESENT")).toBeTruthy();
    expect(screen.queryByText("DESKTOP-APP")).toBeNull();
  });

  it("B9: desktop with secrets and an empty setup.json: shows the wizard with keys present", async () => {
    hasBridge.mockReturnValue(true);
    hasKeychain.mockResolvedValue(true);
    getSetupState.mockResolvedValue({});
    render(<ConsoleApp />);
    expect(await screen.findByText("SETUP-WIZARD")).toBeTruthy();
    expect(screen.getByText("KEYS-PRESENT")).toBeTruthy();
  });

  it("B9 migration: an install from before the done-mark (LLM_PROVIDER set, no setup_completed) opens the console", async () => {
    hasBridge.mockReturnValue(true);
    hasKeychain.mockResolvedValue(true);
    getSetupState.mockResolvedValue({ name: "Georg", LLM_PROVIDER: "gemini" });
    render(<ConsoleApp />);
    expect(await screen.findByText("DESKTOP-APP")).toBeTruthy();
    expect(screen.queryByText("SETUP-WIZARD")).toBeNull();
  });

  it("B9: a done-mark without keys (keys removed later) still gates on the wizard", async () => {
    hasBridge.mockReturnValue(true);
    hasKeychain.mockResolvedValue(false);
    getSetupState.mockResolvedValue(SETUP_DONE);
    render(<ConsoleApp />);
    expect(await screen.findByText("SETUP-WIZARD")).toBeTruthy();
    expect(screen.getByText("KEYS-ABSENT")).toBeTruthy();
    expect(screen.queryByText("DESKTOP-APP")).toBeNull();
  });

  it("B9: an unreadable setup.json with secrets present falls safe to the wizard (keys present)", async () => {
    hasBridge.mockReturnValue(true);
    hasKeychain.mockResolvedValue(true);
    getSetupState.mockRejectedValue(new Error("ipc down"));
    render(<ConsoleApp />);
    expect(await screen.findByText("SETUP-WIZARD")).toBeTruthy();
    expect(screen.getByText("KEYS-PRESENT")).toBeTruthy();
  });

  it("B9: the wizard's own onComplete closes the gate", async () => {
    hasBridge.mockReturnValue(true);
    hasKeychain.mockResolvedValue(true);
    getSetupState.mockResolvedValue({ name: "Georg" });
    render(<ConsoleApp />);
    fireEvent.click(await screen.findByText("wizard-complete"));
    expect(await screen.findByText("DESKTOP-APP")).toBeTruthy();
    expect(screen.queryByText("SETUP-WIZARD")).toBeNull();
  });

  it("desktop while the keychain probe is pending: holds the splash, never the wizard or console", async () => {
    // First macOS launch: the shell holds keychain:has-secrets until the runtime download is done
    // (customer reports 2026-09-11). The renderer must show the download splash, not a black window
    // and not a wizard whose key save would fail. B9: setup.json resolving early changes nothing.
    hasBridge.mockReturnValue(true);
    hasKeychain.mockReturnValue(new Promise(() => {}));
    getSetupState.mockResolvedValue(SETUP_DONE);
    render(<ConsoleApp />);
    expect(await screen.findByText("BOOT-SPLASH-HELD")).toBeTruthy();
    expect(screen.queryByText("SETUP-WIZARD")).toBeNull();
    expect(screen.queryByText("DESKTOP-APP")).toBeNull();
  });

  it("desktop with a failing keychain check: falls safe to the wizard (not a blank screen)", async () => {
    hasBridge.mockReturnValue(true);
    hasKeychain.mockRejectedValue(new Error("ipc down"));
    render(<ConsoleApp />);
    expect(await screen.findByText("SETUP-WIZARD")).toBeTruthy();
  });

  it("B4: after a FAILED first-launch provisioning the splash stays (with the error), never the wizard", async () => {
    // main.cjs releases the keychain probe after the failure; a bare python3 then reports "no
    // secrets", which used to swap the honest download error for a wizard whose key save fails.
    hasBridge.mockReturnValue(true);
    hasKeychain.mockResolvedValue(false);
    render(<ConsoleApp />);
    act(() => emitLog("Provisioning failed: no macOS release for app version 0.5.1 (tried desktop-mac-v0.5.1-beta)"));
    expect(await screen.findByText("BOOT-SPLASH-HELD")).toBeTruthy();
    expect(screen.getByText(/PROVISION-ERROR: no macOS release for app version 0\.5\.1/)).toBeTruthy();
    await act(async () => {}); // let the keychain probe resolve
    expect(screen.queryByText("SETUP-WIZARD")).toBeNull();
    expect(screen.queryByText("DESKTOP-APP")).toBeNull();
  });

  it("B4: provisioning lines that carry no error leave the gate untouched", async () => {
    hasBridge.mockReturnValue(true);
    hasKeychain.mockResolvedValue(false);
    render(<ConsoleApp />);
    act(() => emitLog("[provisioning] 23% Python-Laufzeit"));
    expect(await screen.findByText("SETUP-WIZARD")).toBeTruthy();
  });

  it("G5-3b: skipping the wizard shows the dashboard in demo mode + starts the engine", async () => {
    hasBridge.mockReturnValue(true);
    hasKeychain.mockResolvedValue(false);
    render(<ConsoleApp />);
    fireEvent.click(await screen.findByText("skip-demo"));
    expect(await screen.findByText("DESKTOP-APP")).toBeTruthy();
    expect(screen.queryByText("SETUP-WIZARD")).toBeNull();
    expect(startEngine).toHaveBeenCalled(); // boots keyless (G5-3a)
    expect(screen.getByText(/finish setup/i)).toBeTruthy(); // a way back to setup remains
  });

  it("G5-3b: the demo banner's 'Finish setup' re-opens the wizard", async () => {
    hasBridge.mockReturnValue(true);
    hasKeychain.mockResolvedValue(false);
    render(<ConsoleApp />);
    fireEvent.click(await screen.findByText("skip-demo"));
    await screen.findByText("DESKTOP-APP");
    fireEvent.click(screen.getByText(/finish setup/i));
    expect(await screen.findByText("SETUP-WIZARD")).toBeTruthy();
  });
});
