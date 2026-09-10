import { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { liveEnable, liveDisable, updateHitlPolicy } from "@/lib/api";
import { restartEngine, isDesktop, getKeychainStatus, validateAlpaca, saveSecret, getSetupState, saveSetupState } from "@/lib/desktopBridge";
import { postSettingsDeviationAck } from "@/lib/api";
import { reconcileEngineMode } from "@/lib/switchReconcile";
import {
  computeDeviations,
  defaultsMap,
  type SettingsDeviation,
} from "@/console/desktop/tradingSettingsDefaults";
import { useStore } from "@/console/store/useStore";
import { IconShield } from "@/console/shared/Icons";
import { StatusDot } from "@/console/shared/StatusDot";

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
  const resetAccountSnapshot = useStore((s) => s.resetAccountSnapshot);
  const resetAccountFeeds = useStore((s) => s.resetAccountFeeds);
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
  // #2708 D1: a live-arming intent must be refused while a mode switch is in flight (reconciling, or
  // the overlay's modeSwitch is set) — the consent modal must never open as a consequence of a switch.
  const switchReconciling = useStore((s) => s.switchReconciling);
  const modeSwitch = useStore((s) => s.modeSwitch);

  const [confirming, setConfirming] = useState(false);
  const [ack, setAck] = useState(false);
  const [waiver, setWaiver] = useState(false);
  const [busy, setBusy] = useState(false);
  // #2700: two modes only — no per-trade amount, no % threshold. See the picker below for why.
  // #2824: Full autonomy is the product's intended operating mode and therefore PRESELECTED —
  // marked in the Enable-button red below so the default is unmistakable. Both acks + the
  // waiver still gate the switch; the picked mode still persists explicitly on Enable.
  const [hitlMode, setHitlMode] = useState<"manual" | "full">("full");
  const [liveKeysPresent, setLiveKeysPresent] = useState<boolean | null>(null);
  // #4: live keys entered/changed IN the modal; validated + saved by the Enable submit itself.
  const [keyId, setKeyId] = useState("");
  const [secret, setSecret] = useState("");
  // UXC-1 S3 (Leitplanke 6): deviations from the shipped defaults, decided per consent —
  // NO preselection (the operator chooses actively), nothing leaks from an earlier session.
  const [deviations, setDeviations] = useState<SettingsDeviation[]>([]);
  const [deviationChoice, setDeviationChoice] = useState<"keep" | "reset" | null>(null);

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Each time the confirm opens, reset to the DEFAULT mode so a previous session's choice never
  // leaks into a fresh consent. #2824 (revises #2700's safe-default): the default IS Full
  // autonomy — the product's intended operating mode — made unmistakable by the red marking on
  // the preselected pill below. No equity is read, so nothing can collapse to a mode nobody
  // picked (#2700's invariant holds: the shown mode is exactly what Enable persists).
  useEffect(() => {
    if (!confirming) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHitlMode("full");
    setDeviationChoice(null);
    let alive = true;
    // Promise-hop: routes a synchronous throw (partially mocked bridge in old suites)
    // into .catch — the review block then simply stays absent, never a crash.
    void Promise.resolve()
      .then(() => getSetupState())
      .then((state) => {
        if (alive) setDeviations(computeDeviations(state ?? {}));
      })
      .catch(() => {
        if (alive) setDeviations([]);
      });
    return () => {
      alive = false;
    };
  }, [confirming]);

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
    // UXC-1 S3: ONE reconcile code path — shared with SettingsApplyModal (A4).
    await reconcileEngineMode(expectingLive, switchId, {
      isMounted: () => mountedRef.current,
      setPaperTrading,
      setSwitchError,
      setSwitchReconciling,
      setSwitchConfirmFailed,
      setEngineServing,
    });
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
    // UXC-1 S3 (Leitplanke 6): record the operator's keep/reset decision on the Art-14
    // chain BEFORE anything arms. decision="reset" composes ack + settings event in ONE
    // backend request (Rev. 3 — no split-brain); only after its 201 does setup.json
    // persist the defaults. A failed record stops the switch fail-closed.
    const switchId = crypto.randomUUID();
    if (deviations.length > 0 && deviationChoice) {
      try {
        await postSettingsDeviationAck({
          decision: deviationChoice,
          deviations: deviations.map((d) => ({
            key: d.key,
            current: d.current,
            default: d.default,
          })),
          nonce: crypto.randomUUID(),
          switch_id: switchId,
        });
        if (deviationChoice === "reset") {
          await saveSetupState(defaultsMap(deviations.map((d) => d.key)));
          setDeviations([]);
        }
      } catch {
        setSwitchError(
          "The settings decision could not be recorded — live trading was not enabled.",
        );
        setBusy(false);
        return;
      }
    }
    // Keys are valid+saved (or already present). NOW raise the overlay and go live (steps c+d).
    setModeSwitch({ to: "live", startedAt: Date.now() });
    resetEquityCurves(); // #2465: no €152k paper carryover
    resetAccountSnapshot(); // clear the paper equity/positions so no stale account flashes on reveal
    resetAccountFeeds(); // #2822: paper orders/HITL/decisions must not survive onto the live pages
    setDesktopPage("overview"); // #2465: land directly on the Overview, not Settings
    setEngineServing(false);
    setSidebarStatusMsg("Reconfiguring for Live Environment...");
    try {
      try {
        // #2700: exactly the two policies the picker offers. Both limits stay 0 in BOTH modes —
        // that is the documented "all-manual" pair (hitl_gate.py module docstring), and the ONLY
        // thing that distinguishes the modes is the Art-14 waiver flag. No equity, no percentage,
        // no currency amount is involved, so there is no conversion that can land the operator in
        // a mode they did not choose.
        await updateHitlPolicy({
          HITL_MAX_VALUE_PER_TRADE: 0,
          HITL_MAX_VALUE_PER_DAY: 0,
          HITL_AUTONOMOUS_UNLIMITED: hitlMode === "full",
          // Manual mode queues EVERY buy; a protective sell must never wait behind a human, so it
          // stays exempt there. Under full autonomy nothing is queued and the switch is moot.
          HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: hitlMode === "manual",
          HITL_EXPIRY_SECONDS: 900,
        });
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
    resetAccountFeeds(); // #2822: live orders/HITL/decisions must not survive onto the paper pages
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
  // #2708 D1: CONSUME the signal unconditionally FIRST, so it can never be re-evaluated on a later
  // `paper` change (e.g. when a switch back to paper settles → `paper` flips null→true and the effect
  // re-runs). Then refuse in any unsafe state: unknown account (paper==null), a switch in flight, or
  // busy. An intent to move real money is only valid for the settled state it was formed in.
  const switching = switchReconciling || modeSwitch != null;
  useEffect(() => {
    if (!requestLiveConsent) return;
    setRequestLiveConsent(false);
    if (paper == null || allowLive === false || switching || busy) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (paper === true) setConfirming(true);
  }, [requestLiveConsent, paper, allowLive, busy, switching, setRequestLiveConsent]);

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
    // #2794: plain (non-glass) dim — the panel now carries its own SOLID background, so the
    // backdrop blur that used to show THROUGH the transparent panel (the "glass" look) is dropped.
    // The portal lands on document.body, OUTSIDE the app's `.aaa-console` scope — wrap in that class
    // (ModeSwitchOverlay pattern) so the scoped `.btn` / `.surface-flat` styles apply in here.
    <div className="aaa-console fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      {/* #2794: two-column, no-scroll layout on a solid standard surface (#1c1c1e = the opaque
          .surface colour, sans backdrop-filter). Wider panel (max-w-2xl) so consent (left) and
          approval-mode + live-keys (right) are both visible at a glance without scrolling. */}
      <div className="w-full max-w-2xl rounded-2xl border border-white/10 shadow-2xl bg-[#1c1c1e] flex flex-col max-h-[92vh] overflow-hidden">
        <div className="flex items-center gap-3 px-6 pt-6 pb-4 border-b border-white/10 shrink-0">
          <IconShield width={22} height={22} className="text-white/70" />
          <div>
            <h3 className="text-lg font-bold text-white leading-tight">Enable Live Trading?</h3>
            <p className="text-[12px] text-white/60 leading-snug">
              You are switching the engine to your LIVE Alpaca account — orders execute with{" "}
              <strong className="text-white/90">real money</strong>, and you are responsible for every
              one (EU AI Act Art. 14 — human oversight).
            </p>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-4 p-6 overflow-y-auto">
          {/* LEFT — consent + audit acknowledgements */}
          <div className="space-y-3 min-w-0">
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
            <label className="flex items-start gap-2 text-[11px] text-white/60 leading-snug">
              <input
                type="checkbox"
                aria-label="ack-advance-approval"
                checked={waiver}
                onChange={(e) => setWaiver(e.target.checked)}
                className="mt-0.5 shrink-0"
              />
              <span>{ADVANCE_APPROVAL_WAIVER}</span>
            </label>
            <p className="text-[12px] text-white/60 bg-white/[0.03] border border-white/10 p-2.5 rounded-lg">
              This action is permanently recorded on the WORM compliance audit trail.
            </p>
          </div>
          {/* RIGHT — approval mode + live keys. flex-col so the keys box drops to the bottom
              (mt-auto) and its lower edge lines up exactly with the left column's last box; the
              grid already stretches both columns to equal height. */}
          <div className="flex flex-col gap-4 min-w-0">
            {/* #2700: TWO modes, no value arithmetic. The 0-25% threshold slider (#2671) and the
                per-trade amount input (#2454) are both gone: every value-based mode has to convert
                equity → % → $, and each conversion was a way to end up somewhere the operator did not
                choose. Concretely: with the live keys already in the keychain the wizard never
                re-validated them, so `liveEquity` stayed null, `equity × 25%` collapsed to 0, and a
                deliberately-chosen "full autonomy" silently became all-manual — 13 held orders on
                2026-08-06. A mode you PICK cannot collapse into a different one. */}
            <div className="rounded-lg px-3 py-3 border border-white/10 bg-white/[0.02]">
              <div className="text-[12px] font-semibold text-white/90 mb-2">Approval mode</div>
              <div className="flex gap-2 mb-2.5">
                <button
                  type="button"
                  onClick={() => setHitlMode("manual")}
                  aria-pressed={hitlMode === "manual"}
                  className={`flex-1 text-[12px] font-semibold py-1.5 rounded-full border transition-all ${
                    hitlMode === "manual"
                      ? "bg-white/12 text-white border-white/25"
                      : "bg-white/[0.04] text-white/70 border-white/10 hover:text-white/90"
                  }`}
                >
                  Approve every order
                </button>
                <button
                  type="button"
                  onClick={() => setHitlMode("full")}
                  aria-pressed={hitlMode === "full"}
                  // #2824: when selected (incl. the preselection), Full autonomy wears the SOLID
                  // kill-switch red (Sidebar kill switch / Enable button) — no translucent tint,
                  // glow-style selected states were rejected in the #2035 de-glow round.
                  className={`flex-1 text-[12px] font-semibold py-1.5 rounded-full border transition-all ${
                    hitlMode === "full"
                      ? "bg-[#ff5a52] text-white border-transparent font-bold"
                      : "bg-white/[0.04] text-white/70 border-white/10 hover:text-white/90"
                  }`}
                >
                  Full autonomy
                </button>
              </div>
              {hitlMode === "manual" ? (
                <div className="text-[11px] text-white/50">
                  Nothing reaches the broker without you. Every order waits in Orders → Awaiting
                  approval; protective, risk-reducing sells stay exempt so a stop-out is never blocked
                  behind a human.
                </div>
              ) : (
                <div className="text-[11px] text-white/50">
                  Every order executes automatically — no per-order approval (EU AI Act Art. 14
                  advance-approval waiver). The Iron Dome, the 25%-per-symbol sizing cap and the Kill
                  Switch stay active. Changeable anytime in Settings → Trading.
                </div>
              )}
            </div>
            {/* #4 (plan §3.2): live keys are entered/changed HERE and validated + saved by the Enable
                submit itself (no separate button). Keys are still hosted in Settings; blank = keep the
                stored keys, entering new ones changes them. Validation happens on Enable → inline error. */}
            <div className="mt-auto surface-flat px-3 py-3 space-y-2">
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
            {deviations.length > 0 && (
              <div
                data-testid="defaults-review"
                className="surface-flat px-4 py-3.5 flex flex-col gap-2.5"
              >
                <div className="text-[13px] font-semibold text-white/90">
                  Settings that differ from the shipped defaults
                </div>
                <p className="text-[12px] text-white/60 leading-snug">
                  These values were set by you and stay in effect in live mode unless you
                  reset them. Your choice is recorded on the audit trail.
                </p>
                {deviations.map((d) => (
                  <div
                    key={d.key}
                    className="flex justify-between items-baseline gap-3.5 text-[13px]"
                  >
                    <span className="text-white/70">{d.label}</span>
                    <span className="num text-[13px]">
                      <span className="text-white/90 font-medium">{d.current}</span>
                      <span className="text-white/45 mx-1.5">· default</span>
                      <span className="text-white/45">{d.default}</span>
                    </span>
                  </div>
                ))}
                {/* Owner 03.09.: selection-card pattern (Paper/Live language) instead
                    of white active pills — active card = brighter border + live dot. */}
                <div className="grid grid-cols-2 gap-2.5 mt-1">
                  {(
                    [
                      {
                        id: "keep" as const,
                        title: "Keep my settings",
                        body: "The listed values stay in effect in live mode.",
                      },
                      {
                        id: "reset" as const,
                        title: "Reset to defaults",
                        body: "The listed values return to the shipped defaults.",
                      },
                    ]
                  ).map((opt) => {
                    const active = deviationChoice === opt.id;
                    return (
                      <button
                        key={opt.id}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setDeviationChoice(opt.id)}
                        className={`p-4 rounded-xl border text-left transition-all ${
                          active
                            ? "border-white/20 bg-white/[0.06]"
                            : "border-white/10 hover:border-white/20 bg-white/[0.03]"
                        }`}
                      >
                        <span
                          className={`flex items-center gap-2 text-[12.5px] font-semibold ${
                            active ? "text-white" : "text-white/70"
                          }`}
                        >
                          {opt.title}
                          {active && (
                            <StatusDot tone="on" className="ml-auto !text-[11px]">
                              active
                            </StatusDot>
                          )}
                        </span>
                        <span className="block text-[11px] text-white/40 leading-relaxed mt-1">
                          {opt.body}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
            {switchError && (
              <p className="text-[12px] text-white/80 bg-white/[0.03] border border-white/10 rounded-lg px-3 py-2">{switchError}</p>
            )}
          </div>
        </div>
        <div className="flex justify-end gap-3 p-4 border-t border-white/10 shrink-0">
          <button
            className="btn"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            className="rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase text-white bg-[#ff5a52] hover:bg-[#ff6c65] border-none transition-all transform active:scale-[0.97] disabled:opacity-40 disabled:cursor-not-allowed"
            onClick={() => void toLive()}
            disabled={!ack || !waiver || busy || (liveKeysPresent === false && !keyId.trim()) || (deviations.length > 0 && deviationChoice === null)}
          >
            {busy ? "Enabling…" : "Enable live trading"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
