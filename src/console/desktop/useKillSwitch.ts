import { useState } from "react";
import { stop as haltEngineApi } from "@/lib/api";
import { resetKillSwitchAndResume } from "@/lib/killSwitch";

/**
 * Shared kill-switch controls (#1642) — used by both Settings and the Overview status bar so the
 * halt/reset logic lives in ONE place.
 *
 * - `handleKill`: two-step arm→confirm (4s window), then POST /stop (halt trading; the engine keeps
 *   running, open positions are left untouched).
 * - `handleResetKill`: clear a risk-tripped kill switch (system_halted) + resume the loop
 *   (/reset-kill-switch then /start-live) — reset and resume are independent steps, each reported
 *   accurately by resetKillSwitchAndResume().
 */
export function useKillSwitch() {
  const [killMsg, setKillMsg] = useState<string | null>(null);
  const [killResetting, setKillResetting] = useState(false);

  async function handleKill() {
    setKillMsg(null);
    try {
      await haltEngineApi();
      setKillMsg("Engine halted — trading stopped. Open positions left untouched.");
    } catch {
      setKillMsg("Could not reach the engine — nothing changed.");
    }
  }

  async function handleResetKill() {
    setKillResetting(true);
    setKillMsg(null);
    const { message } = await resetKillSwitchAndResume();
    setKillMsg(message);
    setKillResetting(false);
  }

  return {
    killArmed: false,
    killMsg,
    killResetting,
    armTimeLeft: null,
    handleKill,
    handleResetKill,
  };
}
