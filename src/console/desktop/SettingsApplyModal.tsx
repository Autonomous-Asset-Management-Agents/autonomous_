import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { restartEngine } from "@/lib/desktopBridge";
import { useStore } from "@/console/store/useStore";
import { reconcileEngineMode } from "@/lib/switchReconcile";
import { liveSizingAck } from "@/console/desktop/strategyWorkshop/acknowledgments";

/**
 * SettingsApplyModal (UXC-1 S3, #3156) — the SHARED WORM-audited confirm for every
 * settings change (generalisation of PortfolioShapeModal, #3132/#3133 pattern):
 *
 *   confirm → record on the WORM chain → persist → restart
 *
 * **Why that order.** The record function throws when the chain is not writable —
 * then nothing below runs (`strict=True` server-side). The remaining risk is the
 * safe one: a record without a change is harmless, a change without a record is not.
 *
 * **Live-mode branch (A4 fix).** An engine restart without the goLive directive
 * always boots PAPER (`native-engine-manager.cjs` one-shot AND-gated with the WORM
 * verify). So in live mode this modal (a) demands an explicit live re-consent
 * checkbox — without it no apply — and (b) restarts with `{ goLive: true }`, then
 * reconciles EXPECTING live and surfaces `live_enable_failed_reason`. Paper mode
 * restarts without the directive, exactly as before. Defense-in-depth in the shell
 * is untouched — this consumer merely uses the existing seam correctly.
 *
 * **Live sizing-method change (#4989).** The live consent holds for one method, so a
 * change of `CLEAN_WEIGHT_SIZING` in live mode needs a second confirmation naming the
 * old and the new method. Its text reaches `record` as the acknowledgment; the engine
 * refuses the change with any other text (409). Paper mode is unchanged.
 *
 * Record/persist arrive as props: the #3132 portfolio-shape endpoint today, the
 * generic S2 `POST /api/trading-settings` for the S4/S5 cards.
 */

export interface SettingsApplyChange {
  key: string;
  label: string;
  from: string;
  to: string;
}

export interface SettingsApplyModalProps {
  open: boolean;
  changes: SettingsApplyChange[];
  /** Writes the WORM record (throws on failure — nothing below runs then). `sizingAck` is
   *  set only for a confirmed live sizing-method change: send it as the acknowledgment. */
  record: (nonce: string, sizingAck?: string) => Promise<void>;
  /** Persists to setup.json AFTER the audited record (1:1 passthrough, #2863). */
  persist: () => Promise<void>;
  onCancel: () => void;
  onApplied: () => void;
  /** Optional heading override (defaults to the generic settings wording). */
  title?: string;
  description?: string;
  /** #4630: shown INSTEAD of the diff box when `changes` is empty (an assignment is
   *  recorded even without a value change). Without it the modal renders as before. */
  emptyHint?: string;
  /** #4953: restarts another engine than the live profile's (a paper slot). With it the
   *  modal has no live branch at all: no re-consent, no `restartEngine`, no goLive, no
   *  reconcile. Without it the modal behaves as before. */
  restart?: (nonce: string) => Promise<void>;
}

const LIVE_RECONSENT =
  "I confirm the engine restarts and afterwards continues trading LIVE with real money.";

const SIZING_METHOD_KEY = "CLEAN_WEIGHT_SIZING";

export function SettingsApplyModal({
  open,
  changes,
  record,
  persist,
  onCancel,
  onApplied,
  title,
  description,
  emptyHint,
  restart,
}: SettingsApplyModalProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [liveConsent, setLiveConsent] = useState(false);
  const [confirmedSizingAck, setConfirmedSizingAck] = useState<string | null>(null);
  const mountedRef = useRef(true);

  const paper = useStore((s) => s.paperTrading);
  const setPaperTrading = useStore((s) => s.setPaperTrading);
  const setSwitchError = useStore((s) => s.setSwitchError);
  const setSwitchReconciling = useStore((s) => s.setSwitchReconciling);
  const setSwitchConfirmFailed = useStore((s) => s.setSwitchConfirmFailed);
  const setEngineServing = useStore((s) => s.setEngineServing);

  if (!open) return null;

  const isLive = !restart && paper === false;
  const sizingChange = isLive ? changes.find((c) => c.key === SIZING_METHOD_KEY) : undefined;
  const sizingAck = sizingChange ? liveSizingAck(sizingChange.from, sizingChange.to) : undefined;
  const blocked =
    busy || (isLive && !liveConsent) || (sizingAck !== undefined && confirmedSizingAck !== sizingAck);

  const onConfirm = async () => {
    setBusy(true);
    setError(null);
    try {
      // Replay-distinct: the engine rejects a reused nonce with 409 BEFORE any write,
      // so a double-click cannot append a second record for one decision.
      const nonce =
        globalThis.crypto?.randomUUID?.() ??
        `${Date.now()}-${Math.random().toString(36).slice(2)}`;

      // 1. Record. Throws if the chain is not writable — then nothing below runs.
      await record(nonce, sizingAck);

      // 2. Persist under the exact engine flag names (setup.json 1:1 passthrough).
      await persist();

      // 3. Restart so the change becomes effective (values are read at engine boot).
      //    The nonce doubles as the switch id (#2277 INC-1). Live mode carries the
      //    one-shot goLive directive (A4); paper mode never does.
      if (restart) await restart(nonce);
      else await restartEngine(nonce, isLive ? { goLive: true } : undefined);

      if (isLive) {
        await reconcileEngineMode(true, nonce, {
          isMounted: () => mountedRef.current,
          setPaperTrading,
          setSwitchError,
          setSwitchReconciling,
          setSwitchConfirmFailed,
          setEngineServing,
        });
      }

      onApplied();
    } catch (e) {
      setError(
        e instanceof Error && e.message
          ? e.message
          : "The change could not be recorded. Nothing was applied.",
      );
    } finally {
      setBusy(false);
    }
  };

  // Portal to <body> so the fixed overlay centres on the VIEWPORT, not on whatever
  // ancestor happens to establish a containing block (a transform/filter/contain on
  // any parent traps `position: fixed`, which made this modal render up-page where the
  // operator had to scroll to find it). document.body is always present in the app and
  // in jsdom; guarded for a non-DOM SSR pass.
  if (typeof document === "undefined") return null;
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title ?? "Apply trading settings"}
    >
      <div className="surface p-6 w-full max-w-[520px]">
        <div className="flex gap-3 items-start mb-3.5">
          <span className="text-white/70 text-[19px] leading-tight" aria-hidden="true">
            ⚠
          </span>
          <div>
            <h3 className="text-lg font-bold text-white leading-tight">
              {title ?? "Apply trading settings?"}
            </h3>
            <p className="mt-1.5 text-[13px] text-white/70 leading-snug">
              {description ??
                "These settings apply to all future trading decisions. The engine restarts so the change takes effect."}
            </p>
          </div>
        </div>

        {changes.length === 0 && emptyHint ? (
          <p className="surface-flat px-4 py-3.5 my-3.5 text-[13px] text-white/70 leading-snug">
            {emptyHint}
          </p>
        ) : (
          <div className="surface-flat px-4 py-3.5 my-3.5 flex flex-col gap-2.5">
            {changes.map((c) => (
              <div
                key={c.key}
                className="flex justify-between items-baseline gap-3.5 text-[13px]"
              >
                <span className="text-white/70">{c.label}</span>
                <span className="num text-[13px]">
                  <span className="text-white/45">{c.from}</span>
                  <span className="text-white/45 mx-1.5">→</span>
                  <span className="text-white/90 font-medium">{c.to}</span>
                </span>
              </div>
            ))}
          </div>
        )}

        <p className="text-[13px] text-white/70 leading-snug border-t border-white/8 pt-3">
          The change is recorded with timestamp, previous value and new value.
        </p>

        {isLive && (
          <label className="mt-3 flex items-start gap-2.5 surface-flat px-4 py-3 text-[13px] text-white/80 leading-snug cursor-pointer">
            <input
              type="checkbox"
              role="checkbox"
              aria-label={LIVE_RECONSENT}
              checked={liveConsent}
              onChange={(e) => setLiveConsent(e.target.checked)}
              className="mt-0.5 accent-[#00c27a]"
            />
            <span>{LIVE_RECONSENT}</span>
          </label>
        )}

        {sizingAck !== undefined && (
          <label className="mt-3 flex items-start gap-2.5 surface-flat px-4 py-3 text-[13px] text-white/80 leading-snug cursor-pointer">
            <input
              type="checkbox"
              role="checkbox"
              aria-label={sizingAck}
              checked={confirmedSizingAck === sizingAck}
              onChange={(e) => setConfirmedSizingAck(e.target.checked ? sizingAck : null)}
              className="mt-0.5 accent-[#00c27a]"
            />
            <span>{sizingAck}</span>
          </label>
        )}

        {error && (
          <p
            className="mt-3 surface-flat px-4 py-3 text-[13px] text-white/80"
            role="alert"
          >
            {error}
          </p>
        )}

        <div className="flex gap-2.5 justify-end mt-4">
          <button type="button" onClick={onCancel} disabled={busy} className="btn">
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void onConfirm()}
            disabled={blocked}
            className="rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase text-white bg-[#ff5a52] hover:bg-[#ff6c65] border-none transition-all transform active:scale-[0.97] disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {busy ? "Recording…" : "Change and restart"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
