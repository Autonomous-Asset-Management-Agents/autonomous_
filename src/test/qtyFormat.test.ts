import { describe, it, expect } from "vitest";
import { fmtQty } from "../console/lib/format";

/**
 * #2587 (GUI quickie 1): the positions/orders Qty column rendered fractional
 * share counts with a de-DE decimal COMMA inside an en-US UI — "15,951" /
 * "23,029379" read as thousands separators (15 951 shares), not as 15.951
 * shares. Qty must format en-US: whole shares clean (with thousands grouping),
 * fractional fills with 2–4 decimals and a DOT as the decimal separator.
 * The #2131 guarantee stays: a real fractional fill never collapses to "0".
 */
describe("fmtQty (#2587 en-US quantities)", () => {
  it("whole shares render clean, no decimals", () => {
    expect(fmtQty(10)).toBe("10");
    expect(fmtQty(0)).toBe("0");
  });

  it("large whole quantities use en-US thousands grouping", () => {
    expect(fmtQty(1234)).toBe("1,234");
  });

  it("fractional quantities use a decimal POINT, never a comma (de-DE regression)", () => {
    // These exact values rendered "15,951" / "23,029379" before the fix.
    expect(fmtQty(15.951)).toBe("15.951");
    expect(fmtQty(23.029379)).toBe("23.0294");
  });

  it("fractional quantities carry 2-4 decimals", () => {
    expect(fmtQty(0.2057)).toBe("0.2057");
    expect(fmtQty(1.5)).toBe("1.50");
  });

  it("a tiny real fractional fill never collapses to a dead-looking 0 (#2131)", () => {
    expect(fmtQty(0.0007)).not.toBe("0.00");
    expect(Number(fmtQty(0.0007))).toBeGreaterThan(0);
  });
});
