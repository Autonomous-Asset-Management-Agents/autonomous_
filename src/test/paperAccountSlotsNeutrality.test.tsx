/**
 * STW-2b (#4934) — neutrality guard for "Paper accounts", after the pattern of
 * strategyListNeutrality.test.tsx with the word list of the UX concept
 * (docs/4628-ux-konzept-strategie-werkstatt/ux_konzept.md, "Neutrale Wortliste") and the
 * public wording guard (wordingGuard.ts). Checked is what the user reads: the three slots
 * (one connected, one with an open form, one empty) and the open disconnect confirmation.
 */
import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { FORBIDDEN } from "./wordingGuard";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listStrategies: vi.fn().mockResolvedValue({ strategien: [{ name: "North", werte: { STOP_LOSS_PCT: "5.0" } }, { name: "South", werte: {} }], max_strategien: 3 }),
  listStrategyAssignments: vi.fn().mockResolvedValue([{ konto: "paper1", strategie: "South", strategie_hash: "sha256:x", timestamp: "2026-10-30T10:00:00+00:00" }]),
  getTradingSettings: vi.fn().mockResolvedValue({ settings: {}, meta: [{ key: "STOP_LOSS_PCT", kind: "float", default: "7.0" }] }),
}));

import { PaperAccountSlots } from "@/console/desktop/strategyWorkshop/PaperAccountSlots";

const BANNED = [
  /\bbest\b/i, /recommend/i, /empfohlen/i, /winner/i, /\btop\b/i, /top-ranked/i, /\brank/i,
  /outperform/i, /\bsafe(r|st)?\b/i, /risky/i, /guarantee/i, /aggressive/i, /conservative/i,
  /profile/i, /sharpe/i,
];
const ADOPT = /apply|adopt|use this|go live/i;

function visibleText(root: Node): string {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const parts: string[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) parts.push(n.nodeValue ?? "");
  return parts.join(" ");
}

describe("PaperAccountSlots neutrality (STW-2b #4934)", () => {
  beforeEach(() => {
    (window as unknown as { aaagents: unknown }).aaagents = {
      isDesktop: true,
      listPaperSlots: vi.fn().mockResolvedValue([
        { slot: "paper1", connected: true, accountMasked: "••••1111", engineStatus: "running" },
        { slot: "paper2", connected: true, accountMasked: "••••2222", engineStatus: "stopped" },
        { slot: "paper3", connected: false, accountMasked: null, engineStatus: null },
      ]),
      connectPaperSlot: vi.fn(),
      disconnectPaperSlot: vi.fn(),
      startPaperSlotEngine: vi.fn(),
      getPaperSlotSetup: vi.fn().mockResolvedValue({}),
      savePaperSlotSetup: vi.fn(),
      restartPaperSlotEngine: vi.fn(),
    };
  });
  afterEach(() => {
    delete (window as unknown as { aaagents?: unknown }).aaagents;
  });

  it("N1: no ranking, evaluative wording or adopt button in the slots or the open confirmation", async () => {
    render(<PaperAccountSlots />);
    const third = await screen.findByRole("group", { name: "Paper account 3" });
    fireEvent.click(within(third).getByRole("button", { name: "Connect" }));
    fireEvent.click(within(screen.getByRole("group", { name: "Paper account 1" })).getByRole("button", { name: "Disconnect" }));
    await screen.findByRole("alertdialog");

    const text = visibleText(document.body);
    for (const re of BANNED) expect(text, String(re)).not.toMatch(re);
    expect(text.match(FORBIDDEN)).toBeNull();
    for (const b of screen.getAllByRole("button", { hidden: true })) expect(b.textContent ?? "").not.toMatch(ADOPT);
  });

  it("N4: selection and review dialog (T14, #4953): no preselection, ranking, evaluative wording or adopt button", async () => {
    render(<PaperAccountSlots />);
    const first = await screen.findByRole("group", { name: "Paper account 1" });
    const select = within(first).getByLabelText("Strategy for this account") as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["—", "North", "South"]);
    fireEvent.change(select, { target: { value: "North" } });
    fireEvent.click(within(first).getByRole("button", { name: "Review changes" }));
    await screen.findByRole("dialog", { name: "Assign strategy to this paper account?" });

    const text = visibleText(document.body);
    for (const re of BANNED) expect(text, String(re)).not.toMatch(re);
    expect(text.match(FORBIDDEN)).toBeNull();
    for (const b of screen.getAllByRole("button", { hidden: true })) expect(b.textContent ?? "").not.toMatch(ADOPT);
  });

  it("N3: low-memory shows no number, ranking or recommendation (#4952)", async () => {
    const shell = (window as unknown as { aaagents: { startPaperSlotEngine: ReturnType<typeof vi.fn> } }).aaagents;
    shell.startPaperSlotEngine.mockResolvedValue({ ok: false, code: "low_memory" });
    render(<PaperAccountSlots />);
    const second = await screen.findByRole("group", { name: "Paper account 2" });
    fireEvent.click(await within(second).findByRole("button", { name: "Start engine" }));
    const notice = (await within(second).findByText(/not have enough memory/)).closest("[data-state]") as HTMLElement;
    expect(notice).toHaveAttribute("data-state", "low-memory");

    const text = visibleText(notice);
    expect(text).not.toMatch(/\d/);
    for (const re of BANNED) expect(text, String(re)).not.toMatch(re);
    expect(text.match(FORBIDDEN)).toBeNull();
    for (const b of within(notice).getAllByRole("button")) expect(b.textContent ?? "").not.toMatch(ADOPT);
  });

  it("N2: no slot is highlighted — every slot carries the same classes and no marker", async () => {
    render(<PaperAccountSlots />);
    await screen.findByRole("group", { name: "Paper account 1" });
    const groups = within(screen.getByRole("region", { name: "Paper accounts" })).getAllByRole("group");
    expect(groups).toHaveLength(3);
    expect(new Set(groups.map((g) => g.className)).size).toBe(1);
    for (const g of groups) {
      expect(g.getAttribute("aria-current")).toBeNull();
      expect(g.getAttribute("data-highlight")).toBeNull();
      expect(g.className).not.toMatch(/bull|bear|green|red|emerald|amber|gold/i);
    }
  });
});
