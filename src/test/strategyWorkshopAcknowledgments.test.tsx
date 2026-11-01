/** STW-4e (#4892) — acknowledgment texts of the strategy workshop. */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useStore } from "@/console/store/useStore";
import { payload } from "./advancedTradingFixtures";

const pseudo = (key: string) => `⟦${key}⟧`;
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: pseudo, i18n: { changeLanguage: () => new Promise(() => {}), resolvedLanguage: "xx" } }),
  initReactI18next: { type: "3rdParty", init: () => {} },
}));
vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));

const restartEngine = vi.fn(), saveSetupState = vi.fn();
vi.mock("@/lib/desktopBridge", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/desktopBridge")>()),
  isDesktop: () => true, getSetupState: () => Promise.resolve({}),
  restartEngine: (...a: unknown[]) => restartEngine(...a), saveSetupState: (...a: unknown[]) => saveSetupState(...a),
}));

const listStrategies = vi.fn(), assignLiveStrategy = vi.fn(), saveStrategy = vi.fn(), deleteStrategy = vi.fn(), getTradingSettings = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listStrategies: (...a: unknown[]) => listStrategies(...a), listStrategyAssignments: () => Promise.resolve([]),
  listStrategyVersions: () => Promise.resolve({ name: "A", versionen: [] }),
  assignLiveStrategy: (...a: unknown[]) => assignLiveStrategy(...a), saveStrategy: (...a: unknown[]) => saveStrategy(...a),
  deleteStrategy: (...a: unknown[]) => deleteStrategy(...a), getTradingSettings: (...a: unknown[]) => getTradingSettings(...a),
  getHitlPolicy: () => Promise.resolve({ HITL_ENABLED: true }),
  fetchHealth: vi.fn().mockResolvedValue({ status: "healthy", paper_trading: false }),
  postSwitchReconcile: vi.fn().mockResolvedValue(undefined),
}));

import { ASSIGN_ACK, DELETE_ACK, liveSizingAck, PAPER_ASSIGN_ACK, SAVE_ACK } from "@/console/desktop/strategyWorkshop/acknowledgments";
import { LiveStrategyAssignment } from "@/console/desktop/strategyWorkshop/LiveStrategyAssignment";
import { StrategyEditor } from "@/console/desktop/strategyWorkshop/StrategyEditor";
import { StrategyList } from "@/console/desktop/strategyWorkshop/StrategyList";

const PINNED = {
  ASSIGN_ACK: { text: "I confirm assigning this strategy to the live account. Its settings apply to all future trading decisions and the assignment is recorded on a tamper-evident audit trail.", sha256: "a02484937d5745f796cc8a7c069890aafbdfcb5ef6340695ba6107f61a86d843" },
  PAPER_ASSIGN_ACK: { text: "I confirm assigning this strategy to this paper account. Its settings apply to future simulated trading on this account and the assignment is recorded on a tamper-evident audit trail.", sha256: "b1795dc752d732ac6146b4bb91261c4634e62117d16915606cfe517549f7d85a" },
  SAVE_ACK: { text: "I confirm saving this strategy. The change is recorded on a tamper-evident audit trail.", sha256: "475324eff00e445f617145781c35822801c4bca2d3d9144de80db30c141c6ebc" },
  DELETE_ACK: { text: "I confirm deleting this strategy. The deletion is recorded on a tamper-evident audit trail.", sha256: "48fd0cc8118f55416097af301ac3e2532901ef9419c81999d8f4cfc5349c7dd5" },
};
const ACTUAL = { ASSIGN_ACK, PAPER_ASSIGN_ACK, SAVE_ACK, DELETE_ACK };
const sha256 = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");
const k = (id: string) => pseudo(`console.strategyWorkshop.${id}`);

const STRATEGIES = { strategien: [{ name: "A", werte: { STOP_LOSS_PCT: "6.0" } }, { name: "B", werte: { STOP_LOSS_PCT: "5.0" } }], max_strategien: 3 };

function flatValues(obj: unknown): string[] {
  if (typeof obj === "string") return [obj];
  return obj && typeof obj === "object" ? Object.values(obj).flatMap(flatValues) : [];
}

describe("strategy workshop acknowledgments (STW-4e #4892)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    act(() => useStore.setState({ paperTrading: false }));
    listStrategies.mockResolvedValue(STRATEGIES);
    getTradingSettings.mockResolvedValue(payload());
    assignLiveStrategy.mockResolvedValue({ success: true, strategie: "B", applied: {}, changes: [], detail: "" });
    saveStrategy.mockImplementation((name: string, werte: Record<string, string>) =>
      Promise.resolve({ success: true, aktion: "create", name, werte, detail: "recorded" }),
    );
    deleteStrategy.mockResolvedValue({ success: true, aktion: "delete", name: "A", werte: {}, detail: "x" });
    restartEngine.mockResolvedValue(undefined);
    saveSetupState.mockResolvedValue(undefined);
  });

  it("A1: the texts equal their pinned wording and SHA-256", () => {
    for (const [name, pin] of Object.entries(PINNED)) {
      const actual = ACTUAL[name as keyof typeof ACTUAL];
      expect(actual, name).toBe(pin.text);
      expect(sha256(actual), name).toBe(pin.sha256);
    }
  });

  it("A1b: liveSizingAck equals the text the engine builds (#4989)", () => {
    expect(liveSizingAck("b", "a")).toBe(
      'I confirm changing the live sizing method from "b" to "a". The change applies to real-money trading and is recorded on a tamper-evident audit trail.',
    );
  });

  it("A2: acknowledgments.ts imports no i18n", () => {
    const src = readFileSync(resolve(process.cwd(), "src/console/desktop/strategyWorkshop/acknowledgments.ts"), "utf8");
    expect(src).not.toMatch(/from\s+["'](react-i18next|i18next|@\/lib\/i18n)["']/);
    expect(src).not.toMatch(/\bimport\b/);
  });

  it("A3: assign sends ASSIGN_ACK byte for byte in a pseudo-locale", async () => {
    render(<LiveStrategyAssignment />);
    fireEvent.change(await screen.findByLabelText(k("T39")), { target: { value: "B" } });
    fireEvent.click(screen.getByRole("button", { name: k("T40") }));
    await screen.findByRole("dialog", { name: k("T41") });
    fireEvent.click(screen.getByRole("checkbox", { name: /continues trading LIVE/i }));
    fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
    await waitFor(() => expect(assignLiveStrategy).toHaveBeenCalledTimes(1));
    expect(assignLiveStrategy.mock.calls[0][1]).toBe(PINNED.ASSIGN_ACK.text);
  });

  it("A3: save sends SAVE_ACK byte for byte in a pseudo-locale", async () => {
    render(<StrategyEditor mode="new" strategies={STRATEGIES.strategien} onSaved={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText(k("T05")), { target: { value: "C" } });
    const save = await screen.findByRole("button", { name: k("T06") });
    await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(saveStrategy).toHaveBeenCalledTimes(1));
    expect(saveStrategy.mock.calls[0][2]).toBe(PINNED.SAVE_ACK.text);
  });

  it("A3: delete sends DELETE_ACK byte for byte in a pseudo-locale", async () => {
    render(<StrategyList />);
    const table = await screen.findByRole("table");
    const row = within(table).getAllByRole("row").find((r) => within(r).queryByText("A")) as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: k("T44") }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: k("T44") }));
    await waitFor(() => expect(deleteStrategy).toHaveBeenCalledTimes(1));
    expect(deleteStrategy.mock.calls[0][1]).toBe(PINNED.DELETE_ACK.text);
  });

  it("A4: no catalogue value equals an acknowledgment text", () => {
    const read = (lng: string) =>
      flatValues(JSON.parse(readFileSync(resolve(process.cwd(), `src/locales/${lng}/translation.json`), "utf8")));
    const sealed = Object.values(PINNED).map((p) => p.text);
    for (const lng of ["en", "de"]) {
      expect(read(lng).filter((v) => sealed.includes(v)), lng).toEqual([]);
    }
  });
});
