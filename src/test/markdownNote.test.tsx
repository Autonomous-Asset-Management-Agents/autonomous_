import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { MarkdownNote } from "../console/shared/MarkdownNote";

/**
 * Design-guide conformance for the report renderer (SYSTEM_DESIGN_GUIDE §2/§3/§4B).
 * The report body is rendered by MarkdownNote, so the guide's data-presentation rules
 * (monospace for figures/tickers, table-head spec, on-palette link color) live here.
 */

const numTexts = (container: HTMLElement) =>
  Array.from(container.querySelectorAll("span.num")).map((n) => n.textContent ?? "");

describe("MarkdownNote — design-guide conformance", () => {
  // §3 Monospace-Pflichten + §4B: financial figures / tickers render in JetBrains Mono (.num);
  // prose cells stay in the body font.
  it("monospaces numeric/ticker table cells but leaves prose cells proportional", () => {
    const md = [
      "| Ticker | Kurs | These |",
      "|---|---|---|",
      "| NVDA | $142.34 | Starke Fundamentaldaten |",
    ].join("\n");
    const { container } = render(<MarkdownNote source={md} />);
    const tds = Array.from(container.querySelectorAll("td"));
    expect(tds).toHaveLength(3);
    expect(tds[0].className).toContain("num"); // NVDA — ticker
    expect(tds[1].className).toContain("num"); // $142.34 — price
    expect(tds[2].className).not.toContain("num"); // prose column stays Inter
  });

  // §4B / §3 Table-Head: 11px, uppercase, letter-spacing 0.04em, muted color 0.16, hairline 0.05.
  it("renders table headers to the Table-Head spec", () => {
    const md = ["| Kennzahl | Wert |", "|---|---|", "| PEG | 1.8 |"].join("\n");
    const { container } = render(<MarkdownNote source={md} />);
    const th = container.querySelector("th");
    expect(th).not.toBeNull();
    const cls = th!.className;
    expect(cls).toContain("text-[11px]");
    expect(cls).toContain("uppercase");
    expect(cls).toContain("tracking-[0.04em]");
    expect(cls).toContain("text-white/[0.16]");
    expect(cls).toContain("border-white/5");
    expect(cls).not.toContain("text-white/70");
    expect(cls).not.toContain("border-white/15");
  });

  // §2 Design Tokens: no blue/sky hue exists in the palette — links use the off-white foreground.
  it("renders links in the on-palette off-white, not ad-hoc sky-blue", () => {
    const { container } = render(<MarkdownNote source={"Siehe [Quelle](https://example.com/x) dazu."} />);
    const a = container.querySelector("a");
    expect(a).not.toBeNull();
    expect(a!.className).toContain("text-white/90");
    expect(a!.className).not.toContain("sky");
  });

  // §3 Monospace-Pflichten ("ohne Ausnahme"): metrics in prose — prices, percentages, ISO dates,
  // latency/size units, and N/N vote indices — render monospace.
  it("monospaces inline metrics in prose", () => {
    const price = render(<MarkdownNote source={"Kursziel $142.34 erwartet."} />);
    expect(numTexts(price.container)).toContain("$142.34");

    const pct = render(<MarkdownNote source={"Rendite +2.34% aktuell."} />);
    expect(numTexts(pct.container).some((t) => t.includes("+2.34%"))).toBe(true);

    const date = render(<MarkdownNote source={"Stand 2026-07-16 heute."} />);
    expect(numTexts(date.container)).toContain("2026-07-16");

    const unit = render(<MarkdownNote source={"Latenz 42ms gemessen."} />);
    expect(numTexts(unit.container).some((t) => t.includes("42ms"))).toBe(true);

    const vote = render(<MarkdownNote source={"Votum 7/9 BUY dafür."} />);
    expect(numTexts(vote.container).some((t) => t.includes("7/9"))).toBe(true);
  });

  // Conservative: ordinals, counts and bare years in prose must NOT be monospaced (no false positives).
  it("does not monospace ordinals, counts or bare years", () => {
    const { container } = render(<MarkdownNote source={"Das sind die top 5 Titel im Q3 2026."} />);
    expect(numTexts(container)).toHaveLength(0);
  });
});
