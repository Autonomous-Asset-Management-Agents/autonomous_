/**
 * UXC-1 S8 (#3177): SegmentPicker — the neutral segmented control of the
 * "shape follows criticality" grammar (owner decision 03.09.2026).
 *
 * Replaces the iOS Switch and the translucent-green filter chips: every
 * option is visible, the selected segment sits on white/12 with white text
 * (SYSTEM_DESIGN_GUIDE §250), never an accent tint (§252). Critical/arming
 * choices do NOT use this control — they stay solid red/green pills.
 */

export interface SegmentOption {
  id: string;
  label: string;
}

export function SegmentPicker({
  ariaLabel,
  options,
  value,
  onChange,
  disabled,
}: {
  ariaLabel: string;
  options: SegmentOption[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="inline-flex flex-none rounded-[10px] border border-white/10 bg-white/[0.03] p-0.5"
    >
      {options.map((opt) => {
        const selected = opt.id === value;
        return (
          <button
            key={opt.id}
            type="button"
            aria-pressed={selected}
            disabled={disabled}
            onClick={() => {
              if (!selected && !disabled) onChange(opt.id);
            }}
            className={`rounded-lg px-3.5 py-1 text-[12px] font-semibold transition-all disabled:cursor-not-allowed disabled:opacity-50 ${
              selected ? "bg-white/12 text-white" : "text-white/45 hover:text-white/80"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

const ON_OFF: SegmentOption[] = [
  { id: "on", label: "On" },
  { id: "off", label: "Off" },
];

/** Boolean wrapper — the console's toggle. Both states visible, no track/thumb. */
export function OnOffSegment({
  ariaLabel,
  value,
  onChange,
  disabled,
}: {
  ariaLabel: string;
  value: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <SegmentPicker
      ariaLabel={ariaLabel}
      options={ON_OFF}
      value={value ? "on" : "off"}
      onChange={(id) => onChange(id === "on")}
      disabled={disabled}
    />
  );
}
