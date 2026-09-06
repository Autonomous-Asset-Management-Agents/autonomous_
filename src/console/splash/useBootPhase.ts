import { useEffect, useState } from "react";

export type BootPhase = "booting" | "ready" | "error";

interface Args {
  /** Real portfolio data has loaded (engine reachable + live equity merged). */
  dataReady: boolean;
  /** The engine reported a hard error (desktop only). */
  engineErrored: boolean;
  /**
   * The engine is actively starting or running (desktop only). When true, the
   * boot timeout does NOT trip the error state — the engine is up and merely
   * warming the universe (which can exceed the timeout), so "engine didn't
   * start" would be a false alarm. Only a hard engineErrored, or a timeout while
   * the engine is genuinely inactive, surfaces the error/retry UI.
   */
  engineActive?: boolean;
  /** Minimum time the splash stays up so the typing animation always finishes. */
  minMs?: number;
  /** Reveal the status line if boot is still going after this long. */
  statusAfterMs?: number;
  /**
   * Give up and show the error state if no data has arrived by now. Generous so
   * a legitimate cold start isn't misreported as a failure; a truly dead engine
   * still surfaces error/retry. Ignored while engineActive is true (see above).
   */
  timeoutMs?: number;
}

/**
 * Drives the boot splash: `booting` → `ready` (once real data is in AND the
 * minimum display time has elapsed) or `error` (engine error, or timeout with
 * no data). `showStatus` turns on a faint status line only on a slow boot.
 * `ready` always wins over `error` so late-arriving data still reveals the app.
 */
export function useBootPhase({
  dataReady,
  engineErrored,
  engineActive = false,
  minMs = 1200,
  statusAfterMs = 2000,
  timeoutMs = 120000,
}: Args): { phase: BootPhase; showStatus: boolean } {
  const [minElapsed, setMinElapsed] = useState(false);
  const [showStatusRaw, setShowStatusRaw] = useState(false);
  const [timedOut, setTimedOut] = useState(false);

  useEffect(() => {
    const a = setTimeout(() => setMinElapsed(true), minMs);
    const b = setTimeout(() => setShowStatusRaw(true), statusAfterMs);
    const c = setTimeout(() => setTimedOut(true), timeoutMs);
    return () => {
      clearTimeout(a);
      clearTimeout(b);
      clearTimeout(c);
    };
  }, [minMs, statusAfterMs, timeoutMs]);

  // A hard engine error always wins. A timeout only errors when the engine is
  // NOT actively starting/running — if it's up and merely warming, keep booting
  // (no false "engine didn't start"). `ready` still beats everything.
  const phase: BootPhase =
    dataReady && minElapsed
      ? "ready"
      : engineErrored || (timedOut && !engineActive)
        ? "error"
        : "booting";

  return { phase, showStatus: phase === "booting" && showStatusRaw };
}
