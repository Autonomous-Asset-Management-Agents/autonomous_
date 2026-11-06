/**
 * STW-9c (#4918) — word and tone guard of the strategy simulation (ADR-027 Regel 2/3), after
 * accountComparisonNeutrality4633.test.tsx with the word list of the UX concept (#4628,
 * "Neutrale Wortliste") and the list of #4918 ("Goal"). `drawdown` is allowed here: it is a
 * mandatory figure.
 */
import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listStrategies: vi.fn().mockResolvedValue({
    strategien: [{ name: "A", werte: {} }, { name: "B", werte: {} }, { name: "C", werte: {} }],
    max_strategien: 3,
  }),
  startStrategySimulation: vi.fn().mockResolvedValue({ run_id: "0123456789abcdef", status: "laeuft" }),
  getStrategySimulation: vi.fn().mockResolvedValue({
    run_id: "0123456789abcdef",
    status: "fertig",
    ergebnis: {
      zeitraum: { beginn: "2026-01-02", ende: "2026-06-30" },
      strategien: [
        { name: "A", twr_pct: 4.5, max_drawdown_pct: 3.0, spy_twr_pct: 6.0, spy_max_drawdown_pct: 5.0 },
        { name: "B", twr_pct: -2.0, max_drawdown_pct: 8.0, spy_twr_pct: 6.0, spy_max_drawdown_pct: 5.0 },
        { name: "C", twr_pct: 9.0, max_drawdown_pct: 1.0, spy_twr_pct: 6.0, spy_max_drawdown_pct: 5.0 },
        { name: "@delivery-state", twr_pct: 5.0, max_drawdown_pct: 2.0, spy_twr_pct: 6.0, spy_max_drawdown_pct: 5.0 },
      ],
      unsicherheit: { pfade: 1, handelstage: 120 },
      nicht_abgebildet: [
        { art: "survivorship", text: "x" },
        { art: "quelle", text: "y" },
        { art: "ohne_lstm" },
        { art: "agenten", strategie: "A", agenten: ["NewsSentimentAgent"] },
      ],
    },
  }),
}));

import { StrategySimulationCompare } from "@/console/desktop/strategyWorkshop/StrategySimulationCompare";

const BANNED_WORDS: RegExp[] = [
  /\bbest\b/i, /recommend/i, /empfohlen/i, /\bwinner/i, /\btop\b/i, /top-ranked/i,
  /\brank(ing|ed)?\b/i, /outperform/i, /\bsafe(r|st)?\b/i, /\brisky\b/i, /guarantee/i,
  /aggressive/i, /conservative/i, /\bprofile/i, /sharpe/i, /\badopt/i, /\bapply\b/i, /use this/i,
];
const BANNED_CONTROLS: RegExp[] = [/apply/i, /adopt/i, /assign/i, /\buse\b/i, /go live/i];

function texts(root: HTMLElement): string[] {
  const out: string[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) out.push(n.textContent ?? "");
  root.querySelectorAll("*").forEach((el) => {
    for (const attr of ["aria-label", "title", "placeholder", "alt"]) {
      const v = el.getAttribute(attr);
      if (v) out.push(v);
    }
  });
  return out;
}

function assertWords(root: HTMLElement) {
  for (const t of texts(root)) {
    for (const re of BANNED_WORDS) expect(t, `banned word ${re}`).not.toMatch(re);
  }
  const controls = [...within(root).queryAllByRole("button"), ...within(root).queryAllByRole("link")];
  for (const c of controls) {
    const caption = `${c.textContent ?? ""} ${c.getAttribute("aria-label") ?? ""}`;
    for (const re of BANNED_CONTROLS) expect(caption, `banned control ${re}`).not.toMatch(re);
  }
}

function assertNeutral(root: HTMLElement) {
  assertWords(root);
  const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1) as HTMLTableRowElement[];
  expect(new Set(rows.map((r) => r.className)).size).toBe(1);
  for (let col = 0; col < rows[0].cells.length; col++) {
    expect(new Set(rows.map((r) => r.cells[col].className)).size, `column ${col} tone`).toBe(1);
  }
  expect(root.innerHTML).not.toMatch(/\b(bull|bear|text-(green|red|emerald|rose)|positive|negative)\b/);
}

async function openAndPick(names: string[]) {
  const view = render(<StrategySimulationCompare />);
  fireEvent.click(await screen.findByRole("button", { name: "Simulate" }));
  fireEvent.change(screen.getByLabelText("Start"), { target: { value: "2026-01-02" } });
  fireEvent.change(screen.getByLabelText("End"), { target: { value: "2026-06-30" } });
  for (const n of names) fireEvent.click(screen.getByRole("checkbox", { name: n }));
  return view;
}

describe("StrategySimulationCompare neutrality (#4918)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("no word of the list, no adopting control, one tone per row — setup, unsorted and sorted", async () => {
    const { container } = await openAndPick(["A", "B", "C"]);
    assertWords(container);
    // No strategy is preselected (ADR-027 Regel 1/2): the boxes the test just ticked are all.
    expect(within(container).getAllByRole("checkbox").filter((c) => (c as HTMLInputElement).checked)).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Run simulation" }));
    await screen.findByRole("table");
    assertNeutral(container);
    fireEvent.click(screen.getByRole("button", { name: "TWR" }));
    fireEvent.click(screen.getByRole("button", { name: "TWR" }));
    assertNeutral(container);
  });

  it("states a figure per strategy, never a difference between strategies", async () => {
    await openAndPick(["A"]);
    fireEvent.click(screen.getByRole("button", { name: "Run simulation" }));
    await screen.findByRole("table");
    expect(document.body.textContent).not.toMatch(/\b(vs\.?|versus|difference|delta|ahead|behind)\b/i);
  });

  it("#5126: the delivery state box and row look like every other — no badge, no own class", async () => {
    const T20 = "Delivery state (no strategy)";
    const { container } = await openAndPick(["A", "B", "C", T20]);
    const labels = within(container).getAllByRole("checkbox").map((c) => c.closest("label")!);
    expect(new Set(labels.map((l) => l.className)).size).toBe(1);
    expect(labels.at(-1)!.children).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Run simulation" }));
    await screen.findByRole("table");
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1) as HTMLTableRowElement[];
    const row = rows.find((r) => r.cells[0].textContent === T20);
    expect(row).toBeDefined();
    expect(row!.cells[0].children).toHaveLength(0);
    assertNeutral(container);
  });
});
