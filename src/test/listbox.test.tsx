/**
 * Console Listbox — the console's own dropdown (owner decision 01.11.2026, variant C).
 *
 * Why it exists: a native `<select>` styles only its closed button. Its option list
 * is drawn by the operating system — on Windows a light panel with a blue highlight —
 * so every dropdown in the console broke out of the dark surface the moment it opened.
 * No CSS of the app reaches an `<option>`; the only fix is to render the list ourselves.
 *
 * The contract below is what the five call sites rely on, and what `designGuard`
 * rule G9 protects: no `<select>` element remains in `src/console/`.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { Listbox, type ListboxOption } from "../console/shared/Listbox";

const OPTS: ListboxOption[] = [
  { value: "lstm", label: "Model forecast (LSTM)" },
  { value: "llm", label: "Your prompt" },
  { value: "liste", label: "Own list" },
];

/** Opens the control and returns its list. The list carries no name of its own:
 *  the combobox does, so `getByLabelText` stays unambiguous while it is open. */
function auf(name = "Candidates from") {
  fireEvent.click(screen.getByLabelText(name));
  return screen.getByRole("listbox");
}

describe("Listbox — the closed control", () => {
  it("L1: the trigger carries the label, the chosen option's text and its raw value", () => {
    render(<Listbox ariaLabel="Candidates from" options={OPTS} value="llm" onChange={() => {}} />);
    const trigger = screen.getByLabelText("Candidates from");
    expect(trigger.getAttribute("role")).toBe("combobox");
    expect(trigger.textContent).toContain("Your prompt");
    expect(trigger.dataset.value).toBe("llm");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("L2: an unknown value shows the placeholder, not a blank control", () => {
    render(
      <Listbox ariaLabel="Candidates from" options={OPTS} value="" placeholder="—" onChange={() => {}} />,
    );
    expect(screen.getByLabelText("Candidates from").textContent).toContain("—");
  });

  it("L3: no native select element is rendered", () => {
    const { container } = render(
      <Listbox ariaLabel="Candidates from" options={OPTS} value="lstm" onChange={() => {}} />,
    );
    expect(container.querySelector("select")).toBeNull();
    expect(container.querySelector("option")).toBeNull();
  });

  it("L4: a disabled control neither opens nor reports", () => {
    const onChange = vi.fn();
    render(
      <Listbox ariaLabel="Candidates from" options={OPTS} value="lstm" disabled onChange={onChange} />,
    );
    fireEvent.click(screen.getByLabelText("Candidates from"));
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("Listbox — the open list", () => {
  it("L5: every option is an option row; only the value is selected", () => {
    render(<Listbox ariaLabel="Candidates from" options={OPTS} value="llm" onChange={() => {}} />);
    const list = auf();
    const rows = within(list).getAllByRole("option");
    expect(rows.map((r) => r.dataset.value)).toEqual(["lstm", "llm", "liste"]);
    expect(rows.map((r) => r.getAttribute("aria-selected"))).toEqual(["false", "true", "false"]);
    expect(screen.getByLabelText("Candidates from").getAttribute("aria-expanded")).toBe("true");
  });

  it("L6: choosing reports the raw value and closes the list", () => {
    const onChange = vi.fn();
    render(<Listbox ariaLabel="Candidates from" options={OPTS} value="llm" onChange={onChange} />);
    const list = auf();
    fireEvent.click(within(list).getByText("Own list"));
    expect(onChange).toHaveBeenCalledWith("liste");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("L7: choosing the value already set is a no-op, like the segmented control", () => {
    const onChange = vi.fn();
    render(<Listbox ariaLabel="Candidates from" options={OPTS} value="llm" onChange={onChange} />);
    const list = auf();
    fireEvent.click(within(list).getByText("Your prompt"));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("L8: a locked option is marked and cannot be chosen (#4987 lockedChoices)", () => {
    const onChange = vi.fn();
    render(
      <Listbox
        ariaLabel="Sizing method"
        options={[
          { value: "off", label: "Conviction", disabled: true },
          { value: "b", label: "Equal weight with risk tilts" },
        ]}
        value="b"
        onChange={onChange}
      />,
    );
    const list = auf("Sizing method");
    const locked = within(list).getByText("Conviction").closest("[role=option]") as HTMLElement;
    expect(locked.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(locked);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("listbox")).toBeTruthy();
  });

  it("L9: groups render their heading and keep their options reachable (optgroup)", () => {
    render(
      <Listbox
        ariaLabel="Source"
        options={[
          { value: "", label: "No source" },
          { value: "A", label: "A", group: "Saved strategies" },
          { value: "B", label: "B", group: "Saved strategies" },
        ]}
        value=""
        onChange={() => {}}
      />,
    );
    const list = auf("Source");
    expect(within(list).getByRole("group", { name: "Saved strategies" })).toBeTruthy();
    expect(within(list).getAllByRole("option").map((r) => r.dataset.value)).toEqual(["", "A", "B"]);
  });
});

describe("Listbox — keyboard and dismissal", () => {
  it("L10: ArrowDown opens the list and Enter takes the next option", () => {
    const onChange = vi.fn();
    render(<Listbox ariaLabel="Candidates from" options={OPTS} value="lstm" onChange={onChange} />);
    const trigger = screen.getByLabelText("Candidates from");
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    expect(screen.getByRole("listbox")).toBeTruthy();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    fireEvent.keyDown(trigger, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("llm");
  });

  it("L11: Escape closes without reporting and leaves focus on the trigger", () => {
    const onChange = vi.fn();
    render(<Listbox ariaLabel="Candidates from" options={OPTS} value="lstm" onChange={onChange} />);
    const trigger = screen.getByLabelText("Candidates from");
    fireEvent.click(trigger);
    fireEvent.keyDown(trigger, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(trigger);
  });

  it("L12: a click outside closes the list", () => {
    render(
      <div>
        <Listbox ariaLabel="Candidates from" options={OPTS} value="lstm" onChange={() => {}} />
        <button type="button">elsewhere</button>
      </div>,
    );
    auf();
    fireEvent.mouseDown(screen.getByText("elsewhere"));
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("L13: the active option is announced through aria-activedescendant", () => {
    render(<Listbox ariaLabel="Candidates from" options={OPTS} value="llm" onChange={() => {}} />);
    const trigger = screen.getByLabelText("Candidates from");
    const list = auf();
    const active = trigger.getAttribute("aria-activedescendant");
    expect(active).toBeTruthy();
    expect(within(list).getByRole("option", { selected: true }).id).toBe(active);
  });
});
