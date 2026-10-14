import { useEffect, useState } from "react";
import { hasBridge, getEngineLogs, onEngineLog } from "@/lib/desktopBridge";
import { applyProvisionLine, sameProvisionStatus, PROVISION_IDLE, type ProvisionStatus } from "./bootStatus";

/** No progress line for this long while provisioning is active → the phase is reported as stalled. The
 *  shell re-emits its last tick every ~500 ms (provision.cjs heartbeat) even during a long tar / SHA
 *  step, so 90 s of silence means the main process is no longer provisioning, not a slow step. */
export const PROVISION_STALL_MS = 90_000;
export const PROVISION_STALL_ERROR = "no progress reported for 90 seconds, the download may have stalled";

/**
 * First-launch runtime provisioning state for ConsoleApp / BootSplash (mac, B4). Follows the shell's
 * engine-log stream exactly like useEngine (replay of the buffered lines on mount, then live lines), but
 * keeps only the folded ProvisionStatus and publishes a new object solely when it changes, so the app
 * root does not re-render on every log line. Inert without the bridge (browser build) and on Windows,
 * where no provisioning line ever appears.
 */
export function useProvisionStatus(): ProvisionStatus {
  const [status, setStatus] = useState<ProvisionStatus>(PROVISION_IDLE);

  useEffect(() => {
    if (!hasBridge()) return;
    let cur: ProvisionStatus = PROVISION_IDLE;
    let stall: ReturnType<typeof setTimeout> | undefined;
    let replayed = false;
    const pending: string[] = [];

    const publish = (next: ProvisionStatus) => {
      if (sameProvisionStatus(cur, next)) return;
      cur = next;
      setStatus(next);
    };
    // Every provisioning line re-arms the stall watchdog; a finished or failed phase expects nothing more.
    const armStall = () => {
      if (stall) clearTimeout(stall);
      stall = undefined;
      if (!cur.active) return;
      stall = setTimeout(() => publish({ ...cur, active: false, error: PROVISION_STALL_ERROR }), PROVISION_STALL_MS);
    };
    const ingest = (next: ProvisionStatus) => {
      publish(next);
      armStall();
    };

    const unsub = onEngineLog((line) => {
      if (!replayed) {
        pending.push(line); // applied on top of the replay, so an older snapshot never wins over a live line
        return;
      }
      const next = applyProvisionLine(cur, line);
      if (next !== cur) ingest(next);
    });
    const finishReplay = (lines: readonly string[]) => {
      replayed = true;
      const replay = lines.reduce(applyProvisionLine, PROVISION_IDLE);
      const next = pending.splice(0).reduce(applyProvisionLine, replay);
      if (next !== PROVISION_IDLE) ingest(next);
    };
    void getEngineLogs().then(finishReplay, () => finishReplay([]));

    return () => {
      unsub();
      if (stall) clearTimeout(stall);
    };
  }, []);

  return status;
}
