import { describe, it, expect } from "vitest";
import { resolveDeepLinkSymbol } from "@/console/lib/reportDeepLink";

// #2732 Strand B — clicking a company/ticker must open THAT company's report, even when the caller
// passes a base ticker (Overview strips the exchange suffix).

describe("resolveDeepLinkSymbol", () => {
  const reports = ["AAPL", "BRK.B", "HOOD"];

  it("exact match wins", () => {
    expect(resolveDeepLinkSymbol(reports, "HOOD")).toBe("HOOD");
    expect(resolveDeepLinkSymbol(reports, "BRK.B")).toBe("BRK.B");
  });

  it("a base ticker resolves to the suffixed report symbol (BRK -> BRK.B)", () => {
    expect(resolveDeepLinkSymbol(reports, "BRK")).toBe("BRK.B");
  });

  it("falls back to the raw target when no report matches (so nothing crashes)", () => {
    expect(resolveDeepLinkSymbol(reports, "TSLA")).toBe("TSLA");
    expect(resolveDeepLinkSymbol([], "AAPL")).toBe("AAPL");
  });
});
