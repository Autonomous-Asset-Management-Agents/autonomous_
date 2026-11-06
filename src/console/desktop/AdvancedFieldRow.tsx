import { useState } from "react";
import { OnOffSegment } from "@/console/shared/SegmentPicker";
import { Listbox } from "@/console/shared/Listbox";
import { numEq, type AdvancedField } from "@/console/desktop/advancedTradingFields";
import { formatValue, fromDisplay, toDisplay, unitSymbol } from "@/console/desktop/fieldUnits";

interface AdvancedFieldRowProps {
  field: AdvancedField;
  value: string;
  dirty: boolean;
  /** #4987: set while the field does nothing in the pending sizing method (`inertHint`). */
  lockHint?: string | null;
  /** #4940: the registry's choices; replace the fixed `field.choices` when set. */
  choices?: string[];
  /** #4999: the registry's decimals (`meta`); a typed value is rounded to them. */
  decimals?: number;
  /** #5001: the registry default. Shown while `dirty`; its reset goes through `onChange`,
   *  so it is a pending value like any other edit. */
  defaultValue?: string;
  /** #5001: cross-field messages of this field. */
  errors?: string[];
  onChange: (value: string) => void;
}

/** #5001: a default as the row reads, not as it is stored. */
function defaultText(f: AdvancedField, raw: string): string {
  if (f.kind === "toggle") return raw === "true" ? "On" : "Off";
  if (f.kind === "select") return f.choiceLabels?.[raw] ?? raw;
  if (f.kind === "text" && raw === "") return "empty";
  return formatValue(f.unit, raw);
}

/** #4999: text being typed. It belongs to the value it was started on (`base`), so a slider
 *  move or a reset replaces it. */
interface Draft {
  text: string;
  base: string;
  refused: boolean;
}

export function AdvancedFieldRow({
  field: f,
  value: v,
  dirty,
  lockHint,
  choices,
  decimals,
  defaultValue,
  errors = [],
  onChange,
}: AdvancedFieldRowProps) {
  const locked = Boolean(lockHint);
  const [draft, setDraft] = useState<Draft | null>(null);
  const activeDraft = draft?.base === v ? draft : null;
  const refused = activeDraft?.refused ?? false;
  const symbol = unitSymbol(f.unit);

  // Taken over on leaving or Enter, not per keystroke: on the way to 45, the 4 is a value too.
  const commit = () => {
    if (!activeDraft) return;
    const stored = fromDisplay(f.unit, activeDraft.text, decimals);
    if (stored === null || Number(stored) < (f.min as number) || Number(stored) > (f.max as number)) {
      setDraft({ ...activeDraft, refused: true });
      return;
    }
    setDraft(null);
    if (!numEq(stored, v)) onChange(stored);
  };

  return (
    <div data-key={f.key} className="py-3 border-t border-white/6">
      <div className="flex items-start justify-between gap-4">
        <div className="max-w-sm">
          <div className="text-[13px] font-semibold text-white/90">
            {f.label}
            {dirty && (
              <span className="ml-2 rounded-full bg-white/8 px-2 py-0.5 text-[10px] font-medium text-white/60 align-middle">
                Custom
              </span>
            )}
          </div>
          <div data-copy="description" className="text-[11.5px] text-white/45 leading-relaxed mt-0.5">{f.desc}</div>
          {dirty && defaultValue !== undefined && (
            <div className="flex items-baseline gap-2 text-[11.5px] text-white/45 mt-1">
              <span data-copy="default">Default: {defaultText(f, defaultValue)}</span>
              <button
                type="button"
                aria-label={`Reset ${f.label} to default`}
                disabled={locked}
                onClick={() => onChange(defaultValue)}
                className="underline hover:text-white/70 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Reset
              </button>
            </div>
          )}
          {lockHint && (
            <div data-copy="inert-hint" className="text-[11.5px] text-white/70 leading-relaxed mt-1">{lockHint}</div>
          )}
        </div>
        <div className="flex items-center gap-4">
          {f.kind === "toggle" && (
            <OnOffSegment ariaLabel={f.label} value={v === "true"} disabled={locked} onChange={(next) => onChange(next ? "true" : "false")} />
          )}
          {f.kind === "select" && (
            <Listbox
              ariaLabel={f.label}
              value={v}
              disabled={locked}
              onChange={onChange}
              className="min-w-56"
              options={(choices ?? f.choices ?? []).map((c) => ({
                value: c,
                label: f.choiceLabels?.[c] ?? c,
              }))}
            />
          )}
          {f.kind === "slider" && (
            <span data-copy="value" className="num text-[12px] text-white/70 min-w-12 text-right whitespace-nowrap">
              {formatValue(f.unit, v)}
            </span>
          )}
        </div>
      </div>
      {f.kind === "slider" && (
        <div className="flex items-center gap-3 mt-3">
          <span data-copy="bound" className="num text-[10.5px] text-white/30">{formatValue(f.unit, String(f.min))}</span>
          <input
            type="range"
            className="flex-1 accent-[#00c27a] disabled:cursor-not-allowed disabled:opacity-40"
            min={f.min}
            max={f.max}
            step={f.step}
            value={Number(v) || f.min}
            disabled={locked}
            onChange={(e) => {
              setDraft(null);
              onChange(e.target.value);
            }}
            aria-label={f.label}
          />
          <span data-copy="bound" className="num text-[10.5px] text-white/30">{formatValue(f.unit, String(f.max))}</span>
          <span className="flex items-center gap-1.5">
            {f.unit === "usd" && <span className="num text-[11.5px] text-white/45">{symbol}</span>}
            <input
              type="text"
              inputMode="decimal"
              aria-label={`${f.label} value`}
              aria-invalid={refused || errors.length > 0}
              value={activeDraft?.text ?? toDisplay(f.unit, v)}
              disabled={locked}
              onChange={(e) => setDraft({ text: e.target.value, base: v, refused: false })}
              onBlur={commit}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit();
              }}
              className="num w-20 rounded-lg bg-black/40 border border-white/12 px-2 py-1 text-right text-[12px] text-white/90 disabled:cursor-not-allowed disabled:opacity-50"
            />
            {f.unit !== "usd" && symbol && <span className="num text-[11.5px] text-white/45">{symbol}</span>}
          </span>
        </div>
      )}
      {refused && (
        <div role="alert" className="text-[11.5px] text-[#ffb4af] mt-1">
          Not taken over. Enter a value between {formatValue(f.unit, String(f.min))} and {formatValue(f.unit, String(f.max))}.
        </div>
      )}
      {f.kind === "text" && (
        // #4660: no placeholder and no example text (ADR-027 rule 1).
        <textarea
          aria-label={f.label}
          value={v}
          maxLength={f.maxLength}
          rows={4}
          disabled={locked}
          onChange={(e) => onChange(e.target.value)}
          className="mt-3 w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 disabled:cursor-not-allowed disabled:opacity-50"
        />
      )}
      {errors.map((e) => (
        <div key={e} role="alert" className="text-[11.5px] text-[#FF453A] leading-relaxed mt-1">
          {e}
        </div>
      ))}
    </div>
  );
}
