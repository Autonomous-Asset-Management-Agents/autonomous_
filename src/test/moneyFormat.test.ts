import { describe, it, expect } from "vitest";
import { fmtMoney, currencySymbol } from "../console/lib/format";

/** PR-B: money display follows the real account currency (Alpaca account.currency),
 *  not a hardcoded €. */
describe("currencySymbol", () => {
  it("maps common ISO codes to symbols", () => {
    expect(currencySymbol("USD")).toBe("$");
    expect(currencySymbol("EUR")).toBe("€");
    expect(currencySymbol("GBP")).toBe("£");
    expect(currencySymbol("JPY")).toBe("¥");
  });
  it("is case-insensitive", () => {
    expect(currencySymbol("usd")).toBe("$");
  });
  it("falls back to the ISO code for unknown currencies", () => {
    expect(currencySymbol("CHF")).toBe("CHF");
  });
  it("defaults to $ for empty/undefined (fail-soft)", () => {
    expect(currencySymbol("")).toBe("$");
    expect(currencySymbol(undefined)).toBe("$");
  });
});

describe("fmtMoney", () => {
  it("formats with the given symbol + en-US grouping", () => {
    expect(fmtMoney(1234.5, "$")).toBe("$1,234.50");
    expect(fmtMoney(1234.5, "€")).toBe("€1,234.50");
  });
  it("compact k/M", () => {
    expect(fmtMoney(12500, "$", { compact: true })).toBe("$12.50k");
    expect(fmtMoney(1_500_000, "$", { compact: true })).toBe("$1.50M");
  });
  it("negative uses a minus sign before the symbol", () => {
    expect(fmtMoney(-50, "$")).toBe("−$50.00");
  });
  it("sign:true prefixes +/− explicitly", () => {
    expect(fmtMoney(50, "$", { sign: true })).toBe("+$50.00");
    expect(fmtMoney(-50, "$", { sign: true })).toBe("−$50.00");
  });
});
