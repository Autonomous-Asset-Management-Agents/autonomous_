/**
 * STW-4b (#4864) — neutrality guard for the "Strategies" card, after the pattern of
 * liveStrategyAssignmentNeutrality.test.tsx with the word list of the UX concept
 * (docs/4628-ux-konzept-strategie-werkstatt/ux_konzept.md, "Neutrale Wortliste") and the
 * public wording guard (wordingGuard.ts). Checked is what the user reads: the rendered card
 * AND the open delete confirmation.
 */
import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { FORBIDDEN } from "./wordingGuard";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listStrategies: vi.fn().mockResolvedValue({
      strategien: [
        { name: "A", werte: { STOP_LOSS_PCT: "6.0" } },
        { name: "B", werte: { STOP_LOSS_PCT: "5.0" } },
        { name: "C", werte: {} },
      ],
      max_strategien: 3,
    }),
    deleteStrategy: vi.fn(),
  };
});

import { StrategyList } from "@/console/desktop/strategyWorkshop/StrategyList";

const BANNED = [
  /\bbest\b/i,
  /recommend/i,
  /empfohlen/i,
  /winner/i,
  /\btop\b/i,
  /top-ranked/i,
  /\brank/i,
  /outperform/i,
  /\bsafe(r|st)?\b/i,
  /risky/i,
  /guarantee/i,
  /aggressive/i,
  /conservative/i,
  /profile/i,
  /sharpe/i,
];

// Text nodes joined by a space: `textContent` glues neighbours ("A" + "Safe" → "ASafe"),
// and a word-boundary pattern would then miss the banned word.
function visibleText(root: Node): string {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const parts: string[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) parts.push(n.nodeValue ?? "");
  return parts.join(" ");
}

describe("StrategyList neutrality (STW-4b #4864)", () => {
  it("N1: no ranking or evaluative wording in the card or the open delete confirmation", async () => {
    render(<StrategyList onCreate={vi.fn()} onDuplicate={vi.fn()} onEdit={vi.fn()} />);
    await screen.findByRole("table");
    const firstRow = within(screen.getByRole("table")).getAllByRole("row")[1];
    fireEvent.click(within(firstRow).getByRole("button", { name: "Delete" }));
    await screen.findByRole("alertdialog");

    // The open dialog is portalled into <body>, so the body covers card and dialog.
    const text = visibleText(document.body);
    for (const re of BANNED) expect(text, String(re)).not.toMatch(re);
    expect(text.match(FORBIDDEN)).toBeNull();
    // No numbered order or position marker in front of a strategy name.
    expect(text).not.toMatch(/#\s*1|\b1st\b|No\.\s*1/i);
  });

  it("N2: no row is highlighted — every row carries the same classes and no marker", async () => {
    render(<StrategyList assignments={{ B: "Live account" }} onEdit={vi.fn()} />);
    await screen.findByRole("table");
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    const classes = new Set(rows.map((r) => r.className));
    expect(classes.size).toBe(1);
    for (const r of rows) {
      expect(r.getAttribute("aria-current")).toBeNull();
      expect(r.getAttribute("aria-selected")).toBeNull();
      expect(r.getAttribute("data-highlight")).toBeNull();
      expect(r.className).not.toMatch(/bull|bear|green|red|emerald|amber|gold/i);
    }
  });
});
