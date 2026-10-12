import { useEffect } from "react";
import { isDesktop, readAuditChain } from "@/lib/desktopBridge";
import { useStore } from "@/console/store/useStore";
import { adaptAuditEntries } from "./audit";

const POLL_MS = 10_000; // audit lines append per decision cycle; 10s is responsive
const MAX_LINES = 120;

/**
 * Polls the local hash-linked audit log via the desktop bridge → store audit
 * feed (G3d-2). Recursive setTimeout loop (not setInterval) so a slow disk read
 * never stacks overlapping calls — same robustness contract as the other
 * polling hooks. Desktop-only: in the cloud build there is no local file, so the
 * loop never starts and the feed stays empty (the page shows a desktop-only
 * note). Failed reads keep the last value.
 */
export function useAuditPolling(): void {
  const setAudit = useStore((s) => s.setAudit);
  useEffect(() => {
    if (!isDesktop()) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (!alive) return;
      try {
        const raw = await readAuditChain(MAX_LINES);
        if (alive) setAudit(adaptAuditEntries(raw));
      } finally {
        if (alive) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [setAudit]);
}
