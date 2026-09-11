import { describe, it, expect } from "vitest";
import { resolveExecutionMode } from "@/console/desktop/executionMode";

describe("resolveExecutionMode (#1653)", () => {
  it("defaults to autonomous when no preference is stored", () => {
    // Paper runs autonomously (#1442); the UI default should reflect that.
    expect(resolveExecutionMode(null)).toBe("auto");
  });

  it("respects an explicit HITL choice", () => {
    expect(resolveExecutionMode("hitl")).toBe("hitl");
  });

  it("keeps autonomous when explicitly stored", () => {
    expect(resolveExecutionMode("auto")).toBe("auto");
  });

  it("falls back to autonomous on any unexpected value", () => {
    expect(resolveExecutionMode("garbage")).toBe("auto");
  });
});
