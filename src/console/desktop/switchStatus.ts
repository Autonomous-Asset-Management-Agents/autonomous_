// src/console/desktop/switchStatus.ts — #3168: the status line of the Paper↔Live switch screen.
// Pure, so the phase ladder is unit-tested without rendering the overlay.
//
// Why this exists. #2580 replaced the switch screen's single frozen line with phases derived from
// the store, but in practice the operator still saw ONE message, because the ladder asked its
// questions in the wrong order for the real timing:
//   • `engineServing` is set false the moment the switch starts and is only set true again by the
//     health poll, which runs every 10 s (useHealthPolling.ts:6);
//   • the reconcile loop (LiveConsentModal.reconcilePaper) polls /health on its own fast backoff
//     and sets `paperTrading` itself the moment the engine answers.
// So the engine was long back and the account already flipped while the screen still said
// "restarting the engine…", and by the time `engineServing` caught up, the "reconciling…" and
// "switching your account…" phases had been passed unseen.
//
// Two changes: the ladder now reads the FURTHEST evidence available (the account having flipped
// proves the engine answered — no need to wait for the health poll), and the long restart stretch
// is narrated from the engine's own boot log via the boot splash's `bootStatusLabel`, so the
// 40-60 s of "restarting" becomes real progress instead of one frozen line.

import { bootStatusLabel } from "@/console/splash/bootStatus";

export interface SwitchStatusInput {
  /** The reveal has latched — everything is settled. */
  arriving: boolean;
  /** `/health` answered on the console's own poll (lags up to 10 s). */
  engineServing: boolean | null;
  /** The reconcile loop is in flight. */
  switchReconciling: boolean;
  /** `/health.paper_trading` as last seen. */
  paperTrading: boolean | null;
  /** `true` when switching TO paper. */
  targetPaper: boolean;
  /** The account snapshot for the TARGET account has re-synced since the switch began. */
  dataFresh: boolean;
  /** Engine log lines received SINCE the switch started (never the previous boot's). */
  bootLogs: readonly string[];
}

/** Lower-case the first character only — "Loading AI models…" → "loading AI models…" — so the
 *  boot labels match this screen's lower-case voice without mangling "AI". */
function decapitalise(s: string): string {
  return s.length > 0 ? s[0].toLowerCase() + s.slice(1) : s;
}

export function switchStatusLabel(input: SwitchStatusInput): string {
  const { arriving, engineServing, switchReconciling, paperTrading, targetPaper, dataFresh } =
    input;
  const dir = targetPaper ? "paper" : "live";

  if (arriving) return "your account is ready";

  // The account reports the target mode: the engine answered the reconcile loop, so it IS back —
  // regardless of whether the 10 s health poll has noticed yet.
  const accountFlipped = paperTrading === targetPaper;

  if (accountFlipped && !switchReconciling) {
    if (dataFresh && engineServing === true) return `finalising your ${dir} account…`;
    return `syncing your ${dir} account…`;
  }
  if (engineServing === true) {
    if (switchReconciling) return "reconciling your open positions & orders…";
    return `switching your account to ${dir}…`;
  }

  // Still coming back up: narrate the engine's own boot. The first boot phase keeps the
  // direction-flavoured wording, so the operator always knows WHICH way the switch is going.
  const boot = bootStatusLabel(input.bootLogs as string[]);
  const first = bootStatusLabel([]);
  if (boot === first) {
    return targetPaper
      ? "returning to paper — restarting the engine…"
      : "arming live trading — restarting the engine…";
  }
  return decapitalise(boot);
}
