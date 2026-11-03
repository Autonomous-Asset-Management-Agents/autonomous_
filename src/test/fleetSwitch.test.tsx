/** STW-2f (#5125) — the switch for the strategy fleet on the "Paper accounts" card. */
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { FleetSwitch } from "@/console/desktop/strategyWorkshop/FleetSwitch";

function mount(over: Partial<Parameters<typeof FleetSwitch>[0]> = {}) {
  const onChange = vi.fn();
  const view = render(
    <div>
      <span id="title">Paper accounts</span>
      <p id="sentence">Sentence</p>
      <FleetSwitch checked={false} onChange={onChange} labelledBy="title" describedBy="sentence" {...over} />
    </div>,
  );
  return { onChange, ...view };
}

describe("FleetSwitch (#5125)", () => {
  it.each([true, false])("role switch, aria-checked follows the prop (%s)", (checked) => {
    mount({ checked });
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", String(checked));
  });

  it("is named by the title and described by the sentence", () => {
    mount();
    const sw = screen.getByRole("switch", { name: "Paper accounts" });
    expect(sw).toHaveAttribute("aria-describedby", "sentence");
    expect(sw).toHaveAttribute("type", "button");
  });

  it.each([true, false])("a click calls onChange with the opposite (%s)", (checked) => {
    const { onChange } = mount({ checked });
    fireEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(!checked);
  });

  it("is no checkbox", () => {
    const { container } = mount();
    expect(container.querySelector('input[type="checkbox"]')).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("disabled: a click does nothing", () => {
    const { onChange } = mount({ disabled: true });
    const sw = screen.getByRole("switch");
    expect(sw).toBeDisabled();
    fireEvent.click(sw);
    expect(onChange).not.toHaveBeenCalled();
  });
});
