/** STW-4e (#4892) — engine error → workshop text. */
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { HttpError } from "@/lib/api";
import { payload } from "./advancedTradingFixtures";
import en from "@/locales/en/translation.json";

vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));

const saveStrategy = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getTradingSettings: () => Promise.resolve(payload()),
  saveStrategy: (...a: unknown[]) => saveStrategy(...a),
  listStrategyVersions: () => Promise.resolve({ name: "A", versionen: [] }),
}));

import { workshopErrorText } from "@/console/desktop/strategyWorkshop/errorText";
import { StrategyEditor } from "@/console/desktop/strategyWorkshop/StrategyEditor";

type Catalogue = { console: { strategyWorkshop: { errors: Record<string, string> } } };
const ns = (en as unknown as Catalogue).console.strategyWorkshop;
const get = (key: string): unknown =>
  key.split(".").reduce<unknown>((o, p) => (o && typeof o === "object" ? (o as Record<string, unknown>)[p] : undefined), en);
const t = (key: string) => (typeof get(key) === "string" ? (get(key) as string) : key);
const LIMIT_TEXT = ns.errors.strategy_limit_reached;
const http = (status: number, body?: unknown) => new HttpError(status, "x", "/api/strategies", body);

describe("workshopErrorText (STW-4e #4892)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("E1: a known code reads its catalogue text", () => {
    const err = http(422, { detail: "At most 3 strategies — delete one first", code: "strategy_limit_reached", params: { limit: 3 } });
    expect(LIMIT_TEXT).toBe("You have three strategies. Delete one to create another.");
    expect(workshopErrorText(t, err)).toBe(LIMIT_TEXT);
  });

  it("E2: an unknown code shows the English detail", () => {
    const err = http(422, { detail: "Something the console does not know yet", code: "brand_new_code" });
    expect(workshopErrorText(t, err)).toBe("Something the console does not know yet");
  });

  it("E3: neither code nor detail → the fallback key (default: the error state)", () => {
    expect(workshopErrorText(t, http(503))).toBe("Could not load the data.");
    expect(workshopErrorText(t, new Error("network"))).toBe("Could not load the data.");
    expect(workshopErrorText(t, http(503), "console.strategyWorkshop.extra.saveFailed")).toBe("Could not save the strategy.");
  });

  it("E4: the editor shows the catalogue text of strategy_limit_reached in its alert", async () => {
    saveStrategy.mockRejectedValue(http(422, { detail: "At most 3 strategies — delete one first", code: "strategy_limit_reached", params: { limit: 3 } }));
    render(<StrategyEditor mode="new" strategies={[]} onSaved={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("Strategy name"), { target: { value: "X" } });
    await screen.findByLabelText("Buy threshold");
    fireEvent.click(screen.getByRole("button", { name: "Save strategy" }));
    expect((await screen.findByRole("alert")).textContent).toBe(LIMIT_TEXT);
  });
});
