import { useState } from "react";
import { resetKillSwitchAndResume } from "@/lib/killSwitch";
import { useStore } from "@/console/store/useStore";

/**
 * Shared kill-switch controls (#1642) — used by both Settings and the Overview status bar so the
 * halt/reset logic lives in ONE place.
 *
 * - `handleKill` (#2467): a REAL emergency stop. It raises the one-shot `requestSwitchToPaper` signal
 *   so the global LiveConsentModal runs the audited toPaper path — `liveDisable` trips the circuit
 *   breaker (synchronous submit-block + mass-cancel of open orders) + `force_paper` + a WORM disable
 *   record, then restarts fail-closed to Paper and lands on the Overview. (Replaces the old soft
 *   POST /stop, which neither tripped nor cancelled nor wrote WORM — its modal claims were untrue.)
 *   Open positions are left in the account (only resting orders are cancelled).
 * - `handleResetKill`: clear a risk-tripped kill switch (system_halted) + resume the loop
 *   (/reset-kill-switch then /start-live) — reset and resume are independent steps, each reported
 *   accurately by resetKillSwitchAndResume().
 */
export function useKillSwitch() {
  const setSidebarStatusMsg = useStore((s) => s.setSidebarStatusMsg);
  const setRequestSwitchToPaper = useStore((s) => s.setRequestSwitchToPaper);
  const [killResetting, setKillResetting] = useState(false);

  async function handleKill() {
    // #2467: raise the emergency switch-to-paper signal — the global LiveConsentModal owns the
    // audited toPaper path (trip + mass-cancel + force_paper + WORM disable → restart → Paper →
    // Overview). No soft /stop; the confirm gesture lives in the Sidebar/Settings kill modal.
    setSidebarStatusMsg(null);
    setRequestSwitchToPaper(true);
  }

  async function handleResetKill() {
    setKillResetting(true);
    setSidebarStatusMsg(null);
    // CQ-HIGH: surface the ACTUAL outcome — resetKillSwitchAndResume() returns the accurate
    // failure text (e.g. "Could not reach the engine — kill switch not reset.") when the reset
    // failed. The old hardcoded "successful" told the operator the risk-tripped halt was cleared
    // even when it was NOT — a false safety signal (defeats the Archon #1565 protection).
    const { message } = await resetKillSwitchAndResume();
    setSidebarStatusMsg(message);
    setTimeout(() => {
      setSidebarStatusMsg(null);
    }, 5000);
    setKillResetting(false);
  }

  return {
    killArmed: false,
    killResetting,
    armTimeLeft: null,
    handleKill,
    handleResetKill,
  };
}
