// Focus unit for getAppVersion() — the desktop-bridge helper behind the Settings
// "Version" row (#1939). Tested here (NOT via a full Settings render) because
// Settings pulls in engine/store/kill-switch/child cards, so a render is heavy;
// the version logic lives entirely in this helper. jsdom is the global vitest env.
import { describe, it, expect, afterEach } from "vitest";
import { getAppVersion } from "@/lib/desktopBridge";

type WinWithBridge = { aaagents?: unknown };

describe("getAppVersion (desktop bridge)", () => {
  afterEach(() => {
    delete (window as unknown as WinWithBridge).aaagents;
  });

  it("returns the version reported by the desktop bridge", async () => {
    (window as unknown as WinWithBridge).aaagents = { getVersion: async () => "9.9.9" };
    expect(await getAppVersion()).toBe("9.9.9");
  });

  it("returns null in the browser (no desktop bridge)", async () => {
    delete (window as unknown as WinWithBridge).aaagents;
    expect(await getAppVersion()).toBeNull();
  });

  it("returns null when the bridge does not expose getVersion", async () => {
    (window as unknown as WinWithBridge).aaagents = { isDesktop: true };
    expect(await getAppVersion()).toBeNull();
  });
});
