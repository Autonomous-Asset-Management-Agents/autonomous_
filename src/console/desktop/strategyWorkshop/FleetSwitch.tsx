/**
 * Switch for the strategy fleet (STW-2f #5125, owner design of 03.11.2026).
 *
 * A `role="switch"` button, 46 × 27 px. The owner chose a switch for this one card instead of the
 * `OnOffSegment` the design guide names for booleans (deviation in `ux_konzept.md`). It is neither
 * the shadcn Switch primitive nor a checkbox. Off: grey track, knob left. On: the brand green as a tint
 * on the track and solid on the knob, right — no green text on a green tint. It carries no text;
 * the card's title names it and the card's sentence describes it.
 */
export function FleetSwitch({
  checked,
  onChange,
  disabled = false,
  labelledBy,
  describedBy,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  labelledBy: string;
  describedBy: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="relative shrink-0 rounded-[999px] border transition-colors disabled:cursor-not-allowed disabled:opacity-50"
      style={{
        width: 46,
        height: 27,
        borderColor: checked ? "rgba(var(--brand-green-rgb),0.55)" : "rgba(255,255,255,0.14)",
        background: checked ? "rgba(var(--brand-green-rgb),0.22)" : "rgba(255,255,255,0.06)",
      }}
    >
      <span
        aria-hidden="true"
        className="absolute top-1/2 -translate-y-1/2 rounded-[999px] transition-all"
        style={{
          width: 19,
          height: 19,
          left: checked ? 22 : 3,
          background: checked ? "var(--brand-green)" : "rgba(255,255,255,0.45)",
        }}
      />
    </button>
  );
}
