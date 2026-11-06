/** STW-9c (#4918) — "Simulate" (UX-Konzept #4628, Abläufe 3 und 5; Zustände). */
import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { HttpError } from "@/lib/api";

const startEngine = vi.fn().mockResolvedValue(undefined);
vi.mock("@/lib/desktopBridge", async (orig) => ({
  ...(await orig<typeof import("@/lib/desktopBridge")>()),
  startEngine: () => startEngine(),
}));

const listStrategies = vi.fn(), startStrategySimulation = vi.fn(), getStrategySimulation = vi.fn();
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listStrategies: () => listStrategies(),
  startStrategySimulation: (...a: unknown[]) => startStrategySimulation(...a),
  getStrategySimulation: (...a: unknown[]) => getStrategySimulation(...a),
}));

import { StrategySimulationCompare } from "@/console/desktop/strategyWorkshop/StrategySimulationCompare";

const T21 = "Simulation data for this period is not on this computer yet. It is loaded once with your own Alpaca keys and can take several hours the first time.";
const T27 = "Simulated or past performance is not a reliable indicator of future results.";
const T28 = "Uncertainty: one historical path. Another period can give very different results. This is not a forecast.";
const T29 = "Not modelled: news, LLM agents, intraday stops. Costs are estimates only.";
const NO_LSTM = "This simulation runs without the LSTM: it does not vote and does not pre-select stocks. Every stock in the universe is evaluated.";

const STRATEGIES = { strategien: [{ name: "A", werte: {} }, { name: "B", werte: {} }, { name: "C", werte: {} }], max_strategien: 3 };
const RUN_ID = "0123456789abcdef";
const RESULT = {
  run_id: RUN_ID,
  status: "fertig",
  ergebnis: {
    zeitraum: { beginn: "2026-01-02", ende: "2026-06-30" },
    strategien: [
      { name: "C", twr_pct: 4.5, max_drawdown_pct: 3.0, spy_twr_pct: 6.0, spy_max_drawdown_pct: 5.0 },
      { name: "A", twr_pct: -2.0, max_drawdown_pct: 8.0, spy_twr_pct: 6.0, spy_max_drawdown_pct: 5.0 },
    ],
    unsicherheit: { pfade: 1, handelstage: 120 },
    nicht_abgebildet: [
      { art: "survivorship", text: "x" },
      { art: "einstellungen", strategie: "A", keys: ["STOP_LOSS_PCT"] },
    ],
  },
};
const RUNNING = { run_id: RUN_ID, status: "laeuft" };
const coded = (status: number, code: string, params?: Record<string, unknown>) =>
  new HttpError(status, "", "/api/strategy-sim/runs", { detail: "x", code, params });

const rows = () => within(screen.getByRole("table")).getAllByRole("row").slice(1) as HTMLTableRowElement[];
const run = () => screen.getByRole("button", { name: "Run simulation" });

async function openView() {
  const view = render(<StrategySimulationCompare />);
  fireEvent.click(await screen.findByRole("button", { name: "Simulate" }));
  return view;
}

function setPeriod(start: string, end: string) {
  fireEvent.change(screen.getByLabelText("Start"), { target: { value: start } });
  fireEvent.change(screen.getByLabelText("End"), { target: { value: end } });
}

async function runWith(names: string[]) {
  const view = await openView();
  setPeriod("2026-01-02", "2026-06-30");
  for (const n of names) fireEvent.click(screen.getByRole("checkbox", { name: n }));
  fireEvent.click(run());
  return view;
}

describe("StrategySimulationCompare (#4918)", () => {
  const fetchSpy = vi.fn();
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-28T12:00:00Z"));
    vi.stubGlobal("fetch", fetchSpy);
    listStrategies.mockResolvedValue(STRATEGIES);
    startStrategySimulation.mockResolvedValue(RUNNING);
    getStrategySimulation.mockResolvedValue(RESULT);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("#5044: a run without the LSTM vote says so under not modelled", async () => {
    const { ergebnis } = RESULT;
    getStrategySimulation.mockResolvedValue({
      ...RESULT,
      ergebnis: { ...ergebnis, nicht_abgebildet: [...ergebnis.nicht_abgebildet, { art: "ohne_lstm" }] },
    });
    await runWith(["A"]);
    expect(await screen.findByText(NO_LSTM)).toBeInTheDocument();
    expect(screen.queryByText("ohne_lstm")).toBeNull();
  });

  it("#5071: agents a strategy uses but the simulation cannot compute are named per strategy", async () => {
    const { ergebnis } = RESULT;
    const agenten = { art: "agenten", strategie: "A", agenten: ["NewsSentimentAgent", "VIXAwareRiskAgent"] };
    getStrategySimulation.mockResolvedValue({
      ...RESULT,
      ergebnis: { ...ergebnis, nicht_abgebildet: [...ergebnis.nicht_abgebildet, agenten] },
    });
    await runWith(["A"]);
    expect(await screen.findByText("A: agents not simulated — NewsSentimentAgent, VIXAwareRiskAgent")).toBeInTheDocument();
  });

  it("#5083: stocks without fundamental data are named in the result", async () => {
    const { ergebnis } = RESULT;
    const titel = { art: "ohne_fundamentaldaten", titel: ["JPM", "XOM"] };
    getStrategySimulation.mockResolvedValue({
      ...RESULT,
      ergebnis: { ...ergebnis, nicht_abgebildet: [...ergebnis.nicht_abgebildet, titel] },
    });
    await runWith(["A"]);
    expect(await screen.findByText("Without fundamental data: JPM, XOM")).toBeInTheDocument();
    expect(screen.queryByText("ohne_fundamentaldaten")).toBeNull();
  });

  it("flag off: GET /api/strategies 404 renders nothing", async () => {
    listStrategies.mockRejectedValue(new HttpError(404, "Not Found", "/api/strategies"));
    const { container } = render(<StrategySimulationCompare />);
    await waitFor(() => expect(listStrategies).toHaveBeenCalled());
    await act(async () => {});
    expect(container.innerHTML).toBe("");
  });

  it("#5166: engine down on load (fetch fails, no HTTP status) → engine-offline with Retry, not error", async () => {
    listStrategies.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<StrategySimulationCompare />);
    const hinweis = (
      await screen.findByText("Engine offline. Start the engine in Settings → System, then retry.")
    ).closest('[role="status"]');
    expect(hinweis).toHaveAttribute("data-state", "engine-offline");
    expect(screen.queryByText("Could not load the data.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: "Simulate" })).toBeInTheDocument();
  });

  it("empty: without strategies the setup says so", async () => {
    listStrategies.mockResolvedValue({ strategien: [], max_strategien: 3 });
    await openView();
    expect(screen.getByText("No strategies yet. Create one from your current settings.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run simulation" })).toBeNull();
  });

  it("A3-P1: Run simulation is disabled without a strategy or with an invalid period", async () => {
    await openView();
    setPeriod("2026-01-02", "2026-06-30");
    expect(run()).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "A" }));
    expect(run()).toBeEnabled();
    setPeriod("2026-06-30", "2026-01-02");
    expect(run()).toBeDisabled();
    setPeriod("2026-01-02", "2026-10-29");
    expect(run()).toBeDisabled();
  });

  it("A3-P2: a fourth strategy cannot be checked", async () => {
    listStrategies.mockResolvedValue({ strategien: [...STRATEGIES.strategien, { name: "D", werte: {} }], max_strategien: 3 });
    await openView();
    for (const n of ["A", "B", "C"]) fireEvent.click(screen.getByRole("checkbox", { name: n }));
    expect(screen.getByRole("checkbox", { name: "D" })).toBeDisabled();
  });

  it("A3-P3: a start before three years ago is not selectable", async () => {
    await openView();
    expect(screen.getByLabelText("Start")).toHaveAttribute("min", "2023-10-28");
    expect(screen.getByLabelText("End")).toHaveAttribute("max", "2026-10-28");
    fireEvent.click(screen.getByRole("checkbox", { name: "A" }));
    setPeriod("2023-10-27", "2026-06-30");
    expect(run()).toBeDisabled();
  });

  it("sends period, strategies in list order and capital", async () => {
    await openView();
    expect(screen.getByLabelText("Starting capital (USD)")).toHaveValue(100000);
    setPeriod("2026-01-02", "2026-06-30");
    fireEvent.click(screen.getByRole("checkbox", { name: "C" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "A" }));
    fireEvent.change(screen.getByLabelText("Starting capital (USD)"), { target: { value: "5000" } });
    fireEvent.click(run());
    await waitFor(() => expect(startStrategySimulation).toHaveBeenCalledWith({
      beginn: "2026-01-02", ende: "2026-06-30", strategien: ["A", "C"], startkapital: 5000,
    }));
    await waitFor(() => expect(getStrategySimulation).toHaveBeenCalledWith(RUN_ID));
  });

  it("polls every 1.5 s until finished, then stops", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(new Date("2026-10-28T12:00:00Z"));
    getStrategySimulation.mockResolvedValueOnce(RUNNING).mockResolvedValueOnce(RESULT);
    render(<StrategySimulationCompare />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Simulate" }));
    setPeriod("2026-01-02", "2026-06-30");
    fireEvent.click(screen.getByRole("checkbox", { name: "A" }));
    fireEvent.click(run());
    await act(async () => {});
    expect(getStrategySimulation).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(getStrategySimulation).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("table")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(getStrategySimulation).toHaveBeenCalledTimes(2);
  });

  it("stops polling on unmount", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(new Date("2026-10-28T12:00:00Z"));
    getStrategySimulation.mockResolvedValue(RUNNING);
    const view = render(<StrategySimulationCompare />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Simulate" }));
    setPeriod("2026-01-02", "2026-06-30");
    fireEvent.click(screen.getByRole("checkbox", { name: "A" }));
    fireEvent.click(run());
    await act(async () => {});
    expect(getStrategySimulation).toHaveBeenCalledTimes(1);
    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(getStrategySimulation).toHaveBeenCalledTimes(1);
  });

  it("shows TWR, Max drawdown and SPY per strategy", async () => {
    await runWith(["A", "C"]);
    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["Strategy", "TWR", "Max drawdown", "SPY (same period)"]);
    expect(rows().map((r) => [...r.cells].map((c) => c.textContent))).toEqual([
      ["C", "+4.50 %", "−3.00 %", "+6.00 %"],
      ["A", "−2.00 %", "−8.00 %", "+6.00 %"],
    ]);
    expect(screen.getByText("Results stay on this computer.")).toBeInTheDocument();
  });

  it("A5-P1: mandatory notice, period, uncertainty and not-modelled parts above the table", async () => {
    await runWith(["A", "C"]);
    const table = await screen.findByRole("table");
    for (const t of [T27, "Period: 2026-01-02 – 2026-06-30", T28, T29, "A: settings not simulated — STOP_LOSS_PCT"]) {
      const el = screen.getByText(t);
      expect(el.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING, t).toBeTruthy();
    }
    expect(screen.queryByRole("button", { name: /show|hide|collapse/i })).toBeNull();
  });

  it("A5-P2/P3: rows in send order, sorting only on click, aria-sort follows; restore creation order", async () => {
    await runWith(["A", "C"]);
    await screen.findByRole("table");
    expect(rows().map((r) => r.cells[0].textContent)).toEqual(["C", "A"]);
    const twr = screen.getByRole("button", { name: "TWR" });
    expect(twr.closest("th")).toHaveAttribute("aria-sort", "none");
    fireEvent.click(twr);
    expect(rows().map((r) => r.cells[0].textContent)).toEqual(["A", "C"]);
    expect(twr.closest("th")).toHaveAttribute("aria-sort", "ascending");
    fireEvent.click(twr);
    expect(twr.closest("th")).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(screen.getByRole("button", { name: "Restore creation order" }));
    expect(rows().map((r) => r.cells[0].textContent)).toEqual(["C", "A"]);
    expect(twr.closest("th")).toHaveAttribute("aria-sort", "none");
  });

  it("A5-P5 / Change setup: no adopt control, back to the setup", async () => {
    await runWith(["A"]);
    await screen.findByRole("table");
    for (const b of screen.getAllByRole("button")) expect(b.textContent).not.toMatch(/assign|adopt|apply|\buse\b/i);
    fireEvent.click(screen.getByRole("button", { name: "Change setup" }));
    expect(run()).toBeInTheDocument();
  });

  it("409 keine_keys → no-keys", async () => {
    startStrategySimulation.mockRejectedValue(coded(409, "simulation_keys_missing", { reason: "keine_keys" }));
    await runWith(["A"]);
    expect(await screen.findByText("No Alpaca keys for this account. Simulation data is loaded with your own keys.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("409 kein_korpus → T21 notice, not a result", async () => {
    startStrategySimulation.mockRejectedValue(coded(409, "simulation_corpus_missing", { reason: "kein_korpus" }));
    await runWith(["A"]);
    expect(await screen.findByText(T21)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button", { name: "Prepare data" })).toBeNull();
  });

  it("403 → edition-limit, Shorten period sets start to end minus 12 months", async () => {
    startStrategySimulation.mockRejectedValue(coded(403, "simulation_edition_limit", { months: 12 }));
    await runWith(["A"]);
    expect(await screen.findByText("Your edition allows simulation periods of up to 12 months.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Shorten period" }));
    expect(screen.getByLabelText("Start")).toHaveValue("2025-06-30");
    expect(screen.getByLabelText("End")).toHaveValue("2026-06-30");
  });

  it("network error → engine-off", async () => {
    startStrategySimulation.mockRejectedValue(new TypeError("Failed to fetch"));
    await runWith(["A"]);
    expect(await screen.findByText("The engine for this account is not running.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start engine" }));
    expect(startEngine).toHaveBeenCalled();
  });

  it("status fehler → error with Retry", async () => {
    getStrategySimulation.mockResolvedValue({ run_id: RUN_ID, status: "fehler" });
    await runWith(["A"]);
    expect(await screen.findByText("Could not load the data.")).toBeInTheDocument();
    getStrategySimulation.mockResolvedValue(RESULT);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("calls no API besides the two simulation functions and listStrategies", async () => {
    await runWith(["A", "C"]);
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "TWR" }));
    expect(listStrategies).toHaveBeenCalledTimes(1);
    expect(startStrategySimulation).toHaveBeenCalledTimes(1);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  describe("#5126: delivery state (T20, E2)", () => {
    const T20 = "Delivery state (no strategy)";
    const DELIVERY = "@delivery-state";

    it("its checkbox stands after the strategies, unchecked, and is never locked by the limit", async () => {
      await openView();
      const boxes = screen.getAllByRole("checkbox");
      expect(boxes.at(-1)).toBe(screen.getByRole("checkbox", { name: T20 }));
      expect((boxes.at(-1) as HTMLInputElement).checked).toBe(false);
      for (const n of ["A", "B", "C"]) fireEvent.click(screen.getByRole("checkbox", { name: n }));
      expect(screen.getByRole("checkbox", { name: T20 })).not.toBeDisabled();
    });

    it("sends the own strategies in creation order, then the delivery state", async () => {
      await openView();
      setPeriod("2026-01-02", "2026-06-30");
      for (const n of [T20, "C", "A", "B"]) fireEvent.click(screen.getByRole("checkbox", { name: n }));
      fireEvent.click(run());
      await waitFor(() => expect(startStrategySimulation).toHaveBeenCalledTimes(1));
      expect(startStrategySimulation.mock.calls[0][0].strategien).toEqual(["A", "B", "C", DELIVERY]);
    });

    it("with only T20 checked Run simulation stays disabled", async () => {
      await openView();
      setPeriod("2026-01-02", "2026-06-30");
      fireEvent.click(screen.getByRole("checkbox", { name: T20 }));
      expect(run()).toBeDisabled();
    });

    it("the result row and its agents note show T20, not the identifier", async () => {
      const { ergebnis } = RESULT;
      getStrategySimulation.mockResolvedValue({
        ...RESULT,
        ergebnis: {
          ...ergebnis,
          strategien: [...ergebnis.strategien, { ...ergebnis.strategien[0], name: DELIVERY }],
          nicht_abgebildet: [
            ...ergebnis.nicht_abgebildet,
            { art: "agenten", strategie: DELIVERY, agenten: ["NewsSentimentAgent"] },
          ],
        },
      });
      await runWith(["A", T20]);
      await screen.findByRole("table");
      expect(rows().at(-1)!.cells[0].textContent).toBe(T20);
      expect(screen.getByText(`${T20}: agents not simulated — NewsSentimentAgent`)).toBeInTheDocument();
      expect(document.body.textContent).not.toContain(DELIVERY);
    });
  });
});
