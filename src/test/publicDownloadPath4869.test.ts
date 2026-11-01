// #4869 (#4581 S1, Plan §3c): the public download path through the `edge` function. S1 adds only
// the helper; the buttons switch to it in S2 (#4870), together with the rewrite. The direct GitHub
// URLs stay as they are — they are the redirect target and the base of guard #3306.
import { describe, expect, it } from "vitest";

import { WINDOWS_DOWNLOAD_URL, publicDownloadPath } from "@/lib/appVersion";

describe("publicDownloadPath (#4869)", () => {
  it("builds /download/<os> without a variant", () => {
    expect(publicDownloadPath("windows")).toBe("/download/windows");
    expect(publicDownloadPath("mac")).toBe("/download/mac");
  });

  it("carries the variant as ?v=", () => {
    expect(publicDownloadPath("windows", "transparency")).toBe("/download/windows?v=transparency");
  });

  it("drops a control or empty variant", () => {
    expect(publicDownloadPath("mac", "control")).toBe("/download/mac");
    expect(publicDownloadPath("mac", "")).toBe("/download/mac");
  });

  it("encodes the variant", () => {
    expect(publicDownloadPath("mac", "a b&c")).toBe("/download/mac?v=a%20b%26c");
  });

  it("leaves the direct GitHub URL unchanged", () => {
    expect(WINDOWS_DOWNLOAD_URL).toMatch(/^https:\/\/github\.com\/.+\/autonomous_setup\.exe$/);
  });
});
