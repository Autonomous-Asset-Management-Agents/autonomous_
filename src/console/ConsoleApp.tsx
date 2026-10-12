import { useEffect, useState } from "react";
import "./console.css";
import { DesktopApp } from "./desktop/DesktopApp";
import { SetupWizard } from "./setup/SetupWizard";
import { BootSplash } from "./splash/BootSplash";
import { useProvisionStatus } from "./splash/useProvisionStatus";
import { getSetupState, hasBridge, hasKeychain, startEngine } from "@/lib/desktopBridge";
import { isSetupComplete } from "./setup/setupCompletion";

/**
 * Operator console entry (G3, #1050) — "one frontend for all editions".
 *
 * The same console renders in the cloud build and inside the Electron desktop
 * shell; engine calls are edition-switched at the API layer (desktopBridge +
 * api.ts). The dark base is scoped to `.aaa-console` (console.css) so it never
 * leaks into the cloud landing / marketing pages.
 *
 * G4-1 first-run gate (desktop only): the keychain check is async (IPC), so it
 * runs in an effect with a loading frame to avoid flashing the wizard. The
 * cloud build (no bridge) starts "checked" → straight to the console, no IPC.
 *
 * B9: the keychain alone is not "setup done". The gate also reads the non-secret setup.json and
 * opens the console only when the wizard wrote its own done-mark (see setup/setupCompletion.ts).
 * Otherwise the wizard is shown and told whether the keys are already there, so it resumes on the
 * first unfinished step (keys present: the model step) instead of the requirements screen.
 *
 * G5-3b demo mode: from the wizard the user can "skip / explore in demo mode" —
 * the dashboard renders immediately (the engine boots keyless via G5-3a,
 * paper-only) with a persistent banner back to setup. Setup is NOT done, so the
 * gate still offers the wizard via "Finish setup".
 */
// Stable no-op for the held splash: its reveal effects are disabled by `hold`, and a stable
// reference keeps them from re-arming on every render.
const HOLD_NOOP = () => {};

export default function ConsoleApp() {
  // #2647: gate the Electron-only setup wizard + keychain probe on ACTUAL bridge presence, not
  // isDesktop() (which is also true for a plain localhost browser → the dead-wizard trap). A bridge-less
  // browser is "already checked" and boots straight to the dashboard (config lives in .env.oss). Using
  // hasBridge() for `booted` also avoids the prod-build BootSplash freeze on a localhost browser.
  const [checked, setChecked] = useState(() => !hasBridge());
  const [needsSetup, setNeedsSetup] = useState(false);
  // B9: what the keychain probe said, handed to the wizard so it can skip the steps already done.
  const [keysPresent, setKeysPresent] = useState(false);
  const [demoMode, setDemoMode] = useState(false);
  const [booted, setBooted] = useState(() => !hasBridge() || !!import.meta.env.DEV);
  // B4: first-launch runtime provisioning (mac). Idle everywhere else (browser, Windows, a provisioned
  // Mac). A FAILED download keeps the splash up with the honest error instead of falling through to
  // the wizard, whose key save cannot work without a runtime (the shell releases the keychain probe
  // after the failure, and a bare python3 then reports "no secrets").
  const provision = useProvisionStatus();

  useEffect(() => {
    if (!hasBridge()) return; // no Electron bridge (plain browser) → no keychain probe, no wizard
    let alive = true;
    // Both probes run together; the gate waits for the slower one (on a first macOS launch the
    // shell holds the keychain probe until the runtime download is done, and the splash stays up).
    // An unreadable setup.json counts as "not finished": fail-safe towards offering setup, the same
    // way an unverifiable keychain does.
    void Promise.all([hasKeychain(), getSetupState().catch(() => ({}))])
      .then(([has, state]) => {
        if (!alive) return;
        setKeysPresent(has);
        setNeedsSetup(!has || !isSetupComplete(state));
        setChecked(true);
      })
      .catch(() => {
        if (!alive) return;
        setKeysPresent(false);
        setNeedsSetup(true); // fail-safe: an unverifiable keychain → offer setup
        setChecked(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  // Skip into demo: show the dashboard now and boot the engine keyless (G5-3a
  // injects ALPACA_API_KEY=offline_mode when the keychain is empty → paper boot).
  const enterDemo = () => {
    setDemoMode(true);
    void startEngine().catch(() => {});
  };

  const isSetupHash = typeof window !== "undefined" && window.location.hash.startsWith("#setup");
  const showWizard = (needsSetup && !demoMode) || isSetupHash;
  return (
    <div className="aaa-console h-screen w-screen overflow-hidden">
      {!checked || provision.error ? (
        // Keychain probe pending. On a first macOS launch the shell holds this probe until the runtime
        // download has finished (it then relaunches once), so show the splash with its download phase
        // instead of a black window — and never the wizard, whose key save would fail without a runtime.
        // A failed download stays here too: the splash reports it (B4).
        <BootSplash hold onDone={HOLD_NOOP} provision={provision} />
      ) : showWizard ? (
        <SetupWizard keysPresent={keysPresent} onComplete={() => setNeedsSetup(false)} onSkip={enterDemo} />
      ) : !booted ? (
        // Boot splash until real portfolio data has loaded — never flash the
        // empty store. The splash polls the portfolio while it's up and reveals
        // the dashboard once equity arrives (or a hard safety timeout).
        <BootSplash onDone={() => setBooted(true)} provision={provision} />
      ) : (
        <div className="relative h-full w-full">
          {/* UXC-1 S8 (#3177): passive green tint stays; copy is white — colour never carries state. */}
          {needsSetup && demoMode ? (
            <div className="absolute top-0 inset-x-0 z-50 flex items-center justify-between gap-3 px-4 py-1.5 text-[12px] bg-[#00c27a]/12 border-b border-[#00c27a]/25 text-white/90">
              <span>Demo mode — paper only, no live trading. Add your keys to go live.</span>
              <button
                onClick={() => setDemoMode(false)}
                className="shrink-0 font-semibold underline underline-offset-2 text-white hover:text-white/80"
              >
                Finish setup
              </button>
            </div>
          ) : null}
          <DesktopApp />
        </div>
      )}
    </div>
  );
}
