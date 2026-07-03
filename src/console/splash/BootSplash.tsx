import { useEffect } from "react";
import { useStore, selectDataReady } from "@/console/store/useStore";
import { useEngine } from "@/console/live/useEngine";
import { usePortfolioPolling } from "@/console/live/usePortfolioPolling";
import { useBootPhase } from "./useBootPhase";
import { useTypewriter } from "./useTypewriter";

/**
 * Full-screen boot splash (#1050) — faithful UX re-port of the bundle splash.
 * Types the `autonomous_` wordmark on black; a faint status line appears only on
 * a slow boot; on engine failure it shows an honest error/retry state — never a
 * mock dashboard. Calls `onDone` once boot completes (after a short fade).
 *
 * Two adaptations for main: (1) the bundle's "View logs" button used
 * engine.fetchLogs(), which main's useEngine doesn't expose (logs stream live),
 * so only Retry is shown and the tail is rendered inline; (2) a hard safety
 * reveal guarantees the splash never hangs even if the data-ready signal never
 * arrives (main polls per-page, not via the bundle's global live-data hook).
 */
const SAFETY_REVEAL_MS = 6000;

export function BootSplash({ onDone }: { onDone: () => void }) {
  // Poll the portfolio while the splash is up so `dataReady` (live equity) can
  // flip; this hook unmounts with the splash, so it never double-polls with the
  // page-level polling that starts once the dashboard mounts.
  usePortfolioPolling();
  const dataReady = useStore(selectDataReady);
  const engine = useEngine();
  const engineErrored = engine.isDesktop && engine.status === "error";
  // Engine up and warming (spawned or port-bound) → don't let the boot timeout
  // misreport a slow cold start as "engine didn't start".
  const engineActive = engine.isDesktop && (engine.status === "starting" || engine.status === "running");
  const { phase, showStatus } = useBootPhase({ dataReady, engineErrored, engineActive });
  const typed = useTypewriter("autonomous", 95);
  const statusText = engine.logs.length ? engine.logs[engine.logs.length - 1] : "starting engine…";

  useEffect(() => {
    if (phase !== "ready") return;
    const t = setTimeout(onDone, 420); // let the fade play, then reveal the dashboard
    return () => clearTimeout(t);
  }, [phase, onDone]);

  // Safety net: never strand the user on the splash if data-ready never flips.
  useEffect(() => {
    const t = setTimeout(onDone, SAFETY_REVEAL_MS);
    return () => clearTimeout(t);
  }, [onDone]);

  const errored = phase === "error";
  return (
    <div className={`aaa-boot${phase === "ready" ? " aaa-boot--out" : ""}`} role="status" aria-live="polite">
      {/* The wordmark is anchored at screen centre and never moves; everything
          variable hangs in __below so boot ↔ error keep it in place. */}
      <div className="aaa-boot__center">
        <div className="aaa-boot__logo">
          {errored ? "autonomous" : typed}
          <span className={`aaa-boot__caret${errored ? " aaa-boot__caret--err" : ""}`}>_</span>
        </div>
        <div className="aaa-boot__below">
          {errored ? (
            <div className="aaa-boot__err">
              <p className="aaa-boot__msg">engine didn't start</p>
              <div className="aaa-boot__actions">
                <button className="aaa-boot__btn" onClick={() => { if (engine.isDesktop) void engine.start(); }}>
                  Retry
                </button>
              </div>
              {engine.logs.length > 0 && <pre className="aaa-boot__logs">{engine.logs.slice(-6).join("\n")}</pre>}
            </div>
          ) : (
            <div className="aaa-boot__status" style={{ opacity: showStatus ? 1 : 0 }}>{`› ${statusText}`}</div>
          )}
        </div>
      </div>
    </div>
  );
}
