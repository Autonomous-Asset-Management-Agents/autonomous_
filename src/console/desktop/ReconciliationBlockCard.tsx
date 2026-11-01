import { useEffect, useState } from "react";
import {
  fetchReconciliationBlock,
  releaseReconciliationBlock,
  type ReconciliationBlock,
} from "@/lib/api";

const POLL_MS = 30_000;

const KIND_LABEL: Record<string, string> = {
  orphaned_order: "Order unknown to the engine",
  position_mismatch: "Position mismatch",
  unknown_position: "Position unknown to the engine",
  missing_fill: "Fill not recorded",
  missing_order: "Order unknown to the broker",
  broker_unreachable: "Broker unreachable",
};

/**
 * ReconciliationBlockCard (#3430) — the operator surface for the reconciliation block (#3389).
 *
 * When the broker and the engine disagree, the engine stops NEW ENTRIES (protective exits keep
 * running) until a human lifts the block. This card shows the finding that holds the block —
 * both sides of every mismatch — and offers one explicit release. It gives no recommendation:
 * whether to release is the operator's decision. The engine records who released and when.
 * Invisible while nothing is blocked.
 */
export function ReconciliationBlockCard() {
  const [data, setData] = useState<ReconciliationBlock | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [released, setReleased] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      Promise.resolve()
        .then(() => fetchReconciliationBlock())
        .then((d) => {
          if (alive) setData(d);
        })
        .catch(() => {
          /* engine offline → keep the last known state; nothing to release from here */
        });
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (released) {
    return (
      <div className="surface p-6">
        <div className="eyebrow mb-2">Reconciliation</div>
        <div className="text-[12px] text-white/80 bg-white/[0.05] border border-white/10 rounded-lg px-3 py-2">
          Block released — new entries are possible again. The release is recorded with time and
          author.
        </div>
      </div>
    );
  }

  if (!data?.entries_blocked) return null;

  async function release() {
    setBusy(true);
    setError(null);
    try {
      await releaseReconciliationBlock(reason.trim());
      setReleased(true);
    } catch {
      setError(
        "Couldn't release — the block is still in place. Check that the engine is running.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="surface p-6">
      <div className="eyebrow mb-2">Reconciliation</div>
      <h3 className="text-[15px] font-semibold text-white/90 mb-1">
        New entries are blocked
      </h3>
      <p className="text-[11px] text-white/40 mb-4 max-w-xl leading-relaxed">
        Broker and engine disagree
        {data.finished_at ? ` (run of ${data.finished_at.slice(0, 16).replace("T", " ")} UTC)` : ""}.
        Protective exits keep running; only new entries wait. The block stays until you release
        it.
        {data.last_run_clean
          ? " The latest run found no mismatch — the block still holds the finding below."
          : ""}
      </p>

      <table className="w-full text-[12.5px] mb-4">
        <thead>
          <tr className="text-white/40 text-[10px] font-semibold uppercase tracking-[0.12em]">
            <th className="text-left py-1.5">Symbol</th>
            <th className="text-left py-1.5">Mismatch</th>
            <th className="text-right py-1.5">Broker</th>
            <th className="text-right py-1.5">Engine</th>
          </tr>
        </thead>
        <tbody>
          {data.breaks.map((b, i) => (
            <tr key={`${b.kind}-${b.symbol}-${b.order_id}-${i}`} className="border-t border-white/5">
              <td className="py-2 font-semibold text-white/90">{b.symbol || b.order_id || "—"}</td>
              <td className="py-2 text-white/70">
                {KIND_LABEL[b.kind] ?? b.kind}
                {b.detail ? <span className="block text-[11px] text-white/40">{b.detail}</span> : null}
              </td>
              <td className="py-2 text-right num text-white/92">{b.broker_side || "—"}</td>
              <td className="py-2 text-right num text-white/92">{b.engine_side || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <label className="block text-[12px] text-white/55 mb-3">
        Reason (optional, recorded with the release)
        <input
          id="reconciliation-release-reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={500}
          className="mt-1 w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30"
        />
      </label>
      {error && (
        <div className="text-[12px] text-[#ffb4af] bg-[#ff5a52]/10 border border-[#ff5a52]/25 rounded-lg px-3 py-2 mb-3">
          {error}
        </div>
      )}
      <button className="btn" onClick={() => void release()} disabled={busy}>
        {busy ? "Releasing…" : "Release block"}
      </button>
    </div>
  );
}
