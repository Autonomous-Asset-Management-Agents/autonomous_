import { useState, useEffect } from "react";
import { fetchHealth, liveEnable, liveDisable } from "@/lib/api";
import { startEngine, stopEngine, isDesktop } from "@/lib/desktopBridge";

/**
 * #1425 (LIVE-1 T2) — the Paper⇄Live account switcher. Switching to LIVE executes orders with REAL
 * money, so it is a deliberate, AUDITED step: the operator confirms an EU-AI-Act Art-14
 * acknowledgment, which is recorded on the tamper-evident WORM chain (POST /api/live/enable) BEFORE
 * the engine is restarted live (T1: the shell only flips PAPER_TRADING off once the WORM chain
 * verifies). Switching back to paper revokes it. Never bypasses the WORM gate. Desktop-only.
 */
const ACK =
  "I understand this enables LIVE trading with real money on my Alpaca live account, and that I am responsible for every order placed (EU AI Act Art. 14 — human oversight).";

export function LiveTradingSwitchCard() {
  const [paper, setPaper] = useState<boolean | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
  if (!isDesktop()) return null;

  async function restart() {
    await stopEngine();
    await startEngine();
  }

  async function toLive() {
    setBusy(true);
    setError(null);
    try {
      await liveEnable(ACK, crypto.randomUUID());
      await restart();
      setPaper(false);
      setConfirming(false);
      setAck(false);
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

  return (
    <div className={`surface p-6 ${isLive ? "border border-[#ff453a]/40" : ""}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="eyebrow">Trading account</div>
        <span className={`pill ${isLive ? "pill-warn" : ""}`}>
          {paper == null ? "…" : isLive ? "⚠ LIVE" : "Paper"}
        </span>
      </div>
      <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
        Which Alpaca account the engine trades. <strong>Live executes orders with real money.</strong>{" "}
        Switching is a deliberate, audited step — recorded on the tamper-evident WORM chain. Enter
        your live keys in the card above first.
      </p>

      {error && (
        <div className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2 mb-3">
          {error}
        </div>
      )}

      {paper === true && !confirming && (
        <button className="btn" onClick={() => setConfirming(true)}>
          Switch to live trading…
        </button>
      )}

      {paper === true && confirming && (
        <div className="space-y-3">
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
          <div className="flex gap-2">
            <button className="btn" onClick={() => void toLive()} disabled={!ack || busy} style={{ opacity: !ack || busy ? 0.4 : 1 }}>
              {busy ? "Enabling…" : "Enable live trading"}
            </button>
            <button
              className="btn"
              onClick={() => {
                setConfirming(false);
                setAck(false);
              }}
              disabled={busy}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {isLive && (
        <button className="btn" onClick={() => void toPaper()} disabled={busy} style={{ opacity: busy ? 0.4 : 1 }}>
          {busy ? "Switching…" : "Switch back to paper"}
        </button>
      )}
    </div>
  );
}
