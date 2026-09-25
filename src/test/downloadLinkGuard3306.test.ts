// #3306: the release-time liveness guard for the public download links.
//
// Context: #3304 shipped a website whose download buttons pointed at `desktop-v0.4.11-beta`, a tag
// production never publishes — every visitor got a 404, and nobody noticed until a human clicked.
// A HEAD request at deploy time would have caught it in seconds.
//
// The guard must not become the SAME defect: if the script derived the tag with its own copy of the
// rule, the copy could drift from `appVersion.ts` and the check would happily verify the wrong URL.
// So this test pins the script's URLs to the helper's, version by version. Drift breaks the suite.
import { describe, it, expect } from "vitest";
import { urlsForRelease } from "../../scripts/check-download-links.mjs";
import { windowsDownloadUrl, macDownloadUrl } from "@/lib/appVersion";

describe("download-link guard (#3306) — one rule, checked in two places", () => {
  it("builds exactly the URLs the site ships, for every version shape", () => {
    for (const v of ["0.4.11", "0.4.12", "1.0.0", "0.10.3"]) {
      const urls = urlsForRelease({ version: v, mac_version: v });
      expect(urls.map((u) => u.url)).toEqual([windowsDownloadUrl(v), macDownloadUrl(v)]);
    }
  });

  it("handles a mac version that lags the Windows one", () => {
    const urls = urlsForRelease({ version: "0.4.12", mac_version: "0.4.11" });
    expect(urls[0].url).toBe(windowsDownloadUrl("0.4.12"));
    expect(urls[1].url).toBe(macDownloadUrl("0.4.11"));
  });

  it("labels each URL so a CI failure names the broken button", () => {
    const urls = urlsForRelease({ version: "0.4.12", mac_version: "0.4.12" });
    expect(urls.map((u) => u.label)).toEqual(["Windows installer", "macOS .dmg"]);
  });

  it("a missing mac_version yields no mac URL rather than a 0.0.0 dead link", () => {
    const urls = urlsForRelease({ version: "0.4.12" });
    expect(urls).toHaveLength(1);
    expect(urls[0].label).toBe("Windows installer");
  });

  it("refuses to invent a URL from an absent version", () => {
    expect(() => urlsForRelease({})).toThrow(/version/i);
  });
});
