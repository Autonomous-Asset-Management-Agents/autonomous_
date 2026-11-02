/**
 * STW-4b (#4864) — "Strategies" card in Settings → Trading (UX-Konzept #4628, Ablauf 1,
 * part 1: list and delete; create/duplicate/edit are #4873).
 *
 * The rows come in the order of `GET /api/strategies` (no client sort, ADR-027 Regel 2),
 * the limit comes from `max_strategien`, and a deletion goes through the sealed
 * `DELETE /api/strategies/{name}` only after a confirmation (A1-P6).
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { HttpError } from "@/lib/api";

vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));

const listStrategies = vi.fn();
const deleteStrategy = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listStrategies: (...a: unknown[]) => listStrategies(...a),
    deleteStrategy: (...a: unknown[]) => deleteStrategy(...a),
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

const THREE = {
  strategien: [...TWO.strategien, { name: "C", werte: {} }],
  max_strategien: 3,
};

const bodyRows = () => within(screen.getByRole("table")).getAllByRole("row").slice(1);
const rowOf = (name: string) => bodyRows().find((r) => within(r).queryByText(name)) as HTMLElement;

async function renderList(props: Parameters<typeof StrategyList>[0] = {}) {
  render(<StrategyList {...props} />);
  await screen.findByText("Strategies");
}

describe("StrategyList (STW-4b #4864)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listStrategies.mockResolvedValue(TWO);
    deleteStrategy.mockResolvedValue({
      success: true,
      aktion: "delete",
      name: "Wider stops",
      werte: {},
      detail: "strategy deletion recorded on the WORM chain",
    });
  });

  it('A1-P1: empty list shows the empty state with "Create strategy"', async () => {
    listStrategies.mockResolvedValue({ strategien: [], max_strategien: 3 });
    const onCreate = vi.fn();
    await renderList({ onCreate });
    expect(
      await screen.findByText("No strategies yet. Create one from your current settings."),
    ).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Create strategy" }));
    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it("rows follow the API order (no client-side sort)", async () => {
    await renderList();
    await screen.findByRole("table");
    const heads = within(screen.getByRole("table")).getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads.slice(0, 2)).toEqual(["Name", "Assigned to"]);
    const rows = bodyRows();
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Wider stops")).toBeTruthy();
    expect(within(rows[1]).getByText("Momentum only")).toBeTruthy();
    // No assignment source yet: "Assigned to" shows a dash.
    expect(within(rows[0]).getByText("—")).toBeTruthy();
  });

  it('A1-P5: at max_strategien "New strategy" is disabled and T08 is visible', async () => {
    listStrategies.mockResolvedValue(THREE);
    await renderList({ onCreate: vi.fn() });
    const btn = (await screen.findByRole("button", { name: "New strategy" })) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toBe(
      "You have three strategies. Delete one to create another.",
    );
  });

  it("A1-P6: Delete asks first; confirm calls deleteStrategy with ack + nonce, then reloads", async () => {
    await renderList();
    await screen.findByRole("table");
    fireEvent.click(within(rowOf("Wider stops")).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Delete this strategy?")).toBeTruthy();
    expect(within(dialog).getByText("The deletion is recorded in the audit chain.")).toBeTruthy();
    expect(deleteStrategy).not.toHaveBeenCalled();

    listStrategies.mockResolvedValue({ strategien: [TWO.strategien[1]], max_strategien: 3 });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteStrategy).toHaveBeenCalledTimes(1));
    const [name, ack, nonce] = deleteStrategy.mock.calls[0];
    expect(name).toBe("Wider stops");
    expect(ack).toMatch(/deleting this strategy/i);
    expect(typeof nonce).toBe("string");
    expect((nonce as string).length).toBeGreaterThan(0);
    await waitFor(() => expect(bodyRows()).toHaveLength(1));
    expect(screen.queryByText("Wider stops")).toBeNull();
    expect(listStrategies).toHaveBeenCalledTimes(2);
  });

  it("A1-P6: Cancel calls nothing", async () => {
    await renderList();
    await screen.findByRole("table");
    fireEvent.click(within(rowOf("Wider stops")).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(deleteStrategy).not.toHaveBeenCalled();
    expect(bodyRows()).toHaveLength(2);
  });

  it("A1-P6: an assigned strategy is not deleted; the hint is shown", async () => {
    await renderList({ assignments: { "Wider stops": "Live account" } });
    await screen.findByRole("table");
    expect(within(rowOf("Wider stops")).getByText("Live account")).toBeTruthy();
    fireEvent.click(within(rowOf("Wider stops")).getByRole("button", { name: "Delete" }));
    expect(
      await screen.findByText(
        "This strategy is assigned to an account. Assign another strategy to that account first.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(deleteStrategy).not.toHaveBeenCalled();
  });

  it("a failed delete keeps the row and shows the error hint", async () => {
    deleteStrategy.mockRejectedValue(new HttpError(503, "Service Unavailable", "/api/strategies/x"));
    await renderList();
    await screen.findByRole("table");
    fireEvent.click(within(rowOf("Wider stops")).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("Could not load the data.")).toBeTruthy();
    expect(rowOf("Wider stops")).toBeTruthy();
    expect(bodyRows()).toHaveLength(2);
  });

  it('load error shows "Could not load the data." with Retry; Retry reloads', async () => {
    listStrategies.mockRejectedValueOnce(new HttpError(500, "Server Error", "/api/strategies"));
    await renderList();
    expect(await screen.findByText("Could not load the data.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("table");
    expect(listStrategies).toHaveBeenCalledTimes(2);
  });

  it("flag off: 404 → the card renders nothing", async () => {
    listStrategies.mockRejectedValue(new HttpError(404, "Not Found", "/api/strategies"));
    const { container } = render(<StrategyList />);
    await waitFor(() => expect(listStrategies).toHaveBeenCalled());
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("without callbacks New strategy / Duplicate / Edit are not rendered; with callbacks they fire with the row name", async () => {
    const first = render(<StrategyList />);
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: "New strategy" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Duplicate" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    first.unmount();

    const onCreate = vi.fn();
    const onDuplicate = vi.fn();
    const onEdit = vi.fn();
    await renderList({ onCreate, onDuplicate, onEdit });
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "New strategy" }));
    fireEvent.click(within(rowOf("Momentum only")).getByRole("button", { name: "Duplicate" }));
    fireEvent.click(within(rowOf("Wider stops")).getByRole("button", { name: "Edit" }));
    expect(onCreate).toHaveBeenCalledTimes(1);
    expect(onDuplicate).toHaveBeenCalledWith("Momentum only");
    expect(onEdit).toHaveBeenCalledWith("Wider stops");
  });
});

describe("deleteStrategy client (STW-4b #4864)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends DELETE to the encoded path with acknowledgment and nonce in the body", async () => {
    const { deleteStrategy: realDelete } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    const body = { success: true, aktion: "delete", name: "Wider stops", werte: {}, detail: "x" };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      json: vi.fn().mockResolvedValue(body),
    });
    expect(await realDelete("Wider stops", "ack", "n-1")).toEqual(body);
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0];
    expect(String(url)).toMatch(/\/api\/strategies\/Wider%20stops$/);
    expect((init as RequestInit).method).toBe("DELETE");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ acknowledgment: "ack", nonce: "n-1" });
  });
});
