/** STW-7 (#4633) — word and tone guard of the live/paper comparison (ADR-027 Regel 2/3). */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const getEngineFleet = vi.fn();
vi.mock("@/lib/desktopBridge", async (orig) => ({ ...(await orig<typeof import("@/lib/desktopBridge")>()), getEngineFleet: () => getEngineFleet() }));

const fetchBenchmarkEquityAt = vi.fn();
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listStrategies: vi.fn().mockResolvedValue({ strategien: [], max_strategien: 3 }),
  fetchBenchmarkEquityAt: (...a: unknown[]) => fetchBenchmarkEquityAt(...a),
}));

import { AccountComparison } from "@/console/desktop/strategyWorkshop/AccountComparison";

const BANNED_WORDS: RegExp[] = [
  /\bbest\b/i, /recommend/i, /empfohlen/i, /\bwinner/i, /\btop\b/i, /top-ranked/i,
  /\brank(ing|ed)?\b/i, /outperform/i, /\bsafe(r|st)?\b/i, /\brisky\b/i, /guarantee/i,
  /aggressive/i, /conservative/i, /\bprofile/i, /sharpe/i,
];
const BANNED_CONTROLS: RegExp[] = [/apply/i, /adopt/i, /assign/i, /use this/i, /go live/i];

function series(days: number, dailyPct: number) {
  const t0 = Date.parse("2026-09-01T00:00:00Z");
  return Array.from({ length: days }, (_, i) => ({
    date: new Date(t0 + i * 86_400_000).toISOString().slice(0, 10),
    equity: 1000 * (1 + dailyPct / 100) ** i,
  }));
}

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

function assertNeutral(root: HTMLElement) {
  for (const t of texts(root)) {
    for (const re of BANNED_WORDS) expect(t, `banned word ${re}`).not.toMatch(re);
  }
  const controls = [...within(root).queryAllByRole("button"), ...within(root).queryAllByRole("link")];
  for (const c of controls) {
    const caption = `${c.textContent ?? ""} ${c.getAttribute("aria-label") ?? ""}`;
    for (const re of BANNED_CONTROLS) expect(caption, `banned control ${re}`).not.toMatch(re);
  }
  const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1) as HTMLTableRowElement[];
  expect(new Set(rows.map((r) => r.className)).size).toBe(1);
  for (let col = 0; col < rows[0].cells.length; col++) {
    expect(new Set(rows.map((r) => r.cells[col].className)).size, `column ${col} tone`).toBe(1);
  }
  expect(root.innerHTML).not.toMatch(/\b(bull|bear|text-(green|red|emerald|rose)|positive|negative)\b/);
}

describe("AccountComparison neutrality (#4633)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getEngineFleet.mockResolvedValue([
      { id: "live", kind: "live", port: 8001 },
      { id: "p1", kind: "paper", port: 8002 },
      { id: "p2", kind: "paper", port: 8003 },
    ]);
    const daily: Record<number, number> = { 8001: 0.1, 8002: -0.2, 8003: 0.3 };
    fetchBenchmarkEquityAt.mockImplementation(async (port: number) => ({
      points: series(30, daily[port]),
      spy_points: series(30, 0.05),
    }));
  });

  it("no word of the list, no adopting control, one tone per row — unsorted and sorted", async () => {
    const { container } = render(<AccountComparison onAdjust={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Compare account performance" }));
    await waitFor(() => expect(within(screen.getByRole("table")).getAllByRole("row")[3].textContent).toMatch(/%/));
    assertNeutral(container);
    fireEvent.click(screen.getByRole("button", { name: "TWR" }));
    fireEvent.click(screen.getByRole("button", { name: "TWR" }));
    assertNeutral(container);
  });

  it("states a period-wide figure, never a difference between accounts", async () => {
    render(<AccountComparison />);
    fireEvent.click(await screen.findByRole("button", { name: "Compare account performance" }));
    await waitFor(() => expect(within(screen.getByRole("table")).getAllByRole("row")[3].textContent).toMatch(/%/));
    const head = within(screen.getByRole("table")).getAllByRole("columnheader").map((h) => h.textContent);
    expect(head).toEqual(["Account", "Strategy", "TWR", "Max drawdown", "SPY (same period)", ""]);
    expect(document.body.textContent).not.toMatch(/\b(vs\.?|versus|difference|delta|ahead|behind)\b/i);
  });
});
