/**
 * Test helpers for the console `Listbox` (owner decision 01.11.2026, variant C).
 *
 * A native `<select>` was driven with `fireEvent.change(…, { target: { value } })`.
 * The console's own dropdown is a combobox button plus a list, so a choice is two
 * events: open, then click the row. These helpers keep that one line at the call
 * site, and they fail loudly with the available values when a value is missing —
 * a silent no-op was the old failure mode of `fireEvent.change` with a bad value.
 *
 * `trigger` is always the element `getByLabelText(<label>)` returns.
 */
import { fireEvent } from "@testing-library/react";

function panelVon(trigger: HTMLElement): HTMLElement | null {
  return trigger.parentElement?.querySelector<HTMLElement>("[role=listbox]") ?? null;
}

function name(trigger: HTMLElement): string {
  return trigger.getAttribute("aria-label") ?? "(ohne Label)";
}

/** The raw value the control currently holds — the former `select.value`. */
export function wert(trigger: HTMLElement): string {
  return trigger.dataset.value ?? "";
}

/** Opens the list, picks the option with that raw value, and lets it close. */
export function waehle(trigger: HTMLElement, value: string): void {
  if (trigger.getAttribute("aria-expanded") !== "true") fireEvent.click(trigger);
  const panel = panelVon(trigger);
  if (!panel) throw new Error(`Listbox "${name(trigger)}" hat sich nicht geoeffnet`);
  const option = panel.querySelector<HTMLElement>(`[role=option][data-value="${value}"]`);
  if (!option) {
    const da = Array.from(panel.querySelectorAll<HTMLElement>("[role=option]")).map((o) => o.dataset.value);
    throw new Error(`Option "${value}" fehlt in "${name(trigger)}" — vorhanden: ${JSON.stringify(da)}`);
  }
  fireEvent.click(option);
}

function imOffenenZustand<T>(trigger: HTMLElement, lesen: (panel: HTMLElement) => T): T {
  const warOffen = trigger.getAttribute("aria-expanded") === "true";
  if (!warOffen) fireEvent.click(trigger);
  const panel = panelVon(trigger);
  if (!panel) throw new Error(`Listbox "${name(trigger)}" hat sich nicht geoeffnet`);
  const ergebnis = lesen(panel);
  if (!warOffen) fireEvent.click(trigger);
  return ergebnis;
}

/** The raw values on offer, in order — the former `Array.from(select.options)`. */
export function optionen(trigger: HTMLElement): string[] {
  return imOffenenZustand(trigger, (panel) =>
    Array.from(panel.querySelectorAll<HTMLElement>("[role=option]")).map((o) => o.dataset.value ?? ""),
  );
}

/** The caption shown for a raw value — the former `option.textContent`. */
export function optionText(trigger: HTMLElement, value: string): string {
  return imOffenenZustand(trigger, (panel) => {
    const option = panel.querySelector<HTMLElement>(`[role=option][data-value="${value}"]`);
    return option?.textContent?.trim() ?? "";
  });
}
