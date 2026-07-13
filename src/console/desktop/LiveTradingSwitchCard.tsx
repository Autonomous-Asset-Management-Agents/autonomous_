import { useState, useEffect } from "react";
import { fetchHealth, liveEnable, liveDisable, fetchEntitlementStatus } from "@/lib/api";
import { startEngine, stopEngine, isDesktop, claimBeta } from "@/lib/desktopBridge";
import { useStore } from "@/console/store/useStore";
import { BrokerKeysCard } from "@/console/desktop/BrokerKeysCard";
import { IconShield } from "@/console/shared/Icons";
import { StatusDot } from "@/console/shared/StatusDot";

/**
 * Trading account (#1425 · #60) — the single "Trading Account" card: broker latency + the
 * Paper⇄Live toggle + both key slots (Paper/Live Trading Keys). The Paper|Live toggle is the
 * easy back-and-forth switch, but arming LIVE is a deliberate, AUDITED step: a compact confirm
 * (EU-AI-Act Art-14 acknowledgment) is recorded on the tamper-evident WORM chain (POST
 * /api/live/enable) BEFORE the engine restarts live. Switching back to paper revokes it. Never
 * bypasses the WORM gate. Desktop-only. Junior (allow_live=false) is paper-only → no toggle,
 * the live-keys slot is locked, and the CTA is Upgrade.
 */
const ACK =
  "I understand this enables LIVE trading with real money on my Alpaca live account, and that I am responsible for every order placed (EU AI Act Art. 14 — human oversight).";
// GTM-1 #1804: the MiFID-style advance-approval waiver — the operator authorizes autonomous
// signal→order execution without a renewed per-trade risk disclosure. Recorded together with ACK on
// the tamper-evident WORM chain (POST /api/live/enable). Legal-approved wording (entity = registered
// UG). Kept verbatim.
const ADVANCE_APPROVAL_WAIVER =
  "I hereby expressly confirm that I authorize the AAAgents software to automatically convert my predefined parameters and trading signals into binding orders and transmit them to my broker via the local interface (Advance-Approval). I am fully aware that Autonomous Asset Management Agents UG (haftungsbeschränkt) acts purely as an execution-only tool, does not conduct any suitability or appropriateness assessment of my person, and that no renewed risk disclosure takes place prior to the system-generated execution of individual trades. I bear sole legal and financial responsibility for all resulting transactions, including the risk of a total loss. I explicitly acknowledge and accept the inherent risks of technical errors in the beta software. Past performance is not a reliable indicator of future results.";

export function LiveTradingSwitchCard() {
  const [paper, setPaper] = useState<boolean | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [ack, setAck] = useState(false);
  const [waiver, setWaiver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // #1915: live trading is Senior-only — gate the toggle on the resolved entitlement.
  const allowLive = useStore((s) => s.allowLive);
  const setEntitlement = useStore((s) => s.setEntitlement);
  const [upgrading, setUpgrading] = useState(false);
  const [upgradeNote, setUpgradeNote] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchHealth().then((h) => {
      if (alive && h) setPaper(h.paper_trading ?? true);
    });
    return () => {
      alive = false;
    };
  }, []);

  // Desktop-only: the cloud edition manages its account out-of-band.
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;

  // #1915: Junior (allow_live=false) — live trading is a Senior feature. No toggle; the live-keys
  // slot is locked and the CTA is Upgrade (the engine 403s live-arming for BASIC anyway).
  if (allowLive === false) {
    const onUpgrade = async () => {
      if (upgrading) return;
      setUpgrading(true);
      setUpgradeNote(null);
      try {
        const res = await claimBeta();
        if (res.status === "claimed") {
          const s = await fetchEntitlementStatus();
          if (s)
            setEntitlement(s.tier, s.can_upgrade, s.simulation_enabled ?? false, s.allow_live ?? false);
        } else {
          setUpgradeNote(
            res.error === "desktop-only"
              ? "Available in the desktop app."
              : "Upgrade failed — please try again.",
          );
        }
      } catch {
        setUpgradeNote("Upgrade failed — please try again.");
      } finally {
        setUpgrading(false);
      }
    };
    return (
      <div className="surface p-6">
        <div className="flex items-center justify-between mb-2">
          <div className="eyebrow">Trading account</div>
          <StatusDot tone={paper === false ? "off" : "on"}>{paper === false ? "Live" : "Paper"}</StatusDot>
        </div>
        <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
          <strong className="text-white/70">Live trading is a Senior feature.</strong> Junior runs
          paper-only — upgrade to trade real money on your Alpaca live account.
        </p>

        <BrokerKeysCard mode="paper" embedded />
        <BrokerKeysCard mode="live" embedded locked />

        <div className="mt-5">
          {upgradeNote && (
            <div className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2 mb-3">
              {upgradeNote}
            </div>
          )}
          <button
            className="rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide text-white bg-[#00c27a] hover:bg-[#00d687] border border-transparent transition-all transform active:scale-[0.98] disabled:opacity-60"
            onClick={() => void onUpgrade()}
            disabled={upgrading}
          >
            {upgrading ? "UPGRADING…" : "UPGRADE TO SENIOR"}
          </button>
        </div>
      </div>
    );
  }

  async function restart() {
    await stopEngine();
    await startEngine();
  }

  async function toLive() {
    setBusy(true);
    setError(null);
    try {
      // Record BOTH consents (live-arming Art-14 ACK + the advance-approval waiver) on the WORM chain.
      await liveEnable(`${ACK}\n\n${ADVANCE_APPROVAL_WAIVER}`, crypto.randomUUID());
      await restart();
      setPaper(false);
      setConfirming(false);
      setAck(false);
      setWaiver(false);
    } catch {
      setError("Couldn't enable live trading — the engine refused or is offline. Enter valid live keys first.");
    } finally {
      setBusy(false);
    }
  }

  async function toPaper() {
    setBusy(true);
    setError(null);
    try {
      await liveDisable("operator switched the active account back to paper", crypto.randomUUID());
      await restart();
      setPaper(true);
    } catch {
      setError("Couldn't switch back to paper — please try again.");
    } finally {
      setBusy(false);
    }
  }

  const isLive = paper === false;

  // The easy Paper|Live toggle. → Paper is instant; → Live (real money) opens a compact,
  // WORM-audited confirm. Optimistic: if live keys are missing/invalid the enable fails and we
  // stay on paper with an error.
  const select = (live: boolean) => {
    if (busy || paper == null || live === isLive) return;
    if (live) setConfirming(true);
    else {
      setConfirming(false);
      void toPaper();
    }
  };

  return (
    <div className={`surface p-6 ${isLive ? "border border-[#ff453a]/40" : ""}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="eyebrow">Trading account</div>
        <div className="flex items-center gap-3">
          <div className="inline-flex items-center rounded-full bg-white/[0.04] border border-white/10 p-0.5 text-[11px] font-semibold">
            <button
              onClick={() => select(false)}
              disabled={busy || paper == null}
              className={`px-3 py-1 rounded-full transition-colors ${
                !isLive ? "bg-[#00c27a] text-white" : "text-white/45 hover:text-white/75"
              }`}
            >
              Paper
            </button>
            <button
              onClick={() => select(true)}
              disabled={busy || paper == null}
              className={`px-3 py-1 rounded-full transition-colors ${
                isLive ? "bg-[#ff5a52] text-white" : "text-white/45 hover:text-white/75"
              }`}
            >
              Live
            </button>
          </div>
        </div>
      </div>
      <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
        Which Alpaca account the engine trades. <strong>Live executes orders with real money.</strong>{" "}
        Switching is a deliberate, audited step — recorded on the tamper-evident WORM chain.
      </p>

      {error && (
        <div className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2 mb-3">
          {error}
        </div>
      )}

      {confirming && paper === true && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="surface max-w-md w-full p-6 space-y-4 rounded-xl border border-white/10 shadow-2xl">
            <div className="flex items-center gap-3 text-bear">
              <IconShield width={24} height={24} />
              <h3 className="text-lg font-bold text-white">Enable Live Trading?</h3>
            </div>
            <p className="text-[13px] text-white/70 leading-relaxed">
              You are switching the engine to your LIVE Alpaca account. Orders will execute with{" "}
              <strong className="text-white/90">real money</strong> and you are responsible for every
              one of them (EU AI Act Art. 14 — human oversight). Enter valid live keys first.
            </p>
            <p className="text-[12px] text-[#ffcc66]/80 bg-[#ffcc66]/10 border border-[#ffcc66]/20 p-2.5 rounded">
              <strong>Audit Notice:</strong> This action is permanently recorded on the WORM compliance audit trail.
            </p>
            <div className="space-y-3 my-4">
              <label className="flex items-start gap-2 text-[12px] text-white/70 leading-snug">
                <input
                  type="checkbox"
                  aria-label="ack-live"
                  checked={ack}
                  onChange={(e) => setAck(e.target.checked)}
                  className="mt-0.5 shrink-0"
                />
                <span>{ACK}</span>
              </label>
              <label className="flex items-start gap-2 text-[12px] text-white/70 leading-snug">
                <input
                  type="checkbox"
                  aria-label="ack-advance-approval"
                  checked={waiver}
                  onChange={(e) => setWaiver(e.target.checked)}
                  className="mt-0.5 shrink-0"
                />
                <span>{ADVANCE_APPROVAL_WAIVER}</span>
              </label>
            </div>
            {error && <p className="text-[12px] text-[#ff8a80]">{error}</p>}
            <div className="flex justify-end gap-3 pt-2">
              <button
                className="btn rounded-full px-5 py-2"
                onClick={() => {
                  setConfirming(false);
                  setAck(false);
                  setWaiver(false);
                }}
                disabled={busy}
              >
                Cancel
              </button>
              <button
                className="rounded-full px-5 py-2 font-bold text-white bg-[#ff5a52] hover:bg-[#ff6c65] border border-transparent transition-all disabled:opacity-40"
                onClick={() => void toLive()}
                disabled={!ack || !waiver || busy}
              >
                {busy ? "Enabling…" : "Enable live trading"}
              </button>
            </div>
          </div>
        </div>
      )}

      <BrokerKeysCard mode="paper" embedded />
      <BrokerKeysCard mode="live" embedded />
    </div>
  );
}
