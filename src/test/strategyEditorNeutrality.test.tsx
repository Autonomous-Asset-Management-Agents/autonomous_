/**
 * STW-4c (#4873) — neutrality guard for the strategy editor.
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { FORBIDDEN } from "./wordingGuard";
import { ADVANCED_GROUPS } from "@/console/desktop/advancedTradingFields";
import { payload } from "./advancedTradingFixtures";

vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getTradingSettings: vi.fn().mockResolvedValue(payload()),
    fetchLlmTrichter: vi.fn().mockResolvedValue({ anbieter: "ollama", websuche: false }),
    saveStrategy: vi.fn().mockResolvedValue({
      success: true, aktion: "create", name: "C", werte: {}, detail: "strategy recorded on the WORM chain",
    }),
    listStrategyVersions: vi.fn().mockResolvedValue({
      name: "C",
      versionen: [{ hash: "h", hash_kuerzel: "abcd1234", zeitpunkt: "2026-10-28T10:00:00Z", aktion: "create", felder: [] }],
    }),
  };
});

import { StrategyEditor } from "@/console/desktop/strategyWorkshop/StrategyEditor";

const BANNED = [
  /\bbest\b/i, /recommend/i, /empfohlen/i, /winner/i, /\btop\b/i, /top-ranked/i, /\brank/i,
  /outperform/i, /\bsafe(r|st)?\b/i, /risky/i, /guarantee/i, /aggressive/i, /conservative/i, /profile/i, /sharpe/i,
];

const STRATEGIES = [
  { name: "A", werte: { STOP_LOSS_PCT: "6.0" } },
  { name: "B", werte: { STOP_LOSS_PCT: "5.0" } },
];

function editorOwnText(root: HTMLElement): string {
  const clone = root.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("[data-key]").forEach((row) => row.remove());
  const parts: string[] = [];
  const walker = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) parts.push(n.nodeValue ?? "");
  clone.querySelectorAll("optgroup").forEach((g) => parts.push(g.getAttribute("label") ?? ""));
  return parts.join(" ");
}

function assertNeutral(text: string) {
  for (const re of BANNED) expect(text, String(re)).not.toMatch(re);
  expect(text.match(FORBIDDEN)).toBeNull();
  expect(text).not.toMatch(/#\s*1|\b1st\b|No\.\s*1/i);
}

describe("StrategyEditor neutrality (STW-4c #4873)", () => {
  it("N1: no ranking or evaluative wording in the editor, before and after a save; T03–T07 and T09 verbatim", async () => {
    render(<StrategyEditor mode="new" strategies={STRATEGIES} onSaved={vi.fn()} onCancel={vi.fn()} />);
    await screen.findByLabelText("Buy threshold");
    const editor = screen.getByTestId("strategy-editor");
    const before = editorOwnText(editor);
    assertNeutral(before);
    for (const t of ["From current settings", "Duplicate", "Strategy name", "Save strategy", "Fixed for all strategies"]) {
      expect(before).toContain(t);
    }
    fireEvent.change(screen.getByLabelText("Strategy name"), { target: { value: "C" } });
    fireEvent.click(screen.getByRole("button", { name: "Save strategy" }));
    await screen.findByTestId("strategy-versions");
    const after = editorOwnText(editor);
    assertNeutral(after);
    expect(after).toContain("Strategy saved. The change is recorded in the audit chain.");
  });

  it("N1b #4940: both prompt hints are neutral", async () => {
    render(<StrategyEditor mode="new" strategies={STRATEGIES} onSaved={vi.fn()} onCancel={vi.fn()} />);
    await screen.findByLabelText("Buy threshold");
    fireEvent.change(screen.getByLabelText("Candidates from"), { target: { value: "llm" } });
    const editor = screen.getByTestId("strategy-editor");
    expect((await screen.findByRole("status")).textContent).toMatch(/^Local model/);
    assertNeutral(editorOwnText(editor));
    fireEvent.change(screen.getByLabelText("Provider for the prompt"), { target: { value: "openai" } });
    expect(screen.getByRole("status").textContent).toMatch(/^Web search requests/);
    assertNeutral(editorOwnText(editor));
  });

  it("N2: the grid rows are exactly the shared field table — no editor-specific field text", async () => {
    render(<StrategyEditor mode="new" strategies={STRATEGIES} onSaved={vi.fn()} onCancel={vi.fn()} />);
    await screen.findByLabelText("Buy threshold");
    const known = new Map(ADVANCED_GROUPS.flatMap((g) => g.fields).map((f) => [f.key, f]));
    const rows = Array.from(document.querySelectorAll<HTMLElement>("[data-key]"));
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      const f = known.get(row.dataset.key ?? "");
      expect(f, row.dataset.key).toBeDefined();
      expect(row.querySelector("[data-copy=description]")?.textContent).toBe(f!.desc);
    }
  });

  it("N2b #5003: the editor shows the six groups in the order of the decision flow", async () => {
    render(<StrategyEditor mode="new" strategies={STRATEGIES} onSaved={vi.fn()} onCancel={vi.fn()} />);
    await screen.findByLabelText("Buy threshold");
    const titles = Array.from(
      screen.getByTestId("strategy-editor").querySelectorAll(".surface-flat > .sublabel"),
    ).map((e) => e.textContent);
    expect(titles.slice(0, 6)).toEqual([
      "Candidates", "Decision", "Position sizing", "Portfolio limits", "Buy pacing", "Exits",
    ]);
  });

  it("N2c #5003: no internal term in a row, and no reference to another card", async () => {
    render(<StrategyEditor mode="new" strategies={STRATEGIES} onSaved={vi.fn()} onCancel={vi.fn()} />);
    await screen.findByLabelText("Buy threshold");
    expect(screen.getByTestId("strategy-editor").textContent).not.toMatch(
      /\bbook\b|ADR-SEC-01|armed direction agents|cross-sectional ranking panel|dynamic sizer/i,
    );
    expect(screen.queryByTestId("sizing-references")).toBeNull();
  });

  it("N3: no strategy is preselected and the strategies keep the given order", async () => {
    render(<StrategyEditor mode="new" strategies={[STRATEGIES[1], STRATEGIES[0]]} onSaved={vi.fn()} onCancel={vi.fn()} />);
    await screen.findByLabelText("Buy threshold");
    const select = screen.getByLabelText("Source") as HTMLSelectElement;
    expect(select.value).toBe("");
    const names = Array.from(select.querySelectorAll("optgroup option")).map((o) => o.textContent);
    expect(names).toEqual(["B", "A"]);
  });
});
