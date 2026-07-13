import { useState, useEffect } from "react";
import { getHitlPolicy, updateHitlPolicy, type HitlPolicy } from "@/lib/api";
import { StatusDot } from "@/console/shared/StatusDot";

/**
 * LIVE-1 T2 (#1425) — the honest human-in-the-loop policy card.
 *
 * Replaces the hardcoded "Auto-approve under €250 with senate ≥ 0.65" `localStorage` placeholder
 * (Settings.tsx, the Decision-routing row) with the REAL engine policy: it loads GET
 * /api/hitl/policy and persists edits via POST /api/hitl/policy. `HITL_ENABLED` is the env+redeploy
 * step (C2 — the engine rejects it on POST with 422), so it is shown READ-ONLY, never a fake
 * toggle. Above a limit an order waits for the operator's approval; 0 = approve every order.
 */
const INPUT =
  "mt-1 w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30 disabled:opacity-40";

export function HitlPolicyCard() {
  const [policy, setPolicy] = useState<HitlPolicy | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [maxTrade, setMaxTrade] = useState("");
  const [maxDay, setMaxDay] = useState("");
  const [unlimited, setUnlimited] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    // Promise.resolve(...) so even a synchronous throw (the engine/API unavailable) degrades to the
    // offline fallback below instead of crashing the Settings page that hosts this card.
    Promise.resolve()
      .then(() => getHitlPolicy())
      .then((p) => {
        if (!alive) return;
        setPolicy(p);
        setMaxTrade(String(p.HITL_MAX_VALUE_PER_TRADE));
        setMaxDay(String(p.HITL_MAX_VALUE_PER_DAY));
        setUnlimited(p.HITL_AUTONOMOUS_UNLIMITED);
      })
      .catch(() => {
        if (alive) setLoadError(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  async function save() {
    if (!policy) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      // HITL_ENABLED is deliberately NOT sent — env-only (C2); the engine 422s a POST that includes it.
      const updated = await updateHitlPolicy({
        HITL_MAX_VALUE_PER_TRADE: Number(maxTrade) || 0,
        HITL_MAX_VALUE_PER_DAY: Number(maxDay) || 0,
        HITL_AUTONOMOUS_UNLIMITED: unlimited,
        HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: policy.HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS,
        HITL_EXPIRY_SECONDS: policy.HITL_EXPIRY_SECONDS,
      });
      setPolicy(updated);
      setSaved(true);
    } catch {
      setError("Couldn't save the policy — is the engine running?");
    } finally {
      setBusy(false);
    }
  }

  if (loadError) {
    return (
      <div className="surface p-6">
        <div className="eyebrow mb-2">Human-in-the-loop policy</div>
        <p className="text-[12px] text-white/40 max-w-md leading-relaxed">
          Policy unavailable — the engine is offline. Start the engine to view and edit the
          approval limits.
        </p>
      </div>
    );
  }

  return (
    <div className="surface p-6">
      <div className="flex items-center justify-between mb-2">
        <div className="eyebrow">Human-in-the-loop policy</div>
        <StatusDot tone={policy?.HITL_ENABLED ? "on" : "off"}>
          Human approval: {policy?.HITL_ENABLED ? "ON" : "OFF"}
        </StatusDot>
      </div>
      <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
        Live limits from the engine (<code className="text-white/40">/api/hitl/policy</code>). An
        order above a limit waits for your approval; <strong>0 = approve every order</strong>. Human
        approval is required for live trading and is set at engine start (env-only), so it is shown
        read-only here.
      </p>

      {error && (
        <div className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2 mb-3">
          {error}
        </div>
      )}
      {saved && (
        <div className="text-[12px] text-white/70 bg-white/[0.05] border border-white/10 rounded-lg px-3 py-2 mb-3">
          Saved to the engine.
        </div>
      )}

      <label className="block text-[12px] text-white/55 mb-3">
        Max order value before approval (€, per trade)
        <input
          aria-label="max-per-trade"
          type="number"
          min={0}
          value={maxTrade}
          onChange={(e) => setMaxTrade(e.target.value)}
          className={INPUT}
          disabled={unlimited}
        />
      </label>
      <label className="block text-[12px] text-white/55 mb-4">
        Max autonomous value per day (€)
        <input
          aria-label="max-per-day"
          type="number"
          min={0}
          value={maxDay}
          onChange={(e) => setMaxDay(e.target.value)}
          className={INPUT}
          disabled={unlimited}
        />
      </label>
      <label className="flex items-center gap-2 text-[12px] text-white/55 mb-4">
        <input
          aria-label="autonomous-unlimited"
          type="checkbox"
          checked={unlimited}
          onChange={(e) => setUnlimited(e.target.checked)}
        />
        Fully autonomous — no value limits
      </label>
      <button className="btn" onClick={() => void save()} disabled={busy || !policy}>
        {busy ? "Saving…" : "Save policy"}
      </button>
    </div>
  );
}
