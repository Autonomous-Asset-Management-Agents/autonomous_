import { useState, useEffect } from "react";
import { getHitlPolicy, updateHitlPolicy, type HitlPolicy } from "@/lib/api";
import { StatusDot } from "@/console/shared/StatusDot";
import { useStore } from "@/console/store/useStore";
import { currencySymbol } from "@/console/lib/format";

/**
 * #2213 INC-7 — the ONE autonomy control (two-axis UX). Replaces the cosmetic
 * localStorage "Execution mode" tiles (which did nothing) AND the misleading
 * "Human approval: ON" pill (which showed the master flag, not the effective state).
 *
 * A single source of truth: the REAL engine policy (GET/POST /api/hitl/policy).
 *  - "Full Autonomous" → HITL_AUTONOMOUS_UNLIMITED = true (Mode C: every order auto-executes
 *    within the risk guardrails). Marked with a RED caution accent — real money, no per-trade
 *    human approval (operator design decision: no gold/amber; red for the high-autonomy state).
 *  - "Human-in-the-loop" → unlimited = false; orders above the account-currency limits wait for approval. Neutral.
 * One honest state line says what ACTUALLY happens. `HITL_ENABLED` stays engine-enforced on
 * live and is not a UI toggle (POST 422s it) — so it is not shown as a standalone pill anymore.
 */
const INPUT =
  "mt-1 w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30 disabled:opacity-40";

export function HitlPolicyCard() {
  const [policy, setPolicy] = useState<HitlPolicy | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [maxTrade, setMaxTrade] = useState("");
  const [maxDay, setMaxDay] = useState("");
  // PR-B: HITL limits are compared against order values in the account currency
  // (Alpaca USD), so the symbol follows account.currency — not a hardcoded €.
  const cur = currencySymbol(useStore((s) => s.accountCurrency));
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // RC2-4: optimistic mode. On click we set this immediately so the clicked tile flips active +
  // shows "Saving…" INSTANTLY, instead of looking frozen for the 13-39s the POST can take. It is
  // reconciled from the server on success and reverted on error (both in `apply`'s finally).
  const [pendingMode, setPendingMode] = useState<"auto" | "hitl" | null>(null);

  useEffect(() => {
    let alive = true;
    // Promise.resolve(...) so a synchronous throw (engine/API unavailable) degrades to the
    // offline fallback instead of crashing the hosting Settings page.
    Promise.resolve()
      .then(() => getHitlPolicy())
      .then((p) => {
        if (!alive) return;
        setPolicy(p);
        setMaxTrade(String(p.HITL_MAX_VALUE_PER_TRADE));
        setMaxDay(String(p.HITL_MAX_VALUE_PER_DAY));
      })
      .catch(() => {
        if (alive) setLoadError(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  // INC-4 (F4): the mode reflects what ACTUALLY happens. `HITL_ENABLED` is the master approval gate;
  // when it is false (paper default — the engine sets it only on live, native-engine-manager.cjs)
  // there is NO per-order approval step, so the effective state is Autonomous regardless of
  // UNLIMITED. Only with the gate ON does UNLIMITED distinguish auto vs. human-in-the-loop.
  // This effective `mode` drives the honest state line + the limits section.
  const mode: "auto" | "hitl" =
    policy && (!policy.HITL_ENABLED || policy.HITL_AUTONOMOUS_UNLIMITED) ? "auto" : "hitl";

  // RC2-4: the master gate is engine-enforced and only turns on for LIVE trading. With it OFF
  // (paper default) the human-in-the-loop tile cannot take effect — so rather than SILENTLY
  // collapsing a click back to auto (the landmine), we disable + annotate the HITL tile clearly.
  const gateOff = !!policy && !policy.HITL_ENABLED;
  // The tile shown active = the optimistic pick if one is inflight, else the effective mode.
  const activeMode: "auto" | "hitl" = pendingMode ?? mode;

  async function apply(patch: Partial<Omit<HitlPolicy, "HITL_ENABLED">>) {
    if (!policy) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      // HITL_ENABLED is deliberately NOT sent — env-only (C2), the engine 422s it. Every other
      // field is preserved so a mode flip never silently drops the operator's tuned limits.
      const updated = await updateHitlPolicy({
        HITL_MAX_VALUE_PER_TRADE: Number(maxTrade) || 0,
        HITL_MAX_VALUE_PER_DAY: Number(maxDay) || 0,
        HITL_AUTONOMOUS_UNLIMITED: policy.HITL_AUTONOMOUS_UNLIMITED,
        HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: policy.HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS,
        HITL_EXPIRY_SECONDS: policy.HITL_EXPIRY_SECONDS,
        ...patch,
      });
      setPolicy(updated);
      setMaxTrade(String(updated.HITL_MAX_VALUE_PER_TRADE));
      setMaxDay(String(updated.HITL_MAX_VALUE_PER_DAY));
      setSaved(true);
    } catch {
      setError("Couldn't save the policy — is the engine running?");
    } finally {
      // RC2-4: clear the optimistic pick — on success the reconciled `policy` (updated) now drives
      // the derived mode; on error the unchanged `policy` reverts the tile to where it was.
      setPendingMode(null);
      setBusy(false);
    }
  }

  const selectMode = (m: "auto" | "hitl") => {
    if (!policy || busy || pendingMode !== null || m === activeMode) return;
    // Belt-and-suspenders: the HITL tile is disabled when the gate is off (see `tile`), so this
    // guard just makes the invariant explicit.
    if (gateOff && m === "hitl") return;
    setPendingMode(m); // instant optimistic flip — the click reacts NOW, not after the slow POST
    void apply({ HITL_AUTONOMOUS_UNLIMITED: m === "auto" });
  };

  if (loadError) {
    return (
      <div className="surface p-6">
        <div className="eyebrow mb-2">Autonomy</div>
        <p className="text-[12px] text-white/40 max-w-md leading-relaxed">
          Autonomy policy unavailable — the engine is offline. Start the engine to view and edit
          how it executes trades.
        </p>
      </div>
    );
  }

  const tile = (m: "auto" | "hitl", title: string, body: string) => {
    const active = activeMode === m; // RC2-4: optimistic (pendingMode ?? mode)
    const isPending = pendingMode === m; // this tile is the one being saved right now
    const inflight = busy || pendingMode !== null; // keep BOTH tiles disabled while a save is inflight
    const hitlLocked = gateOff && m === "hitl"; // engine-enforced only on live → disabled + annotated
    // Active state: NEUTRAL grey, slightly brightened — both modes. No red border/glow on the
    // high-autonomy tile (operator: the red shimmer read as alarm). The only accent is the small
    // red "active" dot below.
    const ring = "border-white/20 bg-white/[0.06]";
    return (
      <button
        aria-label={`mode-${m}`}
        onClick={() => selectMode(m)}
        disabled={inflight || active || !policy || hitlLocked}
        className={`p-4 rounded-xl border text-left transition-all ${active ? ring : "border-white/10 hover:border-white/20 bg-white/[0.03]"} ${hitlLocked ? "opacity-50" : ""}`}
      >
        <div className="flex items-center gap-2 mb-1.5">
          <span className={`text-[13px] font-semibold ${active ? "text-white" : "text-white/70"}`}>{title}</span>
          {isPending ? (
            <StatusDot tone="neutral" className="ml-auto !text-[11px]">
              Saving…
            </StatusDot>
          ) : active ? (
            <StatusDot tone="off" className="ml-auto !text-[11px]">
              active
            </StatusDot>
          ) : null}
        </div>
        <div className="text-[11px] text-white/30 leading-relaxed">{body}</div>
        {hitlLocked && (
          <div className="text-[10px] text-white/40 mt-1.5">Enforced automatically on live trading.</div>
        )}
      </button>
    );
  };

  return (
    <div className="surface p-6">
      <div className="eyebrow mb-2">Autonomy</div>
      <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
        How the engine executes senate decisions. Read from the live engine (
        <code className="text-white/40">/api/hitl/policy</code>). Human oversight on live trading is
        enforced at engine start and cannot be disabled here.
      </p>

      <div className="grid grid-cols-2 gap-3 mb-4">
        {tile("auto", "Full Autonomous", "Every order auto-executes when conviction is met — no manual step. Within the risk guardrails.")}
        {tile("hitl", "Human-in-the-loop", "Orders above your limits wait for your approval in the Decisions queue.")}
      </div>

      {/* One honest state line — what ACTUALLY happens (replaces the misleading pill). */}
      <div className="mb-4 px-4 py-3 rounded-lg bg-white/[0.03] border border-white/8">
        {mode === "auto" ? (
          <StatusDot tone="off">
            Autonomous — every order auto-executes within the risk guardrails.
          </StatusDot>
        ) : Number(maxTrade) > 0 ? (
          <StatusDot tone="neutral">
            Human approval required for orders above <span className="mono">{cur}{maxTrade}</span>.
          </StatusDot>
        ) : (
          <StatusDot tone="neutral">Human approval required for every order.</StatusDot>
        )}
      </div>

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

      {/* Limits — only meaningful under Human-in-the-loop. */}
      {mode === "hitl" && (
        <>
          <label className="block text-[12px] text-white/55 mb-3">
            Max order value before approval ({cur}, per trade)
            <input
              aria-label="max-per-trade"
              type="number"
              min={0}
              value={maxTrade}
              onChange={(e) => setMaxTrade(e.target.value)}
              className={INPUT}
            />
          </label>
          <label className="block text-[12px] text-white/55 mb-4">
            Max autonomous value per day ({cur})
            <input
              aria-label="max-per-day"
              type="number"
              min={0}
              value={maxDay}
              onChange={(e) => setMaxDay(e.target.value)}
              className={INPUT}
            />
          </label>
          <button className="btn" onClick={() => void apply({})} disabled={busy || !policy}>
            {busy ? "Saving…" : "Save limits"}
          </button>
        </>
      )}
    </div>
  );
}
