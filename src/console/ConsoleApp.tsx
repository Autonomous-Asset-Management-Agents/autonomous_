import { useEffect, useState } from "react";
import "./console.css";
import { DesktopApp } from "./desktop/DesktopApp";
import { SetupWizard } from "./setup/SetupWizard";
import { BootSplash } from "./splash/BootSplash";
import { isDesktop, hasKeychain, startEngine } from "@/lib/desktopBridge";

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
 * G5-3b demo mode: from the wizard the user can "skip / explore in demo mode" —
 * the dashboard renders immediately (the engine boots keyless via G5-3a,
 * paper-only) with a persistent banner back to setup. Setup is NOT done, so the
 * gate still offers the wizard via "Finish setup".
 */
export default function ConsoleApp() {
  const [checked, setChecked] = useState(() => !isDesktop());
  const [needsSetup, setNeedsSetup] = useState(false);
  const [demoMode, setDemoMode] = useState(false);
  const [booted, setBooted] = useState(false);

  useEffect(() => {
    if (!isDesktop()) return; // cloud: already checked, no keychain probe
    let alive = true;
    void hasKeychain()
      .then((has) => {
        if (!alive) return;
        setNeedsSetup(!has);
        setChecked(true);
      })
      .catch(() => {
        if (!alive) return;
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

  const showWizard = needsSetup && !demoMode;
  return (
    <div className="aaa-console h-screen w-screen overflow-hidden">
      {!checked ? null : showWizard ? (
        <SetupWizard onComplete={() => setNeedsSetup(false)} onSkip={enterDemo} />
      ) : !booted ? (
        // Boot splash until real portfolio data has loaded — never flash the
        // empty store. The splash polls the portfolio while it's up and reveals
        // the dashboard once equity arrives (or a hard safety timeout).
        <BootSplash onDone={() => setBooted(true)} />
      ) : (
        <div className="relative h-full w-full">
          {needsSetup && demoMode ? (
            <div className="absolute top-0 inset-x-0 z-50 flex items-center justify-between gap-3 px-4 py-1.5 text-[12px] bg-[#00c27a]/12 border-b border-[#00c27a]/25 text-[#00c27a]">
              <span>Demo mode — paper only, no live trading. Add your keys to go live.</span>
              <button
                onClick={() => setDemoMode(false)}
                className="shrink-0 font-semibold underline underline-offset-2 hover:text-white"
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
