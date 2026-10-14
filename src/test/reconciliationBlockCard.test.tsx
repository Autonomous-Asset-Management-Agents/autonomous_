// #3430 — the reconciliation block (#3389) is visible and can be released by a human.
// The card shows BOTH sides of every mismatch, gives no recommendation, and releases only on an
// explicit click. Invisible while nothing is blocked.
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const fetchReconciliationBlock = vi.fn();
const releaseReconciliationBlock = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchReconciliationBlock: () => fetchReconciliationBlock(),
  releaseReconciliationBlock: (r: string) => releaseReconciliationBlock(r),
}));

import { ReconciliationBlockCard } from "../console/desktop/ReconciliationBlockCard";

const BLOCKED = {
  available: true,
  entries_blocked: true,
  block_on_break: true,
  run_id: "lauf-1",
  finished_at: "2026-09-18T14:00:00+00:00",
  breaks: [
    {
      kind: "position_mismatch",
      symbol: "AAPL",
      order_id: "",
      broker_side: "qty=10",
      engine_side: "qty=7",
      detail: "",
    },
  ],
  last_run_id: "lauf-2",
  last_run_clean: true,
};

const RELEASED = { ...BLOCKED, entries_blocked: false, run_id: null, breaks: [] };

describe("ReconciliationBlockCard (#3430)", () => {
  beforeEach(() => {
    fetchReconciliationBlock.mockReset().mockResolvedValue(BLOCKED);
    releaseReconciliationBlock
      .mockReset()
      .mockResolvedValue({ status: "released", by: "lokaler Bediener", ...RELEASED });
  });

  it("renders nothing while entries are not blocked", async () => {
    fetchReconciliationBlock.mockResolvedValue(RELEASED);
    const { container } = render(<ReconciliationBlockCard />);
    await waitFor(() => expect(fetchReconciliationBlock).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("shows both sides of every mismatch without releasing", async () => {
    render(<ReconciliationBlockCard />);
    await screen.findByText(/new entries are blocked/i);
    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.getByText("qty=10")).toBeTruthy();
    expect(screen.getByText("qty=7")).toBeTruthy();
    expect(screen.getByText(/position mismatch/i)).toBeTruthy();
    expect(releaseReconciliationBlock).not.toHaveBeenCalled();
  });

  it("says when the latest run was clean but the block still stands", async () => {
    render(<ReconciliationBlockCard />);
    await screen.findByText(/new entries are blocked/i);
    expect(screen.getByText(/latest run found no mismatch/i)).toBeTruthy();
  });

  it("releases only on the explicit click, passing the optional reason", async () => {
    render(<ReconciliationBlockCard />);
    await screen.findByText(/new entries are blocked/i);
    fireEvent.change(screen.getByLabelText(/reason/i), {
      target: { value: "checked broker by hand" },
    });
    fireEvent.click(screen.getByRole("button", { name: /release block/i }));
    await waitFor(() =>
      expect(releaseReconciliationBlock).toHaveBeenCalledWith("checked broker by hand"),
    );
    await screen.findByText(/block released/i);
  });

  it("keeps the block visible and names the failure when the release fails", async () => {
    releaseReconciliationBlock.mockRejectedValue(new Error("500"));
    render(<ReconciliationBlockCard />);
    await screen.findByText(/new entries are blocked/i);
    fireEvent.click(screen.getByRole("button", { name: /release block/i }));
    await screen.findByText(/block is still in place/i);
    expect(screen.getByText("AAPL")).toBeTruthy();
  });
});
