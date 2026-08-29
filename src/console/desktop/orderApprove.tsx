import { useState } from "react";
import { useStore } from "@/console/store/useStore";
import { approveHitlOrder as realApproveHitlOrder, rejectHitlOrder as realRejectHitlOrder } from "@/lib/api";

/**
 * Per-order HITL approval primitives (#2660, Epic #2667) — the console half of the engine's
 * PR-0a-ii approval queue. Approve releases a held order into the engine's audited execution
 * drain (trading_loop._drain_hitl_approvals → execute_approved_order, EU AI Act Art. 14);
 * Reject removes it (audited server-side, never silently dropped). Mirrors orderCancel.tsx:
 * one co-located primitive per control, two-step arm→confirm so a mis-click can never move
 * live money.
 */

/** Fail-soft approve: calls the (injectable) approve API and, on success, optimistically drops the
 *  row from the store (the next /api/hitl/pending poll reconciles authoritatively). `success:false`
 *  (expired / already gone) OR a thrown rejection is treated as failure — the row is kept so a
 *  failed approve is never masked as done. */
// eslint-disable-next-line react-refresh/only-export-components
export async function approvePendingOrder(
  approvalId: string,
  approveOrder: typeof realApproveHitlOrder = realApproveHitlOrder,
): Promise<boolean> {
  try {
    const r = await approveOrder(approvalId);
    if (!r.success) return false; // keep the row + let ApproveButton surface the failure
    const { pendingApprovals, setPendingApprovals } = useStore.getState();
    setPendingApprovals(pendingApprovals.filter((p) => p.approvalId !== approvalId));
    return true;
  } catch {
    // Defense-in-depth: a rejected approve must never bubble as an unhandled rejection.
    return false;
  }
}

/** Fail-soft reject — same optimistic-removal contract as approvePendingOrder. */
// eslint-disable-next-line react-refresh/only-export-components
export async function rejectPendingOrder(
  approvalId: string,
  rejectOrder: typeof realRejectHitlOrder = realRejectHitlOrder,
): Promise<boolean> {
  try {
    const r = await rejectOrder(approvalId, "human_rejected");
    if (!r.success) return false;
    const { pendingApprovals, setPendingApprovals } = useStore.getState();
    setPendingApprovals(pendingApprovals.filter((p) => p.approvalId !== approvalId));
    return true;
  } catch {
    return false;
  }
}

/** HITL Approve control — two-step (arm → confirm): approving fires a REAL order at the broker,
 *  so a single mis-click must never be enough. On failure the control shows an error and stays
 *  armed for a retry (the row is NOT removed — the next poll would otherwise mask it). */
export function ApproveButton({ onApprove }: { onApprove: () => Promise<boolean> }) {
  const [state, setState] = useState<"idle" | "armed" | "error">("idle");
  const [busy, setBusy] = useState(false);
  if (state === "armed" || state === "error") {
    return (
      <span className="inline-flex items-center gap-2">
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            const ok = await onApprove();
            setBusy(false);
            if (!ok) setState("error"); // on success the row is removed by the parent
          }}
          className="text-[11px] font-semibold text-bull hover:underline disabled:opacity-50"
        >
          {busy ? "Approving…" : "Confirm"}
        </button>
        <button onClick={() => setState("idle")} className="text-[11px] text-white/40 hover:text-white/70">
          Keep
        </button>
        {state === "error" && <span className="text-[10px] text-bear">approve failed — retry</span>}
      </span>
    );
  }
  return (
    <button
      onClick={() => setState("armed")}
      className="text-[11px] font-semibold text-white/55 hover:text-bull transition-colors"
    >
      Approve
    </button>
  );
}

/** HITL Reject control — two-step like ApproveButton; the confirm label says "Confirm reject" so
 *  the two armed controls in one row can never be mistaken for each other. */
export function RejectButton({ onReject }: { onReject: () => Promise<boolean> }) {
  const [state, setState] = useState<"idle" | "armed" | "error">("idle");
  const [busy, setBusy] = useState(false);
  if (state === "armed" || state === "error") {
    return (
      <span className="inline-flex items-center gap-2">
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            const ok = await onReject();
            setBusy(false);
            if (!ok) setState("error");
          }}
          className="text-[11px] font-semibold text-bear hover:underline disabled:opacity-50"
        >
          {busy ? "Rejecting…" : "Confirm reject"}
        </button>
        <button onClick={() => setState("idle")} className="text-[11px] text-white/40 hover:text-white/70">
          Keep
        </button>
        {state === "error" && <span className="text-[10px] text-bear">reject failed — retry</span>}
      </span>
    );
  }
  return (
    <button
      onClick={() => setState("armed")}
      className="text-[11px] font-semibold text-white/55 hover:text-bear transition-colors"
    >
      Reject
    </button>
  );
}
