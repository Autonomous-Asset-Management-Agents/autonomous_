// #4870 (#4581 S2, Plan §3c): the download buttons go through /download/<os> when the page came
// through the `edge` function — recognisable by <meta name="aaa-experiments">, which edge always
// writes. Without it (console host, OSS install, dev server) the direct GitHub URL stays, because
// /download/<os> exists only behind edge. Guard #3306 keeps checking the direct URLs.
import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { downloadHref } from "@/lib/downloadLink";
import { MAC_DOWNLOAD_URL, WINDOWS_DOWNLOAD_URL } from "@/lib/appVersion";
import { PricingCard } from "@/pages/PricingMaster";
import { SiteMarquee } from "@/components/SiteMarquee";
import { getPricingData } from "@/lib/pricing-config";
import i18n from "@/lib/i18n";

const setMeta = (content: string) => {
  const m = document.createElement("meta");
  m.name = "aaa-experiments";
  m.content = content;
  document.head.appendChild(m);
};

afterEach(() => {
  document.head.querySelectorAll('meta[name="aaa-experiments"]').forEach((m) => m.remove());
});

describe("downloadHref (#4870)", () => {
  it("without the edge meta: the direct GitHub URL", () => {
    expect(downloadHref("windows")).toBe(WINDOWS_DOWNLOAD_URL);
    expect(downloadHref("mac")).toBe(MAC_DOWNLOAD_URL);
  });

  it("served by edge without a test: /download/<os>", () => {
    setMeta("");
    expect(downloadHref("windows")).toBe("/download/windows");
  });

  it("served by edge with a variant: /download/<os>?v=<variant>", () => {
    setMeta("hero_positioning=transparency");
    expect(downloadHref("mac")).toBe("/download/mac?v=transparency");
  });
});

describe("download buttons use downloadHref (#4870)", () => {
  it("edition card: both pills go through /download with the variant", () => {
    setMeta("hero_positioning=positioning");
    render(<PricingCard tier={getPricingData((k) => i18n.t(k)).private} mode="Junior" setMode={() => {}} />);
    expect(screen.getByRole("link", { name: /windows/i }).getAttribute("href")).toBe("/download/windows?v=positioning");
    expect(screen.getByRole("link", { name: /macos/i }).getAttribute("href")).toBe("/download/mac?v=positioning");
  });

  it("marquee: goes through /download for the visitor's OS", () => {
    setMeta("");
    render(<SiteMarquee />);
    expect(screen.getByRole("link").getAttribute("href")).toMatch(/^\/download\/(windows|mac)$/);
  });
});
