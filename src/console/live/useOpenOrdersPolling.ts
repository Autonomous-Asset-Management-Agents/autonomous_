import { useEffect } from "react";
import { fetchOpenOrders } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import { adaptOpenOrders } from "./openOrders";

// Open / working orders churn fast (they fill or cancel within seconds), so poll on a brisk 10s
// cadence. It is cheap — a single OPEN-status window — unlike the paginated fill history (60s).
const POLL_MS = 10_000;

/**
 * Polls /open-orders (through the desktop-aware api layer) and writes the adapted open orders into
 * the store. Recursive setTimeout (not setInterval) so a slow engine can never stack overlapping
 * requests; a failed poll is swallowed and the store keeps its last value.
 */
export function useOpenOrdersPolling(): void {
  const setOpenOrders = useStore((s) => s.setOpenOrders);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const loop = async () => {
      if (!alive) return;
      try {
        const resp = await fetchOpenOrders();
        // #2822: never write while a Paper<->Live switch is in flight - an in-flight poll against
        // the DYING old engine would repaint the OLD account's orders over the cleared store.
        if (alive && resp && useStore.getState().modeSwitch == null) setOpenOrders(adaptOpenOrders(resp));
      } finally {
        if (alive) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();

    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [setOpenOrders]);
}
