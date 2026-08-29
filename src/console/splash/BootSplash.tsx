import { useEffect } from "react";
import { useStore, selectDataReady } from "@/console/store/useStore";
import { useEngine } from "@/console/live/useEngine";
import { usePortfolioPolling } from "@/console/live/usePortfolioPolling";
import { useHealthPolling } from "@/console/live/useHealthPolling";
import { useBootPhase } from "./useBootPhase";
import { useTypewriter } from "./useTypewriter";
import { bootStatusLabel } from "./bootStatus";

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
export function BootSplash({ onDone }: { onDone: () => void }) {
  // Poll the portfolio AND /health while the splash is up so both `dataReady` (live equity) and
  // `engineServing` (engine actually serving, not "starting") can flip. Both hooks unmount with the
  // splash, so they never double-poll with the page-level polling that starts once the dashboard mounts.
  usePortfolioPolling();
  useHealthPolling();
  const dataReady = useStore(selectDataReady);
  const engineServing = useStore((s) => s.engineServing);
  const engine = useEngine();
  const engineErrored = engine.isDesktop && engine.status === "error";
  // Engine up and warming (spawned or port-bound) → don't let the boot timeout
  // misreport a slow cold start as "engine didn't start".
  const engineActive = engine.isDesktop && (engine.status === "starting" || engine.status === "running");
  // The pulse must stay until the app is FULLY operational — same philosophy as the Paper↔Live switch:
  // it is not enough that a first portfolio number arrived; on desktop the engine must actually be
  // SERVING (/health status !== "starting") so the dashboard renders live, not still-warming. In the
  // cloud/browser build there is no engine (engineServing stays false) → don't gate on it there.
  const operational = dataReady && (!engine.isDesktop || engineServing === true);
  // Anti-hang cap: generous on desktop so a legitimate cold start (engine spawn + model warm) is never
  // cut short before it is operational; short in cloud/browser where there is no engine to wait for. A
  // hard engine error still surfaces the retry UI immediately regardless (engineErrored path below).
  const SAFETY_REVEAL_MS = import.meta.env.DEV
    ? (engine.isDesktop ? 2000 : 1000)
    : (engine.isDesktop ? 60000 : 6000);
  const { phase, showStatus } = useBootPhase({ dataReady: operational, engineErrored, engineActive });
  const typed = useTypewriter("autonomous", 95);
  // Friendly, mapped phase text — never the raw engine log line (that reads as cryptic system
  // output). The raw log stays in Settings → Engine-Log and the error-state tail below.
  const statusText = bootStatusLabel(engine.logs);

  useEffect(() => {
    if (phase !== "ready") return;
    const t = setTimeout(onDone, 420); // let the fade play, then reveal the dashboard
    return () => clearTimeout(t);
  }, [phase, onDone]);

  // Safety net: never strand the user on the splash if the operational signal never flips.
  useEffect(() => {
    const t = setTimeout(onDone, SAFETY_REVEAL_MS);
    return () => clearTimeout(t);
  }, [onDone, SAFETY_REVEAL_MS]);

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
