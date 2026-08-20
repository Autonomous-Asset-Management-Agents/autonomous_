import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ModeCLiveBanner } from "@/console/desktop/ModeCLiveBanner";

describe("ModeCLiveBanner (H2 #2370)", () => {
  it("renders the Mode-C live disclosure when active", () => {
    const { getByRole } = render(<ModeCLiveBanner active={true} />);
    const el = getByRole("alert");
    expect(el.textContent).toMatch(/Mode C/);
    expect(el.textContent).toMatch(/real capital/i);
    expect(el.textContent).toMatch(/across restarts/i);
    expect(el.textContent).toMatch(/Kill Switch/i);
  });

  it("renders nothing when not active (paper or Mode-C off)", () => {
    const { container } = render(<ModeCLiveBanner active={false} />);
    expect(container.firstChild).toBeNull();
  });
});
