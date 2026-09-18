import { useEffect, useState } from "react";
import {
  fetchPendingConstraints,
  approvePortfolioConstraints,
  type PendingConstraintsResponse,
} from "@/lib/api";

/**
 * ConstraintApprovalCard (#2666, Epic #2655 Weg 1) — the LOCAL approval surface for the async
 * EOD sector-governance flow. The engine's EOD assessment (#2652) stages a RELATIVE sector-cap
 * proposal and notifies the operator over their own private channels; this card shows the
 * proposal READ-ONLY (current cap → proposed cap) and offers one explicit Confirm that POSTs
 * the Ed25519 approval token to the four-eyes endpoint (#2653). Opening/reading never mutates —
 * only the Confirm click does (magic-link-safe). Rendered locally in the desktop console:
 * nothing hosted, no central auth (BaFin decentralization constraint). Invisible while no
 * proposal is pending.
 */
export function ConstraintApprovalCard() {
  const [data, setData] = useState<PendingConstraintsResponse | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.resolve()
      .then(() => fetchPendingConstraints())
      .then((d) => {
        if (alive) setData(d);
      })
      .catch(() => {
        /* engine offline → card simply stays hidden; nothing to approve anyway */
      });
    return () => {
      alive = false;
    };
  }, []);

  const pending = data?.pending;
  if (!pending || !pending.caps || Object.keys(pending.caps).length === 0) return null;

  const approved = data?.approved ?? {};
  const pct = (v: number | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await approvePortfolioConstraints(token.trim(), "operator");
      setDone(true);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "";
      setError(
        /expired/i.test(msg)
          ? "Token expired — tomorrow's EOD assessment will send a fresh proposal."
          : "Couldn't approve the constraints — check the token and that the engine is running.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="surface p-6">
      <div className="eyebrow mb-2">Governance</div>
      <h3 className="text-[15px] font-semibold text-white/90 mb-1">
        Portfolio constraints — awaiting your approval
      </h3>
      <p className="text-[11px] text-white/40 mb-4 max-w-md leading-relaxed">
        Tomorrow&apos;s sector caps, proposed by the end-of-day assessment
        {pending.created_at ? ` (${pending.created_at.slice(0, 10)})` : ""}. Reviewing changes
        nothing — only Approve does, and it is recorded on the WORM audit trail.
      </p>

      <table className="w-full text-[12.5px] mb-4">
        <thead>
          <tr className="text-white/40 text-[10px] font-semibold uppercase tracking-[0.12em]">
            <th className="text-left py-1.5">Sector</th>
            <th className="text-right py-1.5">Current cap</th>
            <th className="text-right py-1.5">Proposed cap</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(pending.caps)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([sector, cap]) => (
              <tr key={sector} className="border-t border-white/5">
                <td className="py-2 font-semibold text-white/90">{sector}</td>
                <td className="py-2 text-right num text-white/50">{pct(approved[sector])}</td>
                <td className="py-2 text-right num text-white/92">{pct(cap)}</td>
              </tr>
            ))}
        </tbody>
      </table>

      {done ? (
        <div className="text-[12px] text-white/80 bg-white/[0.05] border border-white/10 rounded-lg px-3 py-2">
          Constraints approved — the portfolio manager enforces them from the next rebalance
          cycle.
        </div>
      ) : (
        <>
          <label className="block text-[12px] text-white/55 mb-3">
            Approval token (from your notification)
            <input
              aria-label="constraint-token"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="paste the token from the EOD message"
              className="mt-1 w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30"
            />
          </label>
          {error && (
            <div className="text-[12px] text-[#ffb4af] bg-[#ff5a52]/10 border border-[#ff5a52]/25 rounded-lg px-3 py-2 mb-3">
              {error}
            </div>
          )}
          <button
            className="btn"
            onClick={() => void confirm()}
            disabled={busy || !token.trim()}
          >
            {busy ? "Approving…" : "Approve constraints"}
          </button>
        </>
      )}
    </div>
  );
}
