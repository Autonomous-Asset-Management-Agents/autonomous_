import { useEffect } from "react";
import { fetchActivities } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import { adaptActivities } from "./activities";

// Fills are append-only and change slowly, and /activities pages the full
// history server-side, so a gentle 60s cadence keeps the view fresh without
// hammering the broker.
const POLL_MS = 60_000;

/**
 * Polls /activities (through the desktop-aware api layer) and writes the adapted
 * FULL fill history + the `truncated` flag into the store. Recursive setTimeout
 * (not setInterval) so a slow engine can never stack overlapping requests; a
 * failed poll is swallowed and the store keeps its last value.
 */
export function useActivitiesPolling(): void {
  const setActivities = useStore((s) => s.setActivities);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const loop = async () => {
      if (!alive) return;
      try {
        const resp = await fetchActivities();
        if (alive && resp) setActivities(adaptActivities(resp), resp.truncated);
      } finally {
        if (alive) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();

    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [setActivities]);
}
