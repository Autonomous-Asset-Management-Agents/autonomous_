/**
 * STW-2b (#4934) — neutrality guard for "Paper accounts", after the pattern of
 * strategyListNeutrality.test.tsx with the word list of the UX concept
 * (docs/4628-ux-konzept-strategie-werkstatt/ux_konzept.md, "Neutrale Wortliste") and the
 * public wording guard (wordingGuard.ts). Checked is what the user reads: the three slots
 * (one connected, one with an open form, one empty) and the open disconnect confirmation.
 */
import { render, screen, fireEvent, within } from "@testing-library/react";
import { waehle, wert, optionen } from "./listboxHelfer";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { FORBIDDEN } from "./wordingGuard";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listStrategies: vi.fn().mockResolvedValue({ strategien: [{ name: "North", werte: { STOP_LOSS_PCT: "5.0" } }, { name: "South", werte: {} }], max_strategien: 3 }),
  listStrategyAssignments: vi.fn().mockResolvedValue([{ konto: "paper1", strategie: "South", strategie_hash: "sha256:x", timestamp: "2026-10-30T10:00:00+00:00" }]),
  getTradingSettings: vi.fn().mockResolvedValue({ settings: {}, meta: [{ key: "STOP_LOSS_PCT", kind: "float", default: "7.0" }] }),
  fetchHealth: vi.fn().mockResolvedValue(null),
}));

import { PaperAccountSlots } from "@/console/desktop/strategyWorkshop/PaperAccountSlots";
// #5187: the key form, Disconnect, Start and Rename of Paper account 2/3 stand under Trading keys.
import { TradingKeysCard } from "@/console/desktop/TradingKeysCard";

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
    render(<TradingKeysCard />);
    const third = await screen.findByRole("group", { name: "Paper account 3" });
    fireEvent.click(within(third).getByRole("button", { name: "Connect" }));
    fireEvent.click(within(screen.getByRole("group", { name: "Paper account 2" })).getByRole("button", { name: "Disconnect" }));
    await screen.findByRole("alertdialog");

    const text = visibleText(document.body);
    for (const re of BANNED) expect(text, String(re)).not.toMatch(re);
    expect(text.match(FORBIDDEN)).toBeNull();
    for (const b of screen.getAllByRole("button", { hidden: true })) expect(b.textContent ?? "").not.toMatch(ADOPT);
  });

  it("N4: selection, preview (#5117) and review dialog (T14, #4953): no preselection, ranking, evaluative wording or adopt button", async () => {
    render(<PaperAccountSlots />);
    const first = await screen.findByRole("group", { name: "Paper account 1" });
    const feld = within(first).getByLabelText("Strategy for this account");
    expect(wert(feld)).toBe("");
    expect(optionen(feld)).toEqual(["North", "South"]);
    waehle(feld, "North");
    const preview = await within(first).findByRole("group", { name: "Changes on confirm" });
    const previewText = visibleText(preview);
    for (const re of BANNED) expect(previewText, String(re)).not.toMatch(re);
    expect(previewText.match(FORBIDDEN)).toBeNull();
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
    render(<TradingKeysCard />);
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

  it("N5: setup account, restart notice and its confirmation (#5142)", async () => {
    const getFleetSession = vi.fn().mockResolvedValue({ fleetActive: true });
    Object.assign((window as unknown as { aaagents: Record<string, unknown> }).aaagents, {
      listPaperSlots: vi.fn().mockResolvedValue([
        { slot: "paper1", connected: true, accountMasked: null, engineStatus: null, setupAccount: true, mainEngineLive: true },
        { slot: "paper2", connected: true, accountMasked: "••••2222", engineStatus: "running" },
        { slot: "paper3", connected: false, accountMasked: null, engineStatus: null },
      ]),
      getFleetSession,
      getSetupState: vi.fn().mockResolvedValue({ strategyProfilesEnabled: true }),
      relaunchApp: vi.fn(),
    });
    const check = () => {
      const text = visibleText(document.body);
      for (const re of BANNED) expect(text, String(re)).not.toMatch(re);
      expect(text.match(FORBIDDEN)).toBeNull();
      for (const b of screen.getAllByRole("button", { hidden: true })) expect(b.textContent ?? "").not.toMatch(ADOPT);
      return text;
    };

    const first = render(<PaperAccountSlots />);
    await screen.findByRole("group", { name: "Paper account 2" });
    expect(within(screen.getByRole("group", { name: "Paper account 3" })).getByRole("link", { name: "Connect under System → Trading keys" })).toHaveClass("btn-link");
    expect(check()).toMatch(/main engine trades the live account/);
    first.unmount();

    getFleetSession.mockResolvedValue({ fleetActive: false });
    render(<PaperAccountSlots />);
    fireEvent.click(await screen.findByRole("button", { name: "Restart app" }));
    await screen.findByRole("alertdialog");
    expect(check()).toMatch(/Restart the app/);
  });

  it("N6: every new catalog text of #5142 passes the word list, en and de", async () => {
    const keys = [
      "invalidKeys", "network", "setupKeysMissing", "mainEngineLive", "restartRequired", "restartApp",
      "restartConfirm", "restart",
    ];
    // ADOPT is a button rule (N1): only the labels of buttons are held to it.
    const buttons = new Set(["restartApp", "restart"]);
    for (const lang of ["en", "de"]) {
      const { default: cat } = await import(`../locales/${lang}/translation.json`);
      const deviation = cat.console.strategyWorkshop.deviation as Record<string, unknown>;
      for (const k of keys) {
        const v = deviation[k];
        expect(typeof v, `${lang}.${k}`).toBe("string");
        for (const re of BANNED) expect(v as string, `${lang}.${k} ${re}`).not.toMatch(re);
        expect((v as string).match(FORBIDDEN)).toBeNull();
        if (buttons.has(k)) expect(v as string).not.toMatch(ADOPT);
      }
    }
  });

  it("N7: the rename texts of #5148 pass the word list, en and de, and so does the open name field", async () => {
    const keys = ["rename", "accountName", "save"];
    for (const lang of ["en", "de"]) {
      const { default: cat } = await import(`../locales/${lang}/translation.json`);
      const deviation = cat.console.strategyWorkshop.deviation as Record<string, unknown>;
      for (const k of keys) {
        const v = deviation[k];
        expect(typeof v, `${lang}.${k}`).toBe("string");
        for (const re of BANNED) expect(v as string, `${lang}.${k} ${re}`).not.toMatch(re);
        expect((v as string).match(FORBIDDEN)).toBeNull();
        expect(v as string).not.toMatch(ADOPT);
      }
    }

    Object.assign((window as unknown as { aaagents: Record<string, unknown> }).aaagents, {
      renamePaperSlot: vi.fn().mockResolvedValue({ ok: true }),
    });
    render(<TradingKeysCard />);
    const second = await screen.findByRole("group", { name: "Paper account 2" });
    fireEvent.click(within(second).getByRole("button", { name: "Rename" }));
    within(second).getByLabelText("Account name");
    const text = visibleText(document.body);
    for (const re of BANNED) expect(text, String(re)).not.toMatch(re);
    expect(text.match(FORBIDDEN)).toBeNull();
    for (const b of screen.getAllByRole("button", { hidden: true })) expect(b.textContent ?? "").not.toMatch(ADOPT);
  });

  it("N8: the link texts of #5187 pass the word list, en and de", async () => {
    for (const lang of ["en", "de"]) {
      const { default: cat } = await import(`../locales/${lang}/translation.json`);
      for (const v of [cat.console.strategyWorkshop.extra.connectUnderTradingKeys, cat.console.tradingKeys.restartOnStrategies]) {
        expect(typeof v, lang).toBe("string");
        for (const re of BANNED) expect(v as string, `${lang} ${re}`).not.toMatch(re);
        expect((v as string).match(FORBIDDEN)).toBeNull();
        expect(v as string).not.toMatch(ADOPT);
      }
    }
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
