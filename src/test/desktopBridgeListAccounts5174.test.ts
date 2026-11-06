import { describe, it, expect, afterEach } from "vitest";
import { listAccounts } from "@/lib/desktopBridge";

// #5174: the shell lists paper1 (setup account) and live with masked numbers, with or without a fleet
// (desktop/electron/accounts.cjs, IPC accounts:list). The renderer never sees more than the last 4.

const setBridge = (impl: Record<string, unknown> | undefined) => {
  (window as unknown as { aaagents?: unknown }).aaagents = impl;
};

describe("listAccounts (#5174)", () => {
  afterEach(() => setBridge(undefined));

  it("returns null outside the desktop (no bridge)", async () => {
    setBridge(undefined);
    await expect(listAccounts()).resolves.toBeNull();
  });

  it("returns null in an older shell without the member", async () => {
    setBridge({ isDesktop: true });
    await expect(listAccounts()).resolves.toBeNull();
  });

  it("returns paper1 and live in fixed order with the shell's masked numbers", async () => {
    setBridge({
      isDesktop: true,
      listAccounts: async () => [
        { slot: "live", connected: true, accountMasked: null },
        { slot: "paper1", connected: true, accountMasked: "••••8888" },
      ],
    });
    await expect(listAccounts()).resolves.toEqual([
      { slot: "paper1", connected: true, accountMasked: "••••8888" },
      { slot: "live", connected: true, accountMasked: null },
    ]);
  });

  it("drops anything that is not a masked number (never a full number in the console)", async () => {
    setBridge({
      isDesktop: true,
      listAccounts: async () => [
        { slot: "paper1", connected: true, accountMasked: "PA9SETUP8888" },
        { slot: "live", connected: "yes", accountMasked: 4321 },
      ],
    });
    await expect(listAccounts()).resolves.toEqual([
      { slot: "paper1", connected: true, accountMasked: null },
      { slot: "live", connected: false, accountMasked: null },
    ]);
  });

  it("rejects on an unusable answer, so the caller can tell 'no shell' from 'shell failed'", async () => {
    setBridge({ isDesktop: true, listAccounts: async () => null });
    await expect(listAccounts()).rejects.toThrow();
  });
});
