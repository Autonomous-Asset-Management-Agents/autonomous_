import { fetchHealth, postSwitchReconcile } from "@/lib/api";

/**
 * UXC-1 S3 (#3156) — the mode-switch reconcile, EXTRACTED from LiveConsentModal so
 * that the go-live confirm and the shared SettingsApplyModal run EXACTLY the same
 * code: poll /health past the boot window, settle the paper/live label from an
 * AUTHORITATIVE read, surface `live_enable_failed_reason` when live was expected —
 * never a silent paper fallback (epic review A4).
 */

export const RECONCILE_MAX_ATTEMPTS = 20;
// ~41.2 s total backoff window: fast early probes then a steady 3 s tail, long enough to
// cover a cold engine boot that answers /health "starting" the whole time.
export const RECONCILE_BACKOFF_MS = [
  200, 500, 1000, 1500, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 3000, 3000, 3000, 3000,
  3000, 3000, 3000, 3000,
];
// Per-call cap for each reconcile /health poll (bounds a single stalled /health).
export const RECONCILE_POLL_TIMEOUT_MS = 5000;

const sleep = (ms: number) => new Promise<void>((res) => setTimeout(res, ms));

export function emitReconcileTelemetry(payload: {
  switch_id?: string;
  attempts: number;
  settled_status?: string | null;
  paper_trading?: boolean | null;
}) {
  try {
    void postSwitchReconcile(payload);
  } catch {
    /* fail-open: telemetry never blocks a switch */
  }
}

export interface ReconcileDeps {
  /** false once the calling component unmounted — store writes stop, telemetry still fires. */
  isMounted: () => boolean;
  setPaperTrading: (paper: boolean) => void;
  setSwitchError: (msg: string | null) => void;
  setSwitchReconciling: (v: boolean) => void;
  setSwitchConfirmFailed: (v: boolean) => void;
  setEngineServing: (v: boolean) => void;
}

export async function reconcileEngineMode(
  expectingLive: boolean,
  switchId: string | undefined,
  deps: ReconcileDeps,
): Promise<void> {
  deps.setSwitchReconciling(true);
  deps.setSwitchConfirmFailed(false);
  try {
    for (let attempt = 0; attempt < RECONCILE_MAX_ATTEMPTS; attempt++) {
      const h = await fetchHealth(RECONCILE_POLL_TIMEOUT_MS);
      if (h && h.status !== "starting") {
        if (!deps.isMounted()) return;
        deps.setPaperTrading(h.paper_trading ?? true);
        if (expectingLive && h.paper_trading === true && h.live_enable_failed_reason) {
          deps.setSwitchError(`Live not enabled: ${h.live_enable_failed_reason}`);
        }
        emitReconcileTelemetry({
          switch_id: switchId,
          attempts: attempt + 1,
          settled_status: h.status ?? "healthy",
          paper_trading: h.paper_trading ?? true,
        });
        return;
      }
      if (attempt < RECONCILE_MAX_ATTEMPTS - 1) {
        await sleep(RECONCILE_BACKOFF_MS[attempt] ?? 800);
      }
    }
    if (deps.isMounted()) {
      deps.setSwitchConfirmFailed(true);
      deps.setEngineServing(false);
    }
    emitReconcileTelemetry({
      switch_id: switchId,
      attempts: RECONCILE_MAX_ATTEMPTS,
      settled_status: "unconfirmed",
      paper_trading: null,
    });
  } finally {
    if (deps.isMounted()) deps.setSwitchReconciling(false);
  }
}
