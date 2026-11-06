import { getSetupState } from "@/lib/desktopBridge";

/**
 * The operator's fleet choice (STW-2f #5125): only a boolean `strategyProfilesEnabled` in setup.json
 * counts; anything else, or a failing read, is no choice (null) and the runtime decides. Shared by
 * "Paper accounts" and "Trading keys" (#5176; moved unchanged out of PaperAccountSlots.tsx).
 */
export async function readChoice(): Promise<boolean | null> {
  try {
    const s = await getSetupState();
    return typeof s.strategyProfilesEnabled === "boolean" ? s.strategyProfilesEnabled : null;
  } catch (err) {
    console.warn("paperAccountSlots: getSetupState failed → no operator choice, runtime decides", err);
    return null;
  }
}
