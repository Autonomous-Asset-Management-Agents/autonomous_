import { resetKillSwitch, startLive } from "./api";

/**
 * Reset a risk-tripped kill switch and resume the trading loop, returning an accurate
 * user-facing outcome.
 *
 * The two backend steps are INDEPENDENT: clearing the kill switch (POST /reset-kill-switch)
 * and restarting the loop (POST /start-live). If the reset fails, nothing changed. If the
 * reset SUCCEEDS but the resume fails, the switch IS already cleared — we must report that,
 * never a false "kill switch not reset" (Archon PR #1565, dim. 5: misleading false-negative).
 */
export async function resetKillSwitchAndResume(): Promise<{
  reset: boolean;
  message: string;
}> {
  try {
    await resetKillSwitch();
  } catch {
    return {
      reset: false,
      message: "Could not reach the engine — kill switch not reset.",
    };
  }
  try {
    await startLive();
    return { reset: true, message: "Kill switch reset — trading resumed." };
  } catch {
    return {
      reset: true,
      message:
        "Kill switch reset, but the engine did not restart — press Start engine.",
    };
  }
}
