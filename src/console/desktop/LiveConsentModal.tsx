import { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { liveEnable, liveDisable, postSwitchReconcile, updateHitlPolicy, fetchHealth } from "@/lib/api";
import { defaultPerTradeLimit } from "@/console/live/hitlAutonomy";
import { restartEngine, isDesktop, getKeychainStatus, validateAlpaca, saveSecret } from "@/lib/desktopBridge";
import { useStore } from "@/console/store/useStore";
import { currencySymbol } from "@/console/lib/format";
import { IconShield } from "@/console/shared/Icons";

/**
 * LiveConsentModal (#2465) — the WORM-audited go-live confirm, HOISTED out of the Settings card
 * into a single GLOBAL mount (DesktopApp). It opens OVER whatever page is active (typically the
 * Overview) via the one-shot `requestLiveConsent` store signal — the "Start Live Trading" button no
 * longer navigates to Settings. It owns the whole Paper↔Live switch: the ACK + advance-approval
 * consent, the live-key entry (embedded, keys still live in Settings), the per-trade HITL autonomy,
 * the WORM enable, the engine restart (with the AAA_GO_LIVE directive), and the reconcile. The
 * reconcile display state is published to the store so the Settings card's state line still shows it.
 */
const ACK =
  "I understand this enables LIVE trading with real money on my Alpaca live account, and that I am responsible for every order placed (EU AI Act Art. 14 — human oversight).";
// GTM-1 #1804: MiFID-style advance-approval waiver — recorded with ACK on the WORM chain. Verbatim.
const ADVANCE_APPROVAL_WAIVER =
  "I hereby expressly confirm that I authorize the AAAgents software to automatically convert my predefined parameters and trading signals into binding orders and transmit them to my broker via the local interface (Advance-Approval). I am fully aware that Autonomous Asset Management Agents UG (haftungsbeschränkt) acts purely as an execution-only tool, does not conduct any suitability or appropriateness assessment of my person, and that no renewed risk disclosure takes place prior to the system-generated execution of individual trades. I bear sole legal and financial responsibility for all resulting transactions, including the risk of a total loss. I explicitly acknowledge and accept the inherent risks of technical errors in the beta software. Past performance is not a reliable indicator of future results.";

const RECONCILE_MAX_ATTEMPTS = 20;
// ~41.2 s total backoff window: fast early probes then a steady 3 s tail, long enough to cover a
// cold engine boot (model load + DB bootstrap) that answers /health "starting" quickly the whole
// time — the old ~17.2 s window false-failed such boots into "account unconfirmed".
const RECONCILE_BACKOFF_MS = [
  200, 500, 1000, 1500, 2000, 2000, 2000, 2000, 2000, 2000, 2000, 3000, 3000, 3000, 3000, 3000,
  3000, 3000, 3000,
];
// Per-call cap for each reconcile /health poll. Bounds a single stalled /health (up to 26 s under
// event-loop starvation, OTel 0.3.0) so it aborts and retries fast instead of hanging the whole
// switch confirmation for the 45 s generic FETCH_TIMEOUT_MS.
const RECONCILE_POLL_TIMEOUT_MS = 5000;
const sleep = (ms: number) => new Promise<void>((res) => setTimeout(res, ms));

function emitReconcileTelemetry(payload: {
  switch_id?: string;
  attempts: number;
  settled_status?: string | null;
  paper_trading?: boolean | null;
}) {
  try {
    void postSwitchReconcile(payload);
  } catch {
    /* fail-open: telemetry never blocks a switch */
  }
}

export function LiveConsentModal() {
  const paper = useStore((s) => s.paperTrading);
  const setPaperTrading = useStore((s) => s.setPaperTrading);
  const allowLive = useStore((s) => s.allowLive);
  const liveEquity = useStore((s) => s.liveEquity);
  const setLiveEquity = useStore((s) => s.setLiveEquity);
  const engineServing = useStore((s) => s.engineServing);
  const setEngineServing = useStore((s) => s.setEngineServing);
  const setModeSwitch = useStore((s) => s.setModeSwitch);
  const resetEquityCurves = useStore((s) => s.resetEquityCurves);
  const curSym = currencySymbol(useStore((s) => s.accountCurrency)); // PR-B: account currency (USD)
  const resetAccountSnapshot = useStore((s) => s.resetAccountSnapshot);
  const setSidebarStatusMsg = useStore((s) => s.setSidebarStatusMsg);
  const setDesktopPage = useStore((s) => s.setDesktopPage);
  const requestLiveConsent = useStore((s) => s.requestLiveConsent);
  const setRequestLiveConsent = useStore((s) => s.setRequestLiveConsent);
  const requestSwitchToPaper = useStore((s) => s.requestSwitchToPaper);
  const setRequestSwitchToPaper = useStore((s) => s.setRequestSwitchToPaper);
  const setSwitchReconciling = useStore((s) => s.setSwitchReconciling);
  const setSwitchConfirmFailed = useStore((s) => s.setSwitchConfirmFailed);
  const switchError = useStore((s) => s.switchError);
  const setSwitchError = useStore((s) => s.setSwitchError);

  const [confirming, setConfirming] = useState(false);
  const [ack, setAck] = useState(false);
  const [waiver, setWaiver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [hitlMode, setHitlMode] = useState<"limit" | "full">("limit");
  const [hitlAmount, setHitlAmount] = useState<string>("");
  const [liveKeysPresent, setLiveKeysPresent] = useState<boolean | null>(null);
  // #4: live keys entered/changed IN the modal; validated + saved by the Enable submit itself.
  const [keyId, setKeyId] = useState("");
  const [secret, setSecret] = useState("");

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Prefill the 10%-of-equity HITL default each time the confirm opens.
  useEffect(() => {
    if (!confirming) return;
    const def = defaultPerTradeLimit(liveEquity);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHitlMode("limit");
    setHitlAmount(def > 0 ? String(def) : "");
  }, [confirming, liveEquity]);

  // Live-key presence probe: re-runs on liveEquity change too, so validating+saving live keys in the
  // EMBEDDED BrokerKeysCard below (which sets liveEquity) flips liveKeysPresent → the Enable unlocks.
  useEffect(() => {
    if (!confirming) return;
    let alive = true;
    void getKeychainStatus()
      .then((st) => { if (alive) setLiveKeysPresent(!!st?.ALPACA_LIVE_API_KEY && !!st?.ALPACA_LIVE_SECRET_KEY); })
      .catch(() => { if (alive) setLiveKeysPresent(null); });
    return () => { alive = false; };
  }, [confirming, liveEquity]);


  async function restart(switchId?: string, goLive = false) {
    // #2465: goLive=true carries the one-shot consent directive (AND-gated with the WORM chain in the
    // shell). Only toLive() passes it; toPaper() never does.
    await restartEngine(switchId, { goLive });
  }

  async function reconcilePaper(expectingLive = false, switchId?: string) {
    setSwitchReconciling(true);
    setSwitchConfirmFailed(false);
    try {
      for (let attempt = 0; attempt < RECONCILE_MAX_ATTEMPTS; attempt++) {
        const h = await fetchHealth(RECONCILE_POLL_TIMEOUT_MS);
        if (h && h.status !== "starting") {
          if (!mountedRef.current) return;
          setPaperTrading(h.paper_trading ?? true);
          if (expectingLive && h.paper_trading === true && h.live_enable_failed_reason) {
            setSwitchError(`Live not enabled: ${h.live_enable_failed_reason}`);
          }
          emitReconcileTelemetry({
            switch_id: switchId,
            attempts: attempt + 1,
            settled_status: h.status ?? "healthy",
            paper_trading: h.paper_trading ?? true,
          });
          return;
        }
        if (attempt < RECONCILE_MAX_ATTEMPTS - 1) {
          await sleep(RECONCILE_BACKOFF_MS[attempt] ?? 800);
        }
      }
      if (mountedRef.current) {
        setSwitchConfirmFailed(true);
        setEngineServing(false);
      }
      emitReconcileTelemetry({
        switch_id: switchId,
        attempts: RECONCILE_MAX_ATTEMPTS,
        settled_status: "unconfirmed",
        paper_trading: null,
      });
    } finally {
      if (mountedRef.current) setSwitchReconciling(false);
    }
  }

  async function toLive() {
    setBusy(true);
    setSwitchError(null);
    // #4 (plan §3.2, steps a+b): if the operator entered live keys here, VALIDATE then SAVE them
    // FIRST — before any overlay/WORM/restart. A rejection stops with an inline error, the modal
    // stays open, inputs are preserved, and NO liveEnable/restart runs (mandated error-lifecycle).
    const typedKey = keyId.trim();
    const typedSecret = secret.trim();
    if (typedKey || typedSecret) {
      if (!typedKey || !typedSecret) {
        setSwitchError("Enter BOTH the live API key and the secret.");
        setBusy(false);
        return;
      }
      try {
        const res = await validateAlpaca(typedKey, typedSecret, true);
        if (!res.ok) {
          setSwitchError(
            res.status === 0
              ? "Couldn't reach Alpaca — check your connection."
              : `Alpaca rejected these live keys (HTTP ${res.status}).`,
          );
          setBusy(false);
          return; // STOP — modal open, inputs preserved, no WORM, no restart
        }
        if (res.equity != null) setLiveEquity(res.equity);
        const a = await saveSecret("ALPACA_LIVE_API_KEY", typedKey);
        const b = await saveSecret("ALPACA_LIVE_SECRET_KEY", typedSecret);
        if (!a.ok || !b.ok) {
          setSwitchError("Validated, but saving the keys to the keychain failed — please try again.");
          setBusy(false);
          return;
        }
        setKeyId("");
        setSecret("");
      } catch {
        setSwitchError("Couldn't validate the live keys — please try again.");
        setBusy(false);
        return;
      }
    }
    // Keys are valid+saved (or already present). NOW raise the overlay and go live (steps c+d).
    setModeSwitch({ to: "live", startedAt: Date.now() });
    resetEquityCurves(); // #2465: no €152k paper carryover
    resetAccountSnapshot(); // clear the paper equity/positions so no stale account flashes on reveal
    setDesktopPage("overview"); // #2465: land directly on the Overview, not Settings
    setEngineServing(false);
    setSidebarStatusMsg("Reconfiguring for Live Environment...");
    try {
      const switchId = crypto.randomUUID();
      try {
        if (hitlMode === "full") {
          await updateHitlPolicy({
            HITL_MAX_VALUE_PER_TRADE: 0, HITL_MAX_VALUE_PER_DAY: 0, HITL_AUTONOMOUS_UNLIMITED: true,
            HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: false, HITL_EXPIRY_SECONDS: 900,
          });
        } else {
          await updateHitlPolicy({
            HITL_MAX_VALUE_PER_TRADE: Number(hitlAmount) || 0, HITL_MAX_VALUE_PER_DAY: 0,
            HITL_AUTONOMOUS_UNLIMITED: false,
            HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: false, HITL_EXPIRY_SECONDS: 900,
          });
        }
      } catch { /* fail-open: never block the switch on a policy-save hiccup */ }
      await liveEnable(`${ACK}\n\n${ADVANCE_APPROVAL_WAIVER}`, switchId);
      // #2465 FIX 3: close the confirm BEFORE the restart so only the mode-switch pulse is visible for
      // the whole transition — no "Enabling…" modal lingering over the old paper page.
      setConfirming(false);
      setAck(false);
      setWaiver(false);
      await restart(switchId, true); // #2465: consent-live boot — set the AAA_GO_LIVE directive
      await reconcilePaper(true, switchId);
    } catch {
      setModeSwitch(null);
      setSwitchError("Couldn't enable live trading — the engine refused or is offline.");
    } finally {
      setBusy(false);
      // #2465 FIX 2: clear the transient "Reconfiguring…" status on SUCCESS too (was only cleared in
      // the catch), so the revealed sidebar shows the clean live state, not a stuck "Reconfiguring…".
      setSidebarStatusMsg(null);
    }
  }

  async function toPaper() {
    setModeSwitch({ to: "paper", startedAt: Date.now() });
    resetEquityCurves(); // #2465: clear the live curve when returning to paper
    resetAccountSnapshot(); // clear the LIVE equity/positions so they don't linger on the paper page
    setDesktopPage("overview");
    setEngineServing(false);
    setSidebarStatusMsg("Reconfiguring for Paper Environment...");
    setBusy(true);
    setSwitchError(null);
    try {
      const switchId = crypto.randomUUID();
      await liveDisable("operator switched the active account back to paper", switchId);
      await restart(switchId);
      await reconcilePaper(false, switchId);
    } catch {
      setModeSwitch(null);
      setSwitchError("Couldn't switch back to paper — please try again.");
    } finally {
      setBusy(false);
      setSidebarStatusMsg(null); // #2465 FIX 2: clear the "Reconfiguring…" status on success too
    }
  }

  // #2465: the "Start Live Trading" button (or the Settings "Live" tile) raises this one-shot signal.
  // Consume it and open the confirm — only for a Senior currently on paper. Opens OVER the active page.
  useEffect(() => {
    if (!requestLiveConsent) return;
    if (paper == null) return;
    setRequestLiveConsent(false);
    if (allowLive === false) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (paper === true && !busy) setConfirming(true);
  }, [requestLiveConsent, paper, allowLive, busy, setRequestLiveConsent]);

  // #2465: the Settings "Paper" tile raises this one-shot signal → switch back to paper (no modal).
  useEffect(() => {
    if (!requestSwitchToPaper) return;
    if (paper == null) return;
    setRequestSwitchToPaper(false);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (paper === false && !busy) void toPaper();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestSwitchToPaper, paper, busy]);


  // Desktop-only (browser has no keychain/engine). Nothing to render unless the confirm is open.
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;
  if (!confirming || paper !== true) return null;

  const onCancel = () => {
    setConfirming(false);
    setAck(false);
    setWaiver(false);
    setKeyId("");
    setSecret("");
    setSwitchError(null);
  };

  return createPortal(
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="surface w-full max-w-md rounded-xl border border-white/10 shadow-2xl flex flex-col max-h-[90vh] overflow-hidden">
        <div className="p-6 space-y-4 overflow-y-auto">
          <div className="flex items-center gap-3">
            <IconShield width={22} height={22} className="text-white/70" />
            <h3 className="text-lg font-bold text-white">Enable Live Trading?</h3>
          </div>
          <p className="text-[13px] text-white/70 leading-relaxed">
            You are switching the engine to your LIVE Alpaca account. Orders will execute with{" "}
            <strong className="text-white/90">real money</strong> and you are responsible for every
            one of them (EU AI Act Art. 14 — human oversight).
          </p>
          <p className="text-[12px] text-white/60 bg-white/[0.03] border border-white/10 p-2.5 rounded-lg">
            This action is permanently recorded on the WORM compliance audit trail.
          </p>
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
          {/* #2454: live autonomy — per-trade limit prefilled at 10% of equity, or full autonomous. */}
          <div className="rounded-lg px-3 py-3 border border-white/10 bg-white/[0.02]">
            <div className="text-[12px] font-semibold text-white/90 mb-2">Autonomy limit — per trade</div>
            <div className="flex gap-2 mb-2.5">
              <button
                type="button"
                onClick={() => { setHitlMode("limit"); const d = defaultPerTradeLimit(liveEquity); if (d > 0) setHitlAmount(String(d)); }}
                className={`flex-1 text-[12px] font-semibold py-1.5 rounded-full border transition-all ${
                  hitlMode === "limit" && String(defaultPerTradeLimit(liveEquity)) === hitlAmount
                    ? "bg-white/12 text-white border-white/25"
                    : "bg-white/[0.04] text-white/70 border-white/10 hover:text-white/90"
                }`}
              >
                10% of equity
              </button>
              <button
                type="button"
                onClick={() => setHitlMode("limit")}
                className={`flex-1 text-[12px] font-semibold py-1.5 rounded-full border transition-all ${
                  hitlMode === "limit" && String(defaultPerTradeLimit(liveEquity)) !== hitlAmount
                    ? "bg-white/12 text-white border-white/25"
                    : "bg-white/[0.04] text-white/70 border-white/10 hover:text-white/90"
                }`}
              >
                Custom
              </button>
              <button
                type="button"
                onClick={() => setHitlMode("full")}
                className={`flex-1 text-[12px] font-semibold py-1.5 rounded-full border transition-all ${
                  hitlMode === "full"
                    ? "bg-white/12 text-white border-white/25"
                    : "bg-white/[0.04] text-white/70 border-white/10 hover:text-white/90"
                }`}
              >
                Full autonomous
              </button>
            </div>
            {hitlMode === "limit" ? (
              <>
                <div className="flex items-center gap-2 bg-white/[0.05] border border-white/10 rounded-lg px-3 py-2">
                  <span className="text-white/40 text-[15px]">{curSym}</span>
                  <input
                    type="number"
                    inputMode="numeric"
                    aria-label="hitl-per-trade"
                    value={hitlAmount}
                    onChange={(e) => setHitlAmount(e.target.value)}
                    className="flex-1 bg-transparent text-[15px] font-semibold text-white outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
                    placeholder="per-trade limit"
                  />
                </div>
                <div className="text-[11px] text-white/40 mt-2">
                  {defaultPerTradeLimit(liveEquity) > 0
                    ? <>Default: 10% of your live equity. Orders up to this run automatically; larger ones wait for your approval.</>
                    : <>Enter the max value per trade that may execute automatically; larger orders wait for your approval.</>}
                </div>
              </>
            ) : (
              <div className="text-[11px] text-white/50">
                Every order executes automatically — no per-trade approval. You can set a limit anytime in Settings → Trading.
              </div>
            )}
          </div>
          {/* #4 (plan §3.2): live keys are entered/changed HERE and validated + saved by the Enable
              submit itself (no separate button). Keys are still hosted in Settings; blank = keep the
              stored keys, entering new ones changes them. Validation happens on Enable → inline error. */}
          <div className="rounded-lg px-3 py-3 border border-white/10 bg-white/[0.03] space-y-2">
            <div className="text-[12px] text-white/70">
              Live Alpaca keys{" "}
              {liveKeysPresent === false ? (
                <span className="text-[#ff8a80]">— required (none saved yet)</span>
              ) : (
                <span className="text-white/40">— stored; leave blank to keep, or enter new ones to change</span>
              )}
            </div>
            <input
              aria-label="live-key-id"
              value={keyId}
              onChange={(e) => setKeyId(e.target.value)}
              placeholder={liveKeysPresent === false ? "APCA-API-KEY-ID" : "•••••••••••••• — stored (leave blank to keep)"}
              className="w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30"
            />
            <input
              aria-label="live-secret"
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder={liveKeysPresent === false ? "APCA-API-SECRET-KEY" : "•••••••••••••• — stored (leave blank to keep)"}
              className="w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30"
            />
            <div className="text-[11px] text-white/40">
              Validated against Alpaca and saved to your OS keychain when you enable — never leaves this device.
            </div>
          </div>
          {switchError && (
            <p className="text-[12px] text-white/80 bg-white/[0.03] border border-white/10 rounded-lg px-3 py-2">{switchError}</p>
          )}
        </div>
        <div className="flex justify-end gap-3 p-4 border-t border-white/10 shrink-0">
          <button
            className="rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase text-white/70 bg-white/[0.08] transition-all transform active:scale-[0.97] disabled:opacity-40"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            className="rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase text-white bg-[#ff5a52] hover:bg-[#ff6c65] border-none transition-all transform active:scale-[0.97] disabled:opacity-40 disabled:cursor-not-allowed"
            onClick={() => void toLive()}
            disabled={!ack || !waiver || busy || (liveKeysPresent === false && !keyId.trim())}
          >
            {busy ? "Enabling…" : "Enable live trading"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
