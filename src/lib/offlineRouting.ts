/**
 * Pure decision for the desktop health-poll → /offline routing (operator finding #8).
 *
 * Extracted from App.tsx's inline health-poll debounce so the "suppress /offline
 * during a deliberate paper↔live switch" rule is unit-testable in isolation
 * (mirrors the engineStatusEffect extraction, LSR R8 #2261).
 *
 * Why it exists: switching Paper↔Live restarts the engine, which briefly refuses
 * connections (~60s to serve again on slow machines). That EXPECTED downtime must
 * never flash the red "Backend Offline" screen — the operator deliberately asked
 * for the switch. While a switch is in progress (`modeSwitchActive`) we never route
 * to /offline no matter how many polls fail; the ModeSwitchOverlay owns the screen
 * and clears itself once the engine serves again (heartbeat → wordmark → reveal) or
 * a safety timeout fires. Only AFTER the switch clears — a genuinely failed/timed-out
 * switch — does normal offline routing resume.
 */
export interface OfflineRoutingArgs {
  /** Consecutive failed /health polls so far. */
  consecutiveFail: number;
  /** Debounce threshold — declare offline only after this many consecutive fails. */
  threshold: number;
  /** True when the app is already on the /offline screen (never double-navigate). */
  alreadyOffline: boolean;
  /** True while a deliberate Paper↔Live switch is in progress (store.modeSwitch set). */
  modeSwitchActive: boolean;
}

/**
 * Returns true only when the operator should be sent to the /offline recovery
 * screen. A deliberate mode switch always returns false (expected restart); an
 * already-offline app never re-navigates; otherwise the debounce threshold gates it.
 */
export function shouldRouteOffline({
  consecutiveFail,
  threshold,
  alreadyOffline,
  modeSwitchActive,
}: OfflineRoutingArgs): boolean {
  // A deliberate switch restarts the engine — an EXPECTED transient, never /offline.
  if (modeSwitchActive) return false;
  // Already on the recovery screen — nothing to do.
  if (alreadyOffline) return false;
  // Genuine sustained unreachability.
  return consecutiveFail >= threshold;
}
