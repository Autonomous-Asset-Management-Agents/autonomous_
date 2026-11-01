/**
 * STW-4c (#4873) — StrategyList integration with StrategyEditor.
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { payload } from "./advancedTradingFixtures";

vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));

const listStrategies = vi.fn();
const saveStrategy = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listStrategies: (...a: unknown[]) => listStrategies(...a),
    saveStrategy: (...a: unknown[]) => saveStrategy(...a),
    deleteStrategy: vi.fn(),
    getTradingSettings: vi.fn().mockResolvedValue(payload()),
    listStrategyVersions: vi.fn().mockResolvedValue({ name: "x", versionen: [] }),
  };
});

import { StrategyList } from "@/console/desktop/strategyWorkshop/StrategyList";

const TWO = {
  strategien: [
    { name: "Wider stops", werte: { STOP_LOSS_PCT: "6.0" } },
    { name: "Momentum only", werte: { STOP_LOSS_PCT: "5.0" } },
  ],
  max_strategien: 3,
};

const bodyRows = () => within(screen.getByRole("table")).getAllByRole("row").slice(1);
const rowOf = (name: string) => bodyRows().find((r) => within(r).queryByText(name)) as HTMLElement;
const sliderValue = (label: string) =>
  (screen.getAllByLabelText(label).find((e) => e.tagName === "INPUT") as HTMLInputElement).value;

async function openList() {
  render(<StrategyList withEditor />);
  await screen.findByRole("table");
}

describe("StrategyList → StrategyEditor (STW-4c #4873)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listStrategies.mockResolvedValue(TWO);
    saveStrategy.mockImplementation((name: string, werte: Record<string, string>) =>
      Promise.resolve({ success: true, aktion: "create", name, werte, detail: "recorded" }),
    );
  });

  it("New strategy: opens the editor; after the save the list reloads and the strategy is the last row", async () => {
    await openList();
    expect(screen.queryByTestId("strategy-editor")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "New strategy" }));
    const editor = await screen.findByTestId("strategy-editor");
    await within(editor).findByLabelText("Buy threshold");
    expect((within(editor).getByLabelText("Source") as HTMLSelectElement).value).toBe("");

    listStrategies.mockResolvedValue({ ...TWO, strategien: [...TWO.strategien, { name: "Third", werte: {} }] });
    fireEvent.change(within(editor).getByLabelText("Strategy name"), { target: { value: "Third" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save strategy" }));

    await waitFor(() => expect(bodyRows()).toHaveLength(3));
    expect(within(bodyRows()[2]).getByText("Third")).toBeTruthy();
    expect(listStrategies).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Strategy saved. The change is recorded in the audit chain.")).toBeTruthy();
  });

  it("Create strategy (empty list) opens the editor as well", async () => {
    listStrategies.mockResolvedValue({ strategien: [], max_strategien: 3 });
    render(<StrategyList withEditor />);
    fireEvent.click(await screen.findByRole("button", { name: "Create strategy" }));
    expect(await screen.findByTestId("strategy-editor")).toBeTruthy();
  });

  it("Duplicate: opens the editor with that strategy as source and an empty name", async () => {
    await openList();
    fireEvent.click(within(rowOf("Momentum only")).getByRole("button", { name: "Duplicate" }));
    const editor = await screen.findByTestId("strategy-editor");
    await within(editor).findByLabelText("Buy threshold");
    expect((within(editor).getByLabelText("Source") as HTMLSelectElement).value).toBe("Momentum only");
    expect((within(editor).getByLabelText("Strategy name") as HTMLInputElement).value).toBe("");
    expect(sliderValue("Broker stop distance")).toBe("5");
  });

  it("Edit: opens the editor on that strategy with the name read-only; Cancel closes it", async () => {
    await openList();
    fireEvent.click(within(rowOf("Wider stops")).getByRole("button", { name: "Edit" }));
    const editor = await screen.findByTestId("strategy-editor");
    await within(editor).findByLabelText("Buy threshold");
    const name = within(editor).getByLabelText("Strategy name") as HTMLInputElement;
    expect(name.value).toBe("Wider stops");
    expect(name.readOnly).toBe(true);
    expect(sliderValue("Broker stop distance")).toBe("6");

    fireEvent.click(within(editor).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByTestId("strategy-editor")).toBeNull());
    expect(saveStrategy).not.toHaveBeenCalled();
    expect(listStrategies).toHaveBeenCalledTimes(1);
  });
});
