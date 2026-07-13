import { useState } from "react";
import {
  validateAlpaca,
  saveSecret,
  startEngine,
  stopEngine,
  isDesktop,
} from "@/lib/desktopBridge";

/**
 * #1402 / #1425 — in-app Alpaca key (re-)entry, PAPER or LIVE (`mode`). The desktop stores paper
 * and live credentials in SEPARATE keychain slots (ALPACA_API_KEY vs ALPACA_LIVE_API_KEY) so the
 * operator keeps both and can switch accounts (getrennte Aufbewahrung). Live keys validate against
 * the LIVE Alpaca API and are clearly marked — they trade REAL money. Mirrors SetupWizard's
 * Alpaca step: validate → save BOTH keys to the keychain → offer an engine restart so they apply.
 * Desktop-only (cloud manages credentials via GCP Secret Manager).
 */
const INPUT =
  "mt-1 w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30 disabled:opacity-40";

export function BrokerKeysCard({
  mode = "paper",
  embedded = false,
  locked = false,
}: {
  mode?: "paper" | "live";
  embedded?: boolean;
  locked?: boolean;
}) {
  const isLive = mode === "live";
  const apiKeyName = isLive ? "ALPACA_LIVE_API_KEY" : "ALPACA_API_KEY";
  const secretName = isLive ? "ALPACA_LIVE_SECRET_KEY" : "ALPACA_SECRET_KEY";

  const [isExpanded, setIsExpanded] = useState(false);

  const [keyId, setKeyId] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [restarting, setRestarting] = useState(false);

  // Desktop-only: the cloud edition manages credentials via GCP Secret Manager.
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;

  async function submit() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const res = await validateAlpaca(keyId.trim(), secret.trim(), isLive);
      if (!res.ok) {
        setError(
          res.status === 0
            ? "Couldn't reach Alpaca — check your connection."
            : `Alpaca rejected these ${isLive ? "live " : ""}keys (HTTP ${res.status}).`,
        );
        return;
      }
      const a = await saveSecret(apiKeyName, keyId.trim());
      const b = await saveSecret(secretName, secret.trim());
      if (!a.ok || !b.ok) {
        setError("Validated, but saving to the keychain failed.");
        return;
      }
      setSaved(true);
      setKeyId("");
      setSecret("");
    } catch {
      setError("Something went wrong — please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function restart() {
    setRestarting(true);
    try {
      // Stop-then-start so a running engine re-reads the keychain; on an OFFLINE engine the stop
      // is a no-op and start brings it up with the fresh keys.
      await stopEngine();
      await startEngine();
      setSaved(false);
    } catch {
      setError("Couldn't restart the engine — use the engine controls above.");
    } finally {
      setRestarting(false);
    }
  }

  const submitDisabled = busy || !keyId.trim() || !secret.trim();

  const title = isLive ? "Live Trading Keys" : "Paper Trading Keys";
  // Embedded = rendered as a sub-section inside the Trading Account card (no own surface);
  // standalone keeps its own card. `locked` = Junior's live slot: show a Senior lock, no form.
  const wrapperCls = embedded
    ? "border-t border-white/5 pt-4 mt-4"
    : `surface p-6 ${isLive ? "border border-[#ff453a]/30" : ""}`;

  return (
    <div className={wrapperCls}>
      <div className="flex items-center justify-between mb-2">
        <div className="eyebrow">{title}</div>
        {locked ? (
          <span className="text-[11px] font-medium text-white/40">🔒 Senior</span>
        ) : (
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="text-[11px] font-medium text-white/50 hover:text-white transition-colors"
          >
            {isExpanded ? "Close" : "Manage"}
          </button>
        )}
      </div>

      {locked ? (
        <div className="text-[12px] text-white/30">
          Unlocks with Senior — upgrade to enter live keys and trade real money.
        </div>
      ) : !isExpanded ? (
        <div className="text-[12px] text-white/40">
          {isLive ? "Live trading credentials. " : "Paper trading credentials. "}
          Click manage to update your keys.
        </div>
      ) : (
        <>
          <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
        {isLive ? (
          <>
            <span className="text-[#ff8a80]">Real money.</span> Your LIVE Alpaca keys, validated
            against <code className="text-white/40">api.alpaca.markets</code> and stored in their
            own keychain slot. Entering them does not start live trading — switching the active
            account to live is a separate, deliberate step.
          </>
        ) : (
          <>
            Update your Alpaca paper-trading keys. Validated against Alpaca and stored in your OS
            keychain — never on disk. Restart the engine to apply.
          </>
        )}
      </p>

      {error && (
        <div className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2 mb-3">
          {error}
        </div>
      )}
      {saved && (
        <div className="flex items-center justify-between gap-3 text-[12px] text-white/70 bg-white/[0.05] border border-white/10 rounded-lg px-3 py-2 mb-3">
          <span>Saved to the keychain.</span>
          <button
            className="btn shrink-0"
            onClick={() => void restart()}
            disabled={restarting}
            style={{ opacity: restarting ? 0.4 : 1 }}
          >
            {restarting ? "Restarting…" : "Restart engine"}
          </button>
        </div>
      )}

      <label className="block text-[12px] text-white/55 mb-3">
        API key ID
        <input
          aria-label={`alpaca-${mode}-key-id`}
          value={keyId}
          onChange={(e) => setKeyId(e.target.value)}
          className={INPUT}
        />
      </label>
      <label className="block text-[12px] text-white/55 mb-4">
        API secret key
        <input
          aria-label={`alpaca-${mode}-secret`}
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          className={INPUT}
        />
      </label>
      <button
        className="btn"
        onClick={() => void submit()}
        disabled={submitDisabled}
        style={{ opacity: submitDisabled ? 0.4 : 1 }}
      >
        {busy ? "Validating…" : isLive ? "Validate & save live keys" : "Validate & save"}
      </button>
        </>
      )}
    </div>
  );
}
