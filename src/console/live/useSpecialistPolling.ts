import { useEffect } from "react";
import { fetchSpecialistReports } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import { adaptSpecialistReports } from "./specialist";

const POLL_MS = 60_000; // specialist research cycles are slow; 60s gap between polls

/**
 * Polls /specialist-reports → store specialist cards + status. Recursive
 * setTimeout loop (not setInterval), so a slow engine never stacks overlapping
 * requests (same robustness contract as useEquityPolling). Failed polls keep the
 * last value — `fetchSpecialistReports` returns `{status:"error", reports:[]}`
 * on a network error, which adapts to []; we keep the prior reports in that case
 * rather than flashing an empty state, but we always record the latest status so
 * the page can show an honest unavailable/empty message.
 */
export function useSpecialistPolling(): void {
  const setSpecialistReports = useStore((s) => s.setSpecialistReports);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (!alive) return;
      try {
        const resp = await fetchSpecialistReports();
        if (alive && resp) {
          const reports = adaptSpecialistReports(resp);
          // Keep the last good cards on a transient failure (error → empty
          // reports), but always surface the latest status string.
          if (reports.length > 0 || resp.status === "ok" || resp.status === "unavailable") {
            setSpecialistReports(reports, resp.status, resp.message);
          } else {
            // Transient error: refresh status only, keep the prior reports.
            useStore.setState({ specialistStatus: resp.status });
          }
        }
      } finally {
        if (alive) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [setSpecialistReports]);
}
