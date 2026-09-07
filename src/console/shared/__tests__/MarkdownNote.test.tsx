import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MarkdownNote } from "../MarkdownNote";

/**
 * The MarkdownNote renders the RPT-GOLD auditable note. These lock the subset the
 * deterministic renderer emits AND the safety guarantees (comments stripped,
 * provenance kept as muted tags, no raw HTML injection).
 */
describe("MarkdownNote", () => {
  it("renders headings, tables and inline bold", () => {
    const md = [
      "## 2. Quant-View",
      "",
      "**Der View:** NVDA-Score **4.10**.",
      "",
      "| Kennzahl | Wert | YoY |",
      "|---|---|---|",
      "| Umsatz | 215.94 Mrd USD | +65.5% |",
    ].join("\n");
    const { container } = render(<MarkdownNote source={md} />);

    const h2 = container.querySelector("h2");
    expect(h2?.textContent).toContain("2. Quant-View");
    expect(container.querySelector("strong")?.textContent).toBe("Der View:");
    const table = container.querySelector("table");
    expect(table).not.toBeNull();
    expect(table?.querySelectorAll("thead th").length).toBe(3);
    expect(table?.textContent).toContain("215.94 Mrd USD");
    expect(table?.textContent).toContain("+65.5%");
  });

  it("keeps [src: …] provenance as a muted audit tag (nested brackets), never dropped", () => {
    const md = "Schlusskurs 198.45 USD [src: bars[NVDA] close, PIT <= 2026-05-01]";
    const { container } = render(<MarkdownNote source={md} />);
    // the sentence text survives ...
    expect(container.textContent).toContain("Schlusskurs 198.45 USD");
    // ... and a compact "src" tag carries the FULL annotation (incl. bars[NVDA]) in its title
    const tag = Array.from(container.querySelectorAll("span")).find(
      (s) => s.getAttribute("title")?.includes("bars[NVDA]"),
    );
    expect(tag).toBeTruthy();
    expect(tag?.textContent).toBe("src");
  });

  it("renders links with a safe target and strips HTML comments", () => {
    const md = "See [the filing](https://example.com/x) now.\n\n<!-- SLOT:risks (RPT-5) -->";
    const { container } = render(<MarkdownNote source={md} />);
    const a = container.querySelector("a");
    expect(a?.getAttribute("href")).toBe("https://example.com/x");
    expect(a?.getAttribute("rel")).toContain("noopener");
    // the comment must never reach the DOM
    expect(container.textContent).not.toContain("SLOT");
    expect(container.innerHTML).not.toContain("<!--");
  });

  it("renders bullet lists and horizontal rules", () => {
    const md = ["- first point", "- second point", "", "---"].join("\n");
    const { container } = render(<MarkdownNote source={md} />);
    expect(container.querySelectorAll("ul li").length).toBe(2);
    expect(container.querySelector("hr")).not.toBeNull();
  });

  it("renders nothing for empty or whitespace input", () => {
    const { container } = render(<MarkdownNote source={"   \n  \t "} />);
    expect(container.firstChild).toBeNull();
  });
});
