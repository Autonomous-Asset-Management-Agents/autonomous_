import { useCallback, useEffect } from "react";
import { fetchEntitlementStatus } from "@/lib/api";
import { useStore } from "@/console/store/useStore";

const POLL_MS = 30_000; // gap BETWEEN polls — the tier changes rarely (only on a claim).

/**
 * Polls /api/entitlement/status and writes the resolved tier + `can_upgrade`
 * into the store, for the sidebar Upgrade CTA (GTM-1 #1915). Same robustness
 * contract as useHealthPolling: a recursive setTimeout loop schedules the next
 * poll only after the previous settles, and a failed poll is swallowed (keeps
 * the last value; in the cloud/browser build there is no engine → stays null →
 * no CTA).
 *
 * Returns `refetch()` so the CTA can re-poll immediately after a claim succeeds
 * (Senior now → the store flips canUpgrade to false → the CTA hides).
 */
export function useEntitlementPolling(): { refetch: () => Promise<void> } {
  const setEntitlement = useStore((s) => s.setEntitlement);

  const refetch = useCallback(async () => {
    let s = await fetchEntitlementStatus();
    if (import.meta.env.DEV && import.meta.env.MODE !== "test") {
      const currentStoreTier = useStore.getState().tier;
      if (currentStoreTier === "BASIC" || !currentStoreTier) {
        s = { tier: "BASIC", allow_live: false, can_upgrade: true, simulation_enabled: false };
      }
    }
    if (s) setEntitlement(s.tier, s.can_upgrade, s.simulation_enabled ?? false, s.allow_live ?? false);
  }, [setEntitlement]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (!alive) return;
      try {
        let s = await fetchEntitlementStatus();
        if (import.meta.env.DEV && import.meta.env.MODE !== "test") {
          const currentStoreTier = useStore.getState().tier;
          if (currentStoreTier === "BASIC" || !currentStoreTier) {
            s = { tier: "BASIC", allow_live: false, can_upgrade: true, simulation_enabled: false };
          }
        }
        if (alive && s) setEntitlement(s.tier, s.can_upgrade, s.simulation_enabled ?? false, s.allow_live ?? false);
      } finally {
        if (alive) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [setEntitlement]);

  return { refetch };
}
