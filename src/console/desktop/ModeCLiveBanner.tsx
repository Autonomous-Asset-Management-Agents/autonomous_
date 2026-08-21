/**
 * H2 (#2370): persistent, non-dismissible bottom-left disclosure that UNLIMITED autonomous
 * LIVE trading (Mode C) is active. Shown ONLY when the engine is live AND
 * HITL_AUTONOMOUS_UNLIMITED is on (see useModeCLive). Its continuous visibility (+ the Kill
 * Switch directly below it in the sidebar footer) is the operator-oversight substitute for a
 * per-restart re-consent (EU AI Act Art. 14) — the operator always sees real-money autonomy is on.
 */
export function ModeCLiveBanner({ active }: { active: boolean }) {
  if (!active) return null;
  return (
    <div
      role="alert"
      className="rounded-lg border border-[#ff5a52]/40 bg-[#ff5a52]/10 px-3 py-2 text-[11px] leading-snug text-[#ffb4af]"
    >
      <span className="font-semibold text-[#ff5a52]">
        ⚠️ Autonomous live trading active (Mode C).
      </span>{" "}
      You authorized unlimited autonomous execution with real capital; this authorization
      remains in effect across restarts until you disable it. Oversight is yours — Kill
      Switch anytime.
    </div>
  );
}
