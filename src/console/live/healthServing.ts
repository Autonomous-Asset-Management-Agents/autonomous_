// Debounced engine "serving" signal (pure, testable).
//
// The engine's async event loop can stall for SECONDS under compute load (observed: /health median
// 1.5s, up to 16s; other endpoints up to 97s). A single slow/failed /health poll made the old logic
// (`engineServing = !!h && h.status !== "starting"`) flap `engineServing` to FALSE — which the Sidebar
// renders as a false "engine starting… (a few seconds)" on the green button, even though the engine is
// up and merely slow. This debounces the true→false transition: an unreachable/timeout poll is treated
// as TRANSIENT and keeps the last value until `threshold` consecutive failures. An AFFIRMATIVE
// status:"starting" (a genuine boot window where the engine reports itself not-ready) still flips to
// not-serving IMMEDIATELY — that is a real "starting", not a stall.

export interface ServingInput {
  /** the current engineServing value in the store */
  current: boolean;
  /** the poll reached the engine and got a response (fetchHealth returned non-null) */
  reachable: boolean;
  /** the engine AFFIRMATIVELY reported status:"starting" (only meaningful when reachable) */
  starting: boolean;
  /** consecutive unreachable-poll count so far */
  failStreak: number;
}

export interface ServingResult {
  serving: boolean;
  failStreak: number;
}

/**
 * @param threshold consecutive unreachable polls required before declaring not-serving (default 3;
 *   at POLL_MS=10s that is ~30s, so a genuine outage is still detected, but a single ~16s stall or a
 *   slow first poll after a reload does not produce a false "engine starting").
 */
export function nextServingState(
  { current, reachable, starting, failStreak }: ServingInput,
  threshold = 3,
): ServingResult {
  if (reachable) {
    // The engine answered: it is serving unless it explicitly reports it is still starting.
    return { serving: !starting, failStreak: 0 };
  }
  // Unreachable/timeout — transient. Keep the last value until enough consecutive failures accrue.
  const streak = failStreak + 1;
  return { serving: streak >= threshold ? false : current, failStreak: streak };
}
