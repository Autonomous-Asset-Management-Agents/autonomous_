import type { HitlPendingResponse } from "@/lib/api";

/**
 * HITL approval-queue adapter (#2660, Epic #2667): map the engine's /api/hitl/pending response
 * (orders the HITL gate HELD for human approval, PR-0a-ii) to the console shape. Fail-safe like
 * the open-orders adapter:
 *   - side normalises to "buy" | "sell" (out-of-contract → "buy", never crashes the table)
 *   - qty/price use `?? 0` (a real fractional qty must survive, rendered with fmtQty)
 *   - created_at parses to a Date, an unparseable/absent stamp collapses to null.
 */
export interface ConsolePendingApproval {
  approvalId: string;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price: number;
  createdAt: Date | null;
}

export function adaptHitlPending(
  resp: HitlPendingResponse | null | undefined,
): ConsolePendingApproval[] {
  const raw = Array.isArray(resp?.items) ? resp!.items! : [];
  return raw.map((p) => {
    const d = p.created_at ? new Date(p.created_at) : null;
    return {
      approvalId: String(p.approval_id ?? ""),
      symbol: String(p.symbol ?? ""),
      side: String(p.action ?? "").toLowerCase() === "sell" ? "sell" : "buy",
      qty: p.qty ?? 0,
      price: p.price ?? 0,
      createdAt: d && !Number.isNaN(d.getTime()) ? d : null,
    };
  });
}
