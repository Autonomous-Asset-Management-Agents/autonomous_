import { useState } from "react";
import { useStore } from "@/console/store/useStore";
import { cancelOrder as realCancelOrder } from "@/lib/api";

/**
 * Shared HITL order-cancel primitives (#2137) used by BOTH the Orders page and the Overview
 * order-book tile, so the human-oversight control (EU AI Act Art. 14) is identical everywhere.
 */

/** Fail-soft cancel of an open order: calls the (injectable) cancel API and, on success, optimistically
 *  drops the row from the store (the next /open-orders poll reconciles authoritatively). A non-success
 *  response OR a thrown rejection is treated as a failure (row kept) so a failed cancel is never masked.
 *  Co-located with its CancelButton on purpose (one order-cancel primitive); fast-refresh doesn't apply. */
// eslint-disable-next-line react-refresh/only-export-components
export async function cancelOpenOrder(
  id: string,
  cancelOrder: typeof realCancelOrder = realCancelOrder,
): Promise<boolean> {
  try {
    const r = await cancelOrder(id);
    if (r.status !== "success") return false; // keep the row + let CancelButton surface the failure
    const { openOrders, setOpenOrders } = useStore.getState();
    setOpenOrders(openOrders.filter((o) => o.id !== id));
    return true;
  } catch {
    // Defense-in-depth: a rejected cancel must never bubble as an unhandled rejection.
    return false;
  }
}

/** HITL Cancel control — two-step (arm → confirm) so a mis-click can't pull a live order. `onCancel`
 *  resolves true on success; on failure the control shows an error and stays armed for a retry (the
 *  order is NOT removed optimistically — the next poll would otherwise mask a failed cancel). */
export function CancelButton({ onCancel }: { onCancel: () => Promise<boolean> }) {
  const [state, setState] = useState<"idle" | "armed" | "error">("idle");
  const [busy, setBusy] = useState(false);
  if (state === "armed" || state === "error") {
    return (
      <span className="inline-flex items-center gap-2">
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            const ok = await onCancel();
            setBusy(false);
            if (!ok) setState("error"); // on success the row is removed by the parent
          }}
          className="text-[11px] font-semibold text-[#ff5a52] hover:underline disabled:opacity-50"
        >
          {busy ? "Cancelling…" : "Confirm"}
        </button>
        <button onClick={() => setState("idle")} className="text-[11px] text-white/40 hover:text-white/70">
          Keep
        </button>
        {state === "error" && <span className="text-[10px] text-[#ffb4af]">cancel failed — retry</span>}
      </span>
    );
  }
  return (
    <button
      onClick={() => setState("armed")}
      className="text-[11px] font-semibold text-white/55 hover:text-white/90 transition-colors"
    >
      Cancel
    </button>
  );
}
