import { describe, it, expect } from "vitest";
import { ago } from "../console/live/lastSync";

describe("ago (Last-sync relative time)", () => {
  const now = 1_700_000_000_000;

  it("returns an honest '—' when there has been no sync yet", () => {
    expect(ago(null, now)).toBe("—");
  });

  it("formats fresh, second, minute and hour deltas", () => {
    expect(ago(now - 1_000, now)).toBe("just now"); // < 3s
    expect(ago(now - 5_000, now)).toBe("5s ago");
    expect(ago(now - 65_000, now)).toBe("1m ago");
    expect(ago(now - 2 * 3_600_000, now)).toBe("2h ago");
  });

  it("never shows a negative delta from clock skew", () => {
    expect(ago(now + 5_000, now)).toBe("just now");
  });
});
