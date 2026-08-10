import { useEffect } from "react";
import { fetchRoundTableDecisions } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import { adaptRoundTableDecisions } from "./roundTable";

const POLL_MS = 30_000;

/**
 * Polls /round-table-decisions → store. Recursive setTimeout loop (not
 * setInterval), so a slow engine never stacks overlapping requests. Failed
 * polls keep the last value.
 */
export function useRoundTablePolling(): void {
  const setRoundTable = useStore((s) => s.setRoundTable);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (!alive) return;
      try {
        const resp = await fetchRoundTableDecisions();
        if (alive && resp) setRoundTable(adaptRoundTableDecisions(resp));
      } finally {
        if (alive) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [setRoundTable]);
}
