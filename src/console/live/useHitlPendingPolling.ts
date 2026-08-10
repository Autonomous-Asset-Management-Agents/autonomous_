import { useEffect } from "react";
import { fetchHitlPending } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import { adaptHitlPending } from "./hitlQueue";

// Pending approvals are time-critical (HITL_EXPIRY_SECONDS default 900s — an unseen order
// simply expires), so poll on the same brisk 10s cadence as the open-orders book.
const POLL_MS = 10_000;

/**
 * Polls /api/hitl/pending (through the desktop-aware api layer) and writes the adapted
 * approval queue into the store. Recursive setTimeout (not setInterval) so a slow engine can
 * never stack overlapping requests; a failed poll is swallowed and the store keeps its last
 * value (#2660, Epic #2667).
 */
export function useHitlPendingPolling(): void {
  const setPendingApprovals = useStore((s) => s.setPendingApprovals);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const loop = async () => {
      if (!alive) return;
      try {
        const resp = await fetchHitlPending();
        if (alive && resp) setPendingApprovals(adaptHitlPending(resp));
      } catch {
        // keep last value — a flaky poll must never blank the operator's queue view
      } finally {
        if (alive) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();

    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [setPendingApprovals]);
}
