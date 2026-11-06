/**
 * Listbox — the console's own dropdown (owner decision 01.11.2026, variant C).
 *
 * A native `<select>` styles only its closed button: its option list is drawn by
 * the operating system, so on Windows it opens as a light panel with a blue
 * highlight in the middle of the dark console. No CSS of the app reaches an
 * `<option>`; the list has to be ours. The closed control keeps the shape the
 * native one had, so no layout and no wording changes with it.
 *
 * Grammar (SYSTEM_DESIGN_GUIDE §250–252): selected = `bg-white/12 text-white`,
 * unselected = `text-white/70`, never an accent tint. The panel recipe is
 * `.listbox-panel` in console.css. Boolean settings stay `OnOffSegment`, a short
 * neutral choice stays `SegmentPicker`; this control is for the longer lists
 * (four and five options, free-form strategy names) that do not fit a segment row.
 *
 * Enforced by `src/test/designGuard.test.ts` rule G9: no `<select>` in src/console.
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

export interface ListboxOption {
  value: string;
  label: string;
  /** Shown but not choosable (#4987 `lockedChoices`). */
  disabled?: boolean;
  /** Heading this option belongs under — the former `<optgroup>`. */
  group?: string;
}

interface ListboxProps {
  ariaLabel: string;
  options: ListboxOption[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  /** Shown while `value` matches no option. */
  placeholder?: string;
  /** Extra classes on the trigger — widths belong to the call site. */
  className?: string;
  id?: string;
}

const TYPEAHEAD_MS = 800;

export function Listbox({
  ariaLabel,
  options,
  value,
  onChange,
  disabled,
  placeholder = "",
  className = "",
  id,
}: ListboxProps) {
  const base = useId();
  const listId = `${base}-list`;
  const optionId = (index: number) => `${base}-opt-${index}`;

  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const typed = useRef({ text: "", at: 0 });

  const enabled = options.map((o, i) => (o.disabled ? -1 : i)).filter((i) => i >= 0);
  const selectedIndex = options.findIndex((o) => o.value === value);
  const selected = selectedIndex >= 0 ? options[selectedIndex] : null;

  // The list is rendered in declaration order; a group only adds a heading around
  // the run of options that share it, so the reading order never reshuffles.
  const blocks: { group?: string; items: { opt: ListboxOption; index: number }[] }[] = [];
  options.forEach((opt, index) => {
    const last = blocks[blocks.length - 1];
    if (last && last.group === opt.group) last.items.push({ opt, index });
    else blocks.push({ group: opt.group, items: [{ opt, index }] });
  });

  const oeffnen = () => {
    if (disabled) return;
    setActive(selectedIndex >= 0 && !options[selectedIndex].disabled ? selectedIndex : (enabled[0] ?? 0));
    setOpen(true);
  };

  const schliessen = (zurueckFokussieren: boolean) => {
    setOpen(false);
    if (zurueckFokussieren) triggerRef.current?.focus();
  };

  const waehlen = (index: number) => {
    const opt = options[index];
    if (!opt || opt.disabled) return;
    schliessen(true);
    if (opt.value !== value) onChange(opt.value);
  };

  const bewegen = (delta: number) => {
    if (enabled.length === 0) return;
    const pos = enabled.indexOf(active);
    const next = pos < 0 ? 0 : Math.min(enabled.length - 1, Math.max(0, pos + delta));
    setActive(enabled[next]);
  };

  // Typeahead without a timer: the buffer ages by wall clock, so nothing is left
  // running when the control unmounts mid-word.
  const tippen = (key: string) => {
    const now = Date.now();
    typed.current = {
      text: now - typed.current.at > TYPEAHEAD_MS ? key : typed.current.text + key,
      at: now,
    };
    const needle = typed.current.text.toLowerCase();
    const hit = enabled.find((i) => options[i].label.toLowerCase().startsWith(needle));
    if (hit !== undefined) setActive(hit);
  };

  const beiTaste = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " ", "Home", "End"].includes(e.key)) {
        e.preventDefault();
        oeffnen();
      }
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      bewegen(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      bewegen(-1);
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(enabled[0] ?? 0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(enabled[enabled.length - 1] ?? 0);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      waehlen(active);
    } else if (e.key === "Escape") {
      e.preventDefault();
      schliessen(true);
    } else if (e.key === "Tab") {
      setOpen(false);
    } else if (e.key.length === 1) {
      tippen(e.key);
    }
  };

  useEffect(() => {
    if (!open) return;
    const beiMausUnten = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", beiMausUnten);
    return () => document.removeEventListener("mousedown", beiMausUnten);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const el = document.getElementById(optionId(active));
    // jsdom has no scrollIntoView; the guard keeps the unit tests honest.
    if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest" });
  });

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        id={id}
        type="button"
        role="combobox"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? optionId(active) : undefined}
        data-value={value}
        disabled={disabled}
        onClick={() => (open ? schliessen(false) : oeffnen())}
        onKeyDown={beiTaste}
        className={`flex items-center justify-between gap-2 rounded-lg bg-black/40 border px-3 py-2 text-[13px] text-left disabled:cursor-not-allowed disabled:opacity-50 ${
          open ? "border-white/20 text-white" : "border-white/12 text-white/90"
        } ${className}`}
      >
        <span className="truncate">{selected ? selected.label : placeholder}</span>
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="shrink-0 text-white/55"
          aria-hidden="true"
        >
          <path d={open ? "M6 15l6-6 6 6" : "M6 9l6 6 6-6"} />
        </svg>
      </button>

      {open && (
        // No aria-label here: the combobox carries the name, and a second element
        // under the same name makes the control ambiguous to pick — for a screen
        // reader as much as for a test.
        <ul id={listId} role="listbox" className="listbox-panel">
          {blocks.map((block) =>
            block.group === undefined ? (
              block.items.map(({ opt, index }) => (
                <Zeile
                  key={opt.value}
                  opt={opt}
                  id={optionId(index)}
                  selected={opt.value === value}
                  active={index === active}
                  onPick={() => waehlen(index)}
                  onHover={() => !opt.disabled && setActive(index)}
                />
              ))
            ) : (
              <li key={block.group} role="presentation">
                <div className="px-2.5 pt-2 pb-1 text-[10.5px] uppercase tracking-[0.09em] text-white/35">
                  {block.group}
                </div>
                <ul role="group" aria-label={block.group} className="list-none p-0 m-0">
                  {block.items.map(({ opt, index }) => (
                    <Zeile
                      key={opt.value}
                      opt={opt}
                      id={optionId(index)}
                      selected={opt.value === value}
                      active={index === active}
                      onPick={() => waehlen(index)}
                      onHover={() => !opt.disabled && setActive(index)}
                    />
                  ))}
                </ul>
              </li>
            ),
          )}
        </ul>
      )}
    </div>
  );
}

function Zeile({
  opt,
  id,
  selected,
  active,
  onPick,
  onHover,
}: {
  opt: ListboxOption;
  id: string;
  selected: boolean;
  active: boolean;
  onPick: () => void;
  onHover: () => void;
}) {
  const ton = opt.disabled
    ? "text-white/25 cursor-not-allowed"
    : selected
      ? "bg-white/12 text-white cursor-pointer"
      : active
        ? "bg-white/6 text-white/90 cursor-pointer"
        : "text-white/70 cursor-pointer";
  return (
    <li
      id={id}
      role="option"
      aria-selected={selected}
      aria-disabled={opt.disabled || undefined}
      data-value={opt.value}
      onClick={onPick}
      onMouseMove={onHover}
      className={`flex items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-[13px] ${ton}`}
    >
      <span className="truncate">{opt.label}</span>
      {selected && (
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="shrink-0"
          aria-hidden="true"
        >
          <path d="M20 6L9 17l-5-5" />
        </svg>
      )}
    </li>
  );
}
