/**
 * B9: "is the first-run setup done?" as ONE rule, shared by the gate (ConsoleApp) and the wizard.
 *
 * The OS keychain alone cannot answer it: a user who entered the Alpaca keys and abandoned the
 * model step has a filled keychain and no LLM configured, and used to land in the dashboard on the
 * next start. So the wizard writes its own done-mark into the non-secret setup.json at Finish
 * (`setup:save-state`, allow-listed in desktop/electron/setup-manager.cjs, never injected into the
 * engine env), and the gate opens only when that mark is present.
 *
 * Migration: installs from before the mark never wrote it, but every one of them finished the
 * wizard, whose model step writes LLM_PROVIDER. A present LLM_PROVIDER therefore counts as
 * complete, so no existing user is sent back through the wizard.
 */
export const SETUP_COMPLETED_KEY = "setup_completed";

export function isSetupComplete(state: Record<string, unknown> | null | undefined): boolean {
  if (!state || typeof state !== "object") return false;
  if (state[SETUP_COMPLETED_KEY] === true) return true;
  const provider = state.LLM_PROVIDER;
  return typeof provider === "string" && provider.trim() !== "";
}
