/** STW-7 (#4633) — "Compare account performance" (UX-Konzept #4628, Ablauf 6, mock-up #compare). */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { HttpError } from "@/lib/api";
import type { FleetEngine } from "@/lib/desktopBridge";

const getEngineFleet = vi.fn();
vi.mock("@/lib/desktopBridge", async (orig) => ({ ...(await orig<typeof import("@/lib/desktopBridge")>()), getEngineFleet: () => getEngineFleet() }));

const listStrategies = vi.fn(), listStrategyAssignments = vi.fn(), fetchBenchmarkEquityAt = vi.fn();
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listStrategies: () => listStrategies(),
  listStrategyAssignments: () => listStrategyAssignments(),
  fetchBenchmarkEquityAt: (...a: unknown[]) => fetchBenchmarkEquityAt(...a),
}));

import { AccountComparison } from "@/console/desktop/strategyWorkshop/AccountComparison";

const FLEET: FleetEngine[] = [
  { id: "live", kind: "live", port: 8001, status: "running" },
  { id: "p1", kind: "paper", port: 8002, status: "running" },
  { id: "p2", kind: "paper", port: 8003, status: "running" },
  { id: "p3", kind: "paper", port: 8004, status: "running" },
];

const series = (start: string, days: number, dailyPct: number, base = 1000) => {
  const t0 = Date.parse(`${start}T00:00:00Z`);
  return Array.from({ length: days }, (_, i) => ({
    date: new Date(t0 + i * 86_400_000).toISOString().slice(0, 10),
    equity: base * (1 + dailyPct / 100) ** i,
  }));
};

const RESPONSES: Record<number, unknown> = {
  8001: { points: series("2026-04-01", 200, 0.1), spy_points: series("2026-04-01", 200, 0.05, 1000) },
  8002: { points: series("2026-04-01", 200, 0.2), spy_points: series("2026-04-01", 200, 0.05, 1000) },
  8003: { points: series("2026-10-08", 10, -0.1), spy_points: series("2026-10-08", 10, 0.05, 1000) },
  8004: { points: series("2026-04-01", 200, 0.05), spy_points: series("2026-04-01", 200, 0.05, 1000) },
};

const rows = () => within(screen.getByRole("table")).getAllByRole("row").slice(1) as HTMLTableRowElement[];

async function openView(onAdjust?: (id: string) => void) {
  render(<AccountComparison onAdjust={onAdjust ? (a) => onAdjust(a.id) : undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "Compare account performance" }));
}

describe("AccountComparison (#4633)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(console, "warn").mockImplementation(() => {});
    listStrategies.mockResolvedValue({ strategien: [], max_strategien: 3 });
    listStrategyAssignments.mockResolvedValue([]);
    getEngineFleet.mockResolvedValue(FLEET);
    fetchBenchmarkEquityAt.mockImplementation(async (port: number) => RESPONSES[port]);
  });

  it("A6-P1: live on top, then the paper accounts in slot order", async () => {
    getEngineFleet.mockResolvedValue([FLEET[2], FLEET[0], FLEET[1], FLEET[3]]);
    await openView();
    await waitFor(() => expect(rows()).toHaveLength(4));
    expect(rows().map((r) => r.cells[0].textContent)).toEqual(["Live", "Paper account 1", "Paper account 2", "Paper account 3"]);
  });

  describe("column Strategy (#4953)", () => {
    const SLOTTED: FleetEngine[] = [FLEET[0], { ...FLEET[1], id: "paper1" }, { ...FLEET[3], id: "paper3" }];
    const zuweisung = (konto: string, strategie: string) => ({ konto, strategie, strategie_hash: "sha256:x", timestamp: "2026-10-30T10:00:00+00:00" });

    it("shows the assignment for live and paper, — without a match", async () => {
      getEngineFleet.mockResolvedValue(SLOTTED);
      listStrategyAssignments.mockResolvedValue([zuweisung("live", "A"), zuweisung("paper1", "B"), zuweisung("paper2", "C")]);
      await openView();
      await waitFor(() => expect(rows()[0].cells[1].textContent).toBe("A"));
      expect(rows().map((r) => r.cells[1].textContent)).toEqual(["A", "B", "—"]);
    });

    it("unreadable assignments leave —, the figures stay", async () => {
      listStrategyAssignments.mockRejectedValue(new Error("503"));
      await openView();
      await waitFor(() => expect(rows()[3].textContent).toMatch(/%/));
      expect(rows().map((r) => r.cells[1].textContent)).toEqual(["—", "—", "—", "—"]);
    });

    it("paper2 without paper1 is called Paper account 2, like on the slot card", async () => {
      getEngineFleet.mockResolvedValue([FLEET[0], { ...FLEET[2], id: "paper2" }]);
      await openView();
      await waitFor(() => expect(rows()).toHaveLength(2));
      expect(rows()[1].cells[0].textContent).toBe("Paper account 2");
    });
  });

  it("A6-P2: period, origin and the mandatory note stand above the table — one period for all", async () => {
    await openView();
    await screen.findByText(/Period: 2026-10-08 – 2026-10-17/);
    const note = screen.getByText("Simulated or past performance is not a reliable indicator of future results.");
    const origin = screen.getByText("Cash-flow adjusted, from account activities.");
    const table = screen.getByRole("table");
    for (const el of [note, origin, screen.getByText(/Period:/)]) {
      expect(el.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(screen.getAllByText(/Period:/)).toHaveLength(1);
  });

  it("shows TWR, max drawdown and SPY per account for the common period", async () => {
    await openView();
    await waitFor(() => expect(rows()[0].textContent).toMatch(/%/));
    const live = rows()[0];
    expect(live.cells[2].textContent).toBe(`+${((1.001 ** 9 - 1) * 100).toFixed(2)} %`);
    expect(live.cells[3].textContent).toBe("0.00 %");
    expect(live.cells[4].textContent).toBe(`+${((1.0005 ** 9 - 1) * 100).toFixed(2)} %`);
    const paper2 = rows()[2];
    expect(paper2.cells[2].textContent).toBe(`−${((1 - 0.999 ** 9) * 100).toFixed(2)} %`);
    expect(paper2.cells[3].textContent).toBe(`−${((1 - 0.999 ** 9) * 100).toFixed(2)} %`);
  });

  it("A6-P3: an unreachable account shows Unavailable, the other rows stay", async () => {
    fetchBenchmarkEquityAt.mockImplementation(async (port: number) =>
      port === 8004 ? { points: [], spy_points: [], message: "fetch_failed" } : RESPONSES[port],
    );
    await openView();
    await waitFor(() => expect(rows()[3].textContent).toContain("Unavailable"));
    expect(rows()[0].textContent).toMatch(/%/);
    expect(rows()[1].textContent).toMatch(/%/);
    expect(rows()[2].textContent).toMatch(/%/);
    expect(screen.getByText(/Period: 2026-10-08 – 2026-10-17/)).toBeInTheDocument();
  });

  it("A6-P4: without a paper account the view is empty", async () => {
    getEngineFleet.mockResolvedValue([FLEET[0]]);
    await openView();
    expect(await screen.findByText("No paper account connected.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("A6-P4 (#4934): the empty state offers Connect paper account only on the desktop", async () => {
    getEngineFleet.mockResolvedValue([FLEET[0]]);
    await openView();
    await screen.findByText("No paper account connected.");
    expect(screen.queryByRole("button", { name: "Connect paper account" })).toBeNull();
  });

  it("A6-P4 (#4934): a desktop shell without the paper-slot bridge (before STW-6b) shows no action", async () => {
    (window as unknown as { aaagents: unknown }).aaagents = { isDesktop: true };
    try {
      getEngineFleet.mockResolvedValue([FLEET[0]]);
      await openView();
      await screen.findByText("No paper account connected.");
      expect(screen.queryByRole("button", { name: "Connect paper account" })).toBeNull();
    } finally {
      delete (window as unknown as { aaagents?: unknown }).aaagents;
    }
  });

  it("A6-P4 (#4934): Connect paper account scrolls #paper-accounts into view", async () => {
    (window as unknown as { aaagents: unknown }).aaagents = { isDesktop: true, listPaperSlots: vi.fn() };
    const target = document.createElement("section");
    target.id = "paper-accounts";
    target.scrollIntoView = vi.fn();
    document.body.appendChild(target);
    try {
      getEngineFleet.mockResolvedValue([FLEET[0]]);
      await openView();
      fireEvent.click(await screen.findByRole("button", { name: "Connect paper account" }));
      expect(target.scrollIntoView).toHaveBeenCalledTimes(1);
    } finally {
      target.remove();
      delete (window as unknown as { aaagents?: unknown }).aaagents;
    }
  });

  it("A6-P4: a shell without a fleet is empty too", async () => {
    getEngineFleet.mockResolvedValue(null);
    await openView();
    expect(await screen.findByText("No paper account connected.")).toBeInTheDocument();
  });

  it("loading: rows read Loading… until the engines answer", async () => {
    fetchBenchmarkEquityAt.mockImplementation(() => new Promise(() => {}));
    await openView();
    await waitFor(() => expect(rows()).toHaveLength(4));
    for (const r of rows()) expect(r.textContent).toContain("Loading…");
  });

  it("no common period: the note replaces the period, no figures are shown", async () => {
    fetchBenchmarkEquityAt.mockImplementation(async (port: number) =>
      port === 8003 ? { points: series("2026-11-20", 5, 0.1), spy_points: [] } : RESPONSES[port],
    );
    await openView();
    expect(await screen.findByText("These accounts have no common period yet.")).toBeInTheDocument();
    expect(screen.queryByText(/Period:/)).toBeNull();
    expect(rows().some((r) => /%/.test(r.textContent ?? ""))).toBe(false);
  });

  it("error: an unreadable flag shows Could not load the data. with Retry", async () => {
    listStrategies.mockRejectedValueOnce(new HttpError(500, "x", "/api/strategies"));
    render(<AccountComparison />);
    expect(await screen.findByText("Could not load the data.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: "Compare account performance" })).toBeInTheDocument();
  });

  it("#5166: engine down (fetch fails, no HTTP status) → engine-offline with Retry, no fleet polled", async () => {
    listStrategies.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<AccountComparison />);
    const hinweis = (
      await screen.findByText("Engine offline. Start the engine in Settings → System, then retry.")
    ).closest('[role="status"]');
    expect(hinweis).toHaveAttribute("data-state", "engine-offline");
    expect(screen.queryByText("Could not load the data.")).toBeNull();
    expect(getEngineFleet).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: "Compare account performance" })).toBeInTheDocument();
  });

  it("sorts only on click; Restore creation order brings the slot order back", async () => {
    await openView();
    await waitFor(() => expect(rows()[3].textContent).toMatch(/%/));
    const order = () => rows().map((r) => r.cells[0].textContent);
    const slot = ["Live", "Paper account 1", "Paper account 2", "Paper account 3"];
    expect(order()).toEqual(slot);
    const twrHead = screen.getByRole("columnheader", { name: "TWR" });
    expect(twrHead).toHaveAttribute("aria-sort", "none");
    fireEvent.click(within(twrHead).getByRole("button", { name: "TWR" }));
    expect(twrHead).toHaveAttribute("aria-sort", "ascending");
    expect(order()).toEqual(["Paper account 2", "Paper account 3", "Live", "Paper account 1"]);
    fireEvent.click(within(twrHead).getByRole("button", { name: "TWR" }));
    expect(twrHead).toHaveAttribute("aria-sort", "descending");
    expect(order()).toEqual(["Paper account 1", "Live", "Paper account 3", "Paper account 2"]);
    fireEvent.click(screen.getByRole("button", { name: "Restore creation order" }));
    expect(twrHead).toHaveAttribute("aria-sort", "none");
    expect(order()).toEqual(slot);
  });

  it("Adjust in Settings only on paper rows, for that paper account", async () => {
    const onAdjust = vi.fn();
    await openView(onAdjust);
    await waitFor(() => expect(rows()).toHaveLength(4));
    expect(within(rows()[0]).queryByText("Adjust in Settings")).toBeNull();
    for (const r of rows().slice(1)) expect(within(r).getByText("Adjust in Settings")).toBeInTheDocument();
    fireEvent.click(within(rows()[2]).getByText("Adjust in Settings"));
    expect(onAdjust).toHaveBeenCalledWith("p2");
  });

  it("A6-P5: no button or link leads to the live assignment", async () => {
    await openView(() => {});
    await waitFor(() => expect(rows()[3].textContent).toMatch(/%/));
    for (const c of [...screen.queryAllByRole("button"), ...screen.queryAllByRole("link")]) {
      expect(c.textContent ?? "").not.toMatch(/assign|live account strategy|review changes/i);
    }
  });

  it("flag off: nothing renders and no engine is polled", async () => {
    listStrategies.mockRejectedValue(new HttpError(404, "Not Found", "/api/strategies"));
    const { container } = render(<AccountComparison />);
    await waitFor(() => expect(listStrategies).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
    expect(getEngineFleet).not.toHaveBeenCalled();
    expect(fetchBenchmarkEquityAt).not.toHaveBeenCalled();
  });

  it("entry (#5166 §2.3): Settings → Trading → Strategies, last card, after Simulate", async () => {
    const { default: src } = await import("@/console/desktop/pages/Settings.tsx?raw");
    const sim = src.indexOf("<StrategySimulationCompare />"), compare = src.indexOf("<AccountComparison />"), assign = src.indexOf("<LiveStrategyAssignment />");
    expect(sim).toBeGreaterThan(assign);
    expect(compare).toBeGreaterThan(sim);
  });

  it("#5166: the title says what it is, the subtitle says the results are real", async () => {
    render(<AccountComparison />);
    await screen.findByRole("button", { name: "Compare account performance" });
    expect(
      screen.getByText("Real results of your live and paper accounts over one period, side by side. Nothing is simulated."),
    ).toBeInTheDocument();
  });

  it("#5166: engine down (fetch fails, no HTTP status) → engine-offline with Retry, no fleet polled", async () => {
    listStrategies.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<AccountComparison />);
    const hinweis = (
      await screen.findByText("Engine offline. Start the engine in Settings → System, then retry.")
    ).closest('[role="status"]');
    expect(hinweis).toHaveAttribute("data-state", "engine-offline");
    expect(screen.queryByText("Could not load the data.")).toBeNull();
    expect(getEngineFleet).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: "Compare account performance" })).toBeInTheDocument();
  });

  it("closed section polls nothing", async () => {
    render(<AccountComparison />);
    await screen.findByRole("button", { name: "Compare account performance" });
    expect(fetchBenchmarkEquityAt).not.toHaveBeenCalled();
  });
});
