/**
 * UXC-1 S8 (#3177): SegmentPicker / OnOffSegment — the neutral segmented
 * control that replaces the iOS Switch in the console (owner decision:
 * both states visible, selected segment on white/12, no track/thumb).
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { SegmentPicker, OnOffSegment } from "../console/shared/SegmentPicker";

const OPTS = [
  { id: "1m", label: "1M" },
  { id: "3m", label: "3M" },
  { id: "1y", label: "1Y" },
];

describe("SegmentPicker", () => {
  it("S1: renders one aria-pressed button per option; only the value is pressed", () => {
    render(<SegmentPicker ariaLabel="Range" options={OPTS} value="3m" onChange={() => {}} />);
    const group = screen.getByRole("group", { name: "Range" });
    const buttons = within(group).getAllByRole("button");
    expect(buttons).toHaveLength(3);
    expect(buttons.map((b) => b.getAttribute("aria-pressed"))).toEqual([
      "false",
      "true",
      "false",
    ]);
  });

  it("S2: clicking an unselected segment reports its id", () => {
    const onChange = vi.fn();
    render(<SegmentPicker ariaLabel="Range" options={OPTS} value="3m" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "1Y" }));
    expect(onChange).toHaveBeenCalledWith("1y");
  });

  it("S3: clicking the already-selected segment is a no-op", () => {
    const onChange = vi.fn();
    render(<SegmentPicker ariaLabel="Range" options={OPTS} value="3m" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "3M" }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("S4: disabled blocks every segment", () => {
    const onChange = vi.fn();
    render(
      <SegmentPicker ariaLabel="Range" options={OPTS} value="3m" onChange={onChange} disabled />,
    );
    fireEvent.click(screen.getByRole("button", { name: "1Y" }));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("OnOffSegment", () => {
  it("S5: renders On/Off with the boolean mapped to aria-pressed", () => {
    render(<OnOffSegment ariaLabel="MomentumAgent enabled" value={true} onChange={() => {}} />);
    const group = screen.getByRole("group", { name: "MomentumAgent enabled" });
    expect(within(group).getByRole("button", { name: "On" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(within(group).getByRole("button", { name: "Off" }).getAttribute("aria-pressed")).toBe(
      "false",
    );
  });

  it("S6: clicking the other state reports the new boolean", () => {
    const onChange = vi.fn();
    render(<OnOffSegment ariaLabel="X enabled" value={true} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Off" }));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("S7: clicking the current state is a no-op", () => {
    const onChange = vi.fn();
    render(<OnOffSegment ariaLabel="X enabled" value={false} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Off" }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
