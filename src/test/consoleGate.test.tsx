import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * G4-1 (#1050): ConsoleApp's first-run gate. Desktop-only — if the OS keychain
 * has no secrets, the setup wizard replaces the console until setup completes.
 * Cloud (no bridge) skips the check entirely and renders the console.
 * G5-3b: a "Skip / explore in demo mode" path lets the user see the dashboard
 * before entering keys (the engine boots keyless via G5-3a).
 */
const isDesktop = vi.fn();
const hasKeychain = vi.fn();
const startEngine = vi.fn();
vi.mock("@/lib/desktopBridge", () => ({
  isDesktop: () => isDesktop(),
  hasKeychain: () => hasKeychain(),
  startEngine: () => startEngine(),
}));
vi.mock("@/console/desktop/DesktopApp", () => ({ DesktopApp: () => <div>DESKTOP-APP</div> }));
vi.mock("@/console/setup/SetupWizard", () => ({
  SetupWizard: ({ onSkip }: { onSkip?: () => void }) => (
    <div>SETUP-WIZARD{onSkip ? <button onClick={onSkip}>skip-demo</button> : null}</div>
  ),
}));
// The boot splash gates the dashboard; for the gate test it reveals immediately
// so the existing "reaches the dashboard" assertions hold. The splash's own
// behaviour is covered in consoleBoot.test.tsx.
vi.mock("@/console/splash/BootSplash", async () => {
  const { useEffect } = await import("react");
  return { BootSplash: ({ onDone }: { onDone: () => void }) => { useEffect(() => onDone(), [onDone]); return null; } };
});

import ConsoleApp from "../console/ConsoleApp";

describe("ConsoleApp first-run gate (G4-1)", () => {
  beforeEach(() => {
    isDesktop.mockReset();
    hasKeychain.mockReset();
    startEngine.mockReset().mockResolvedValue(undefined);
  });

  it("cloud build: renders the console, never the wizard, no keychain check", async () => {
    isDesktop.mockReturnValue(false);
    render(<ConsoleApp />);
    expect(await screen.findByText("DESKTOP-APP")).toBeTruthy();
    expect(screen.queryByText("SETUP-WIZARD")).toBeNull();
    expect(hasKeychain).not.toHaveBeenCalled();
  });

  it("desktop with secrets: renders the console", async () => {
    isDesktop.mockReturnValue(true);
    hasKeychain.mockResolvedValue(true);
    render(<ConsoleApp />);
    expect(await screen.findByText("DESKTOP-APP")).toBeTruthy();
    expect(screen.queryByText("SETUP-WIZARD")).toBeNull();
  });

  it("desktop without secrets: renders the setup wizard (gates the console)", async () => {
    isDesktop.mockReturnValue(true);
    hasKeychain.mockResolvedValue(false);
    render(<ConsoleApp />);
    expect(await screen.findByText("SETUP-WIZARD")).toBeTruthy();
    expect(screen.queryByText("DESKTOP-APP")).toBeNull();
  });

  it("desktop with a failing keychain check: falls safe to the wizard (not a blank screen)", async () => {
    isDesktop.mockReturnValue(true);
    hasKeychain.mockRejectedValue(new Error("ipc down"));
    render(<ConsoleApp />);
    expect(await screen.findByText("SETUP-WIZARD")).toBeTruthy();
  });

  it("G5-3b: skipping the wizard shows the dashboard in demo mode + starts the engine", async () => {
    isDesktop.mockReturnValue(true);
    hasKeychain.mockResolvedValue(false);
    render(<ConsoleApp />);
    fireEvent.click(await screen.findByText("skip-demo"));
    expect(await screen.findByText("DESKTOP-APP")).toBeTruthy();
    expect(screen.queryByText("SETUP-WIZARD")).toBeNull();
    expect(startEngine).toHaveBeenCalled(); // boots keyless (G5-3a)
    expect(screen.getByText(/finish setup/i)).toBeTruthy(); // a way back to setup remains
  });

  it("G5-3b: the demo banner's 'Finish setup' re-opens the wizard", async () => {
    isDesktop.mockReturnValue(true);
    hasKeychain.mockResolvedValue(false);
    render(<ConsoleApp />);
    fireEvent.click(await screen.findByText("skip-demo"));
    await screen.findByText("DESKTOP-APP");
    fireEvent.click(screen.getByText(/finish setup/i));
    expect(await screen.findByText("SETUP-WIZARD")).toBeTruthy();
  });
});
