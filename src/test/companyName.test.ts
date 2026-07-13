import { describe, it, expect } from "vitest";
import { formatCompanyName, getCompanyName } from "@/console/lib/companyName";

// Company-name lookup (#console) — turns a bare ticker into the full company name for the
// console's decision/position/report surfaces. Names come from the bundled SEC EDGAR snapshot
// (raw titles are often ALL-CAPS), so `formatCompanyName` prettifies them at runtime.

describe("formatCompanyName", () => {
  it("title-cases ALL-CAPS SEC titles and normalizes corporate suffixes", () => {
    expect(formatCompanyName("MICROSOFT CORP")).toBe("Microsoft Corp.");
    expect(formatCompanyName("NVIDIA CORP")).toBe("Nvidia Corp.");
    expect(formatCompanyName("COCA COLA CO")).toBe("Coca Cola Co.");
    expect(formatCompanyName("BERKSHIRE HATHAWAY INC")).toBe("Berkshire Hathaway Inc.");
  });

  it("keeps already-prettified names and mixed-case tokens", () => {
    expect(formatCompanyName("Apple Inc.")).toBe("Apple Inc.");
    expect(formatCompanyName("Alphabet Inc.")).toBe("Alphabet Inc.");
    expect(formatCompanyName("PROCTER & GAMBLE Co")).toBe("Procter & Gamble Co.");
  });
});

describe("getCompanyName", () => {
  it("resolves a known ticker to its formatted name (case-insensitive)", () => {
    expect(getCompanyName("AAPL")).toBe("Apple Inc.");
    expect(getCompanyName("msft")).toBe("Microsoft Corp.");
  });

  it("normalizes dotted class symbols (BRK.B -> BRK-B)", () => {
    expect(getCompanyName("BRK.B")).toBe("Berkshire Hathaway Inc.");
  });

  it("returns null for unknown / empty symbols (UI falls back to the ticker)", () => {
    expect(getCompanyName("ZZZZNOPE")).toBeNull();
    expect(getCompanyName("")).toBeNull();
    expect(getCompanyName(null)).toBeNull();
    expect(getCompanyName(undefined)).toBeNull();
  });
});
