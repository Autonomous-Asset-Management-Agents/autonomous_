// Appliance referral waitlist — pure ranking/copy core (spec §3, plan Task 1).
import { describe, it, expect } from "vitest";
import { rankPosition, positionCopy, buildInviteLink, padPosition } from "@/lib/applianceWaitlist";

const e = (code: string, referrals: number, createdAt: number) => ({ code, referrals, createdAt });

describe("rankPosition", () => {
  it("referrals beat seniority; ties resolved by createdAt", () => {
    const entries = [e("old", 0, 1), e("mid", 2, 5), e("new", 2, 9), e("late", 0, 20)];
    expect(rankPosition(entries, "mid")).toBe(1);
    expect(rankPosition(entries, "new")).toBe(2);
    expect(rankPosition(entries, "old")).toBe(3);
    expect(rankPosition(entries, "late")).toBe(4);
  });
  it("unknown code -> -1", () => {
    expect(rankPosition([e("a", 0, 1)], "zz")).toBe(-1);
  });
});

describe("positionCopy", () => {
  it("inside the edition", () => {
    expect(positionCopy(42, 100)).toBe("you are #042 of 100 — First Edition secured");
  });
  it("outside the edition flips to the referral driver", () => {
    expect(positionCopy(134, 100)).toBe("#134 — 34 spots from the First Edition");
  });
  it("boundary: #100 is inside", () => {
    expect(positionCopy(100, 100)).toBe("you are #100 of 100 — First Edition secured");
  });
});

describe("helpers", () => {
  it("padPosition zero-pads under 1000", () => {
    expect(padPosition(7)).toBe("007");
    expect(padPosition(1234)).toBe("1234");
  });
  it("buildInviteLink", () => {
    expect(buildInviteLink("https://aaagents.de", "X7K2")).toBe("https://aaagents.de/appliance?ref=X7K2");
  });
});
