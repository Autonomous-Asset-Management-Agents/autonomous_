import { useEffect, useState } from "react";
import { useStore } from "@/console/store/useStore";
import { useTypewriter } from "@/console/splash/useTypewriter";
import { onEngineLog } from "@/lib/desktopBridge";
import { switchStatusLabel } from "./switchStatus";

/**
 * Mode-switch screen (operator finding #8) — the calm face of a deliberate
 * Paper↔Live switch.
 *
 * When the operator flips Paper↔Live, LiveTradingSwitchCard restarts the engine so
 * it re-boots in the target mode. That restart takes up to ~60s to serve again on a
 * slow machine, during which /health refuses connections. The old behaviour let the
 * health-poll debounce flash the red "Backend Offline" screen — a deliberate,
 * expected switch looked like a crash. Operators must NOT panic when going live.
 *
 * This overlay replaces that moment with the SAME experience as a fresh boot,
 * grounded on the `autonomous` boot splash (BootSplash.tsx / the `aaa-boot` styles):
 *   1. black ground + a calm GREEN heartbeat (the machine's pulse coming back), and
 *   2. once the engine serves again (store.engineServing → true), the heartbeat
 *      resolves into the typed `autonomous` wordmark (reusing the boot typewriter),
 *      then fades to reveal the app — exactly like a fresh boot, so it stays coherent
 *      when the real BootSplash reappears on the next mount.
 *
 * No red, no AlertCircle, no "offline"/"error" — this is a reassuring restart, not a
 * failure. App.tsx suppresses /offline routing while `modeSwitch` is set; the honest
 * error only resumes AFTER this overlay clears (engine served, or the safety timeout
 * fired — a genuinely failed/timed-out switch).
 *
 * Styling note: the `aaa-boot*` classes live in console.css scoped under
 * `.aaa-console`, which is why this overlay wraps itself in that class. A switch is
 * only ever initiated from inside the console, so console.css is always loaded when
 * this renders; the class is not imported here to avoid leaking the console bundle's
 * CSS into the marketing pages.
 */

// Comfortably longer than the worst-case ~60s restart so a slow-but-real switch is
// never cut short. It is the honest fall-through: if the engine never comes back (a
// genuinely failed switch, or a renderer reload that lost the in-flight reconcile),
// give up here so App resumes normal /offline routing.
const SWITCH_TIMEOUT_MS = 90_000;
// After the wordmark has typed in, hold it a beat, then fade — same 420ms fade as
// BootSplash so the reveal feels identical to a fresh boot.
const REVEAL_HOLD_MS = 1_500;
const FADE_MS = 420;

export function ModeSwitchOverlay() {
  const modeSwitch = useStore((s) => s.modeSwitch);
  const setModeSwitch = useStore((s) => s.setModeSwitch);
  const engineServing = useStore((s) => s.engineServing);
  const paperTrading = useStore((s) => s.paperTrading);
  const switchReconciling = useStore((s) => s.switchReconciling);
  const lastSyncAt = useStore((s) => s.lastSyncAt);
  const syncedPaper = useStore((s) => s.syncedPaper);

  // Latch "arriving" only when the switch is FULLY settled — not merely when the engine answers
  // /health again. #2465: the pulse must cover the WHOLE transition (restart + reconcile) so no
  // intermediate "booting / reconfiguring / still-paper" state leaks through, and it reveals ONLY
  // the clean final account. Settled = the engine serves AND the reconcile has finished AND /health
  // reports the TARGET account (live → paper_trading:false, paper → true). A degraded/failed live
  // boot (account never flips to the target) stays under the pulse until the 90s honest timeout.
  // Latched so a later flicker can't drop the UI back to the heartbeat mid-reveal. Derived in render
  // (the React-blessed "derive state from a changing input" pattern) — no cascading-render effect.
  const [arriving, setArriving] = useState(false);
  const [fading, setFading] = useState(false);
  const targetPaper = modeSwitch?.to === "paper"; // switching → paper expects paper_trading true
  // Data-freshness guard: /health flipping to the target mode is NOT enough — the portfolio poll must
  // have re-synced the account snapshot (equity/positions/cash) for the TARGET account SINCE the switch
  // began (lastSyncAt > startedAt). Otherwise the reveal shows the OLD account's numbers (e.g. the live
  // €224 on the paper page) for a beat until the next poll lands. When startedAt is absent (a switch
  // persisted before this field existed, or a reload that lost it) we can't time it → don't block on it.
  //
  // Bug B (0.3.5): (lastSyncAt > startedAt) alone is account-AGNOSTIC — under engine-load starvation an
  // in-flight poll against the DYING old engine can resolve after startedAt and write the OLD account's
  // data, satisfying the gate with stale numbers (the reliably-reproduced "paper first, then live"). We
  // additionally require the synced snapshot to belong to the TARGET account (syncedPaper === targetPaper),
  // so a stale cross-account poll can never open the reveal. A genuinely stuck switch still falls through
  // the 90s honest timeout below.
  const startedAt = modeSwitch?.startedAt ?? null;
  const dataFresh =
    startedAt == null ||
    (lastSyncAt != null && lastSyncAt > startedAt && syncedPaper === targetPaper);
  const settled =
    engineServing === true && switchReconciling === false && paperTrading === targetPaper && dataFresh;
  if (modeSwitch && settled && !arriving) setArriving(true);

  // Reveal sequence: once arriving, let the wordmark type + settle, fade, then clear
  // the flag (which unmounts this overlay and reveals the live app underneath).
  useEffect(() => {
    if (!arriving) return;
    const hold = setTimeout(() => setFading(true), REVEAL_HOLD_MS);
    const done = setTimeout(() => setModeSwitch(null), REVEAL_HOLD_MS + FADE_MS);
    return () => {
      clearTimeout(hold);
      clearTimeout(done);
    };
  }, [arriving, setModeSwitch]);

  // Honest fall-through: a genuinely failed / timed-out switch clears the overlay so
  // App stops suppressing /offline and the real error surfaces on the next poll.
  useEffect(() => {
    const t = setTimeout(() => setModeSwitch(null), SWITCH_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [setModeSwitch]);

  // #3168: engine log lines that arrive from NOW ON. Deliberately the live stream only — the
  // buffered log (getEngineLogs / useEngine) still holds the PREVIOUS boot's lines, whose
  // "uvicorn running" would make the status jump straight to "almost ready…" the instant the
  // switch begins. Subscribing at mount means every line we see belongs to the new boot.
  const [bootLogs, setBootLogs] = useState<string[]>([]);
  useEffect(() => onEngineLog((line) => setBootLogs((prev) => [...prev, line].slice(-200))), []);

  // Only type the wordmark once the engine has arrived (empty string = no typing).
  const typed = useTypewriter(arriving ? "autonomous" : "", 95);

  if (!modeSwitch) return null;

  const toLive = modeSwitch.to === "live";
  const title = toLive ? "Switching to live trading" : "Switching to paper trading";

  // Dynamic status — the same "here's what's happening" the boot splash gives, instead of one
  // frozen line. The ladder lives in switchStatus.ts (pure, unit-tested): it reads the furthest
  // evidence available rather than the first failing check, and narrates the long engine restart
  // from the engine's own boot log — see #3168 for why the #2580 version showed only one message.
  const statusText = switchStatusLabel({
    arriving,
    engineServing,
    switchReconciling,
    paperTrading,
    targetPaper,
    dataFresh,
    bootLogs,
  });

  return (
    <div className="aaa-console" role="status" aria-live="polite" aria-label={title}>
      <div className={`aaa-boot${fading ? " aaa-boot--out" : ""}`}>
        <div className="aaa-boot__center">
          {arriving ? (
            <div className="aaa-boot__logo">
              {typed}
              <span className="aaa-boot__caret">_</span>
            </div>
          ) : (
            <div className="aaa-boot__pulsewrap" aria-hidden="true" data-testid="mode-switch-heartbeat">
              {/* Calm green ECG "heartbeat" — the machine's pulse coming back to life.
                  pathLength normalises the traveling-draw animation regardless of the
                  path's real geometry (see console.css / prefers-reduced-motion). */}
              <svg className="aaa-boot__pulse" viewBox="0 0 120 40" role="img" aria-label="engine heartbeat">
                <path pathLength={100} d="M0 20 H46 l5 -2 l4 -12 l5 26 l4 -14 l3 2 H120" />
              </svg>
            </div>
          )}
          <div className="aaa-boot__below">
            <div className="aaa-boot__status" style={{ opacity: 1 }}>{`› ${statusText}`}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
