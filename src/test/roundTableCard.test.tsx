import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";

/**
 * UXC-1 S4 (#3157, Epic #3151) — "Round table" card (Settings → Trading).
 *
 * Rev. 3 (owner decision 2026-09-02, supersedes plan A1/A2): the card mirrors the
 * decision pipeline in three stages (security selection → risk overlay → position
 * sizing), lists the FULL server-delivered roster (incl. dormant Fundamentals/
 * Valuation/RL rows), and EVERY agent with an enable flag is toggleable — including
 * the Drawdown Guard pre-trade veto and the Regime overlay. Role designations
 * (`role_title`) use industry terminology and come from the S2 GET, never the UI.
 * Everything else holds from the approved plan: rows dynamic from the GET, slider
 * bounds from the GET, pending-until-WORM-apply semantics, neutral English copy.
 */

vi.mock("../lib/api", () => ({
  getTradingSettings: vi.fn(),
  postTradingSettings: vi.fn(),
}));

import { getTradingSettings, postTradingSettings } from "../lib/api";
import { RoundTableCard } from "../console/desktop/RoundTableCard";

const mockGet = getTradingSettings as unknown as ReturnType<typeof vi.fn>;

const AGENTS = [
  { name: "MomentumAgent", enabled: true, default_enabled: true, weight: 0.45, default_weight: 0.45, min_weight: 0.0, max_weight: 1.5, role: "mean_voter", role_title: "Momentum Strategist", enable_flag: "MOMENTUM_AGENT_ENABLED", weight_key: "MOMENTUM_AGENT_WEIGHT" },
  { name: "LSTMSignalAgent", enabled: true, default_enabled: true, weight: 0.4, default_weight: 0.4, min_weight: 0.15, max_weight: 1.5, role: "mean_voter", role_title: "Quantitative Analyst", enable_flag: "LSTM_SIGNAL_AGENT_ENABLED", weight_key: "LSTM_SIGNAL_WEIGHT" },
  { name: "NewsSentimentAgent", enabled: true, default_enabled: true, weight: 0.35, default_weight: 0.35, min_weight: 0.1, max_weight: 1.5, role: "mean_voter", role_title: "Sentiment Analyst", enable_flag: "NEWS_SENTIMENT_AGENT_ENABLED", weight_key: "NEWS_SENTIMENT_WEIGHT" },
  { name: "UpsideSkewAgent", enabled: false, default_enabled: false, weight: 0.3, default_weight: 0.3, min_weight: 0.1, max_weight: 1.5, role: "mean_voter", role_title: "Risk-Reward Analyst", enable_flag: "UPSIDE_SKEW_AGENT_ENABLED", weight_key: "UPSIDE_SKEW_WEIGHT" },
  { name: "FundamentalsAgent", enabled: true, default_enabled: true, weight: 0.0, default_weight: 0.0, min_weight: 0.0, max_weight: 1.5, role: "mean_voter", role_title: "Fundamental Analyst", enable_flag: "FUNDAMENTALS_AGENT_ENABLED", weight_key: "FUNDAMENTALS_WEIGHT" },
  { name: "VIXAwareRiskAgent", enabled: true, default_enabled: true, weight: 0.45, default_weight: 0.45, min_weight: 0.1, max_weight: 1.5, role: "size_input", role_title: "Volatility Strategist", enable_flag: "VIX_RISK_AGENT_ENABLED", weight_key: "VIX_RISK_WEIGHT" },
  { name: "DrawdownGuardAgent", enabled: true, default_enabled: true, weight: 0.0, default_weight: 0.0, min_weight: 0.0, max_weight: 0.0, role: "veto_guard", role_title: "Risk Manager", enable_flag: "DRAWDOWN_GUARD_AGENT_ENABLED", weight_key: "" },
  { name: "RegimeDetectionAgent", enabled: true, default_enabled: true, weight: 0.0, default_weight: 0.0, min_weight: 0.0, max_weight: 0.0, role: "conditioner", role_title: "Macro Strategist", enable_flag: "REGIME_DETECTION_AGENT_ENABLED", weight_key: "" },
] as const;

const payload = (overrides: Partial<Record<string, unknown>> = {}) => ({
  agents: AGENTS.map((a) => ({ ...a })),
  implied_vol_forecast_enabled: true,
  ...overrides,
});

const saveSetupMock = vi.fn().mockResolvedValue(undefined);
const restartMock = vi.fn().mockResolvedValue(undefined);
const setBridge = () => {
  (window as unknown as { aaagents?: unknown }).aaagents = {
    isDesktop: true,
    saveSetupState: (...a: unknown[]) => saveSetupMock(...a),
    restartEngine: (...a: unknown[]) => restartMock(...a),
  };
};

describe("Round table card (UXC-1 S4)", () => {
  beforeEach(() => {
    setBridge();
    mockGet.mockResolvedValue(payload());
  });
  afterEach(() => {
    (window as unknown as { aaagents?: unknown }).aaagents = undefined;
    vi.clearAllMocks();
  });

  it("T1: renders one row per agent from the engine GET — an extra voter yields an extra row", async () => {
    render(<RoundTableCard />);
    await waitFor(() => screen.getByText("Round table"));
    expect(screen.getAllByText(/Momentum/).length).toBeGreaterThan(0);
    expect(screen.getByText(/LSTM/)).toBeTruthy();

    // extra voter in the mock → extra row (no hardcoding)
    mockGet.mockResolvedValue(
      payload({
        agents: [
          ...AGENTS.map((a) => ({ ...a })),
          { name: "ValuationAgent", enabled: true, default_enabled: true, weight: 0.2, default_weight: 0.2, min_weight: 0.0, max_weight: 1.5, role: "mean_voter", role_title: "Valuation Analyst", enable_flag: "VALUATION_AGENT_ENABLED", weight_key: "VALUATION_WEIGHT" },
        ],
      }),
    );
    const { unmount } = render(<RoundTableCard />);
    await waitFor(() => expect(screen.getAllByText(/Valuation/).length).toBeGreaterThan(0));
    unmount();
  });

  it("T2: slider bounds per row come from the GET (Momentum down to 0, LSTM not below 0.15)", async () => {
    render(<RoundTableCard />);
    const momentum = await waitFor(
      () => screen.getByLabelText("MomentumAgent weight") as HTMLInputElement,
    );
    expect(momentum.min).toBe("0");
    const lstm = screen.getByLabelText("LSTMSignalAgent weight") as HTMLInputElement;
    expect(lstm.min).toBe("0.15");
    expect(lstm.max).toBe("1.5");
  });

  it("T3: consensus share = weight / sum of ACTIVE voters; a disabled voter shows a dash and the rest renormalize", async () => {
    // active mean voters (IV flag on → VIXAware is size_input; UpsideSkew off):
    // Momentum 0.45, LSTM 0.40, News 0.35, Fundamentals 0.00 → sum 1.20 → Momentum 37.5%
    render(<RoundTableCard />);
    await waitFor(() => screen.getByText("37.5%"));

    // disable News via its On/Off toggle → sum 0.85 → Momentum 52.9%, News row shows —
    fireEvent.click(
      within(screen.getByRole("group", { name: "NewsSentimentAgent enabled" })).getByRole(
        "button",
        { name: "Off" },
      ),
    );
    await waitFor(() => screen.getByText("52.9%"));
    const newsRow = screen.getByTestId("agent-row-NewsSentimentAgent");
    expect(newsRow.textContent).toContain("—");
  });

  it("T4: VIXAware row follows the IV flag — sizing-input role without share when on, normal voter row when off", async () => {
    render(<RoundTableCard />);
    const row = await waitFor(() => screen.getByTestId("agent-row-VIXAwareRiskAgent"));
    expect(row.textContent).toMatch(/sizing input/i);

    mockGet.mockResolvedValue(
      payload({
        implied_vol_forecast_enabled: false,
        agents: AGENTS.map((a) =>
          a.name === "VIXAwareRiskAgent" ? { ...a, role: "mean_voter" } : { ...a },
        ),
      }),
    );
    const second = render(<RoundTableCard />);
    await waitFor(() => {
      const rows = screen.getAllByTestId("agent-row-VIXAwareRiskAgent");
      const fresh = rows[rows.length - 1];
      expect(fresh.textContent).not.toMatch(/sizing input/i);
    });
    second.unmount();
  });

  it("T5: overlay agents are toggleable with role badges — On/Off toggle present, but no weight slider", async () => {
    render(<RoundTableCard />);
    const ddg = await waitFor(() => screen.getByTestId("agent-row-DrawdownGuardAgent"));
    expect(ddg.textContent).toMatch(/pre-trade veto/i);
    const ddgToggle = screen.getByRole("group", { name: "DrawdownGuardAgent enabled" });
    expect(
      within(ddgToggle).getByRole("button", { name: "On" }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(screen.queryByLabelText("DrawdownGuardAgent weight")).toBeNull();

    const regime = screen.getByTestId("agent-row-RegimeDetectionAgent");
    expect(regime.textContent).toMatch(/regime overlay/i);
    const regimeToggle = screen.getByRole("group", { name: "RegimeDetectionAgent enabled" });
    expect(
      within(regimeToggle).getByRole("button", { name: "On" }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(screen.queryByLabelText("RegimeDetectionAgent weight")).toBeNull();
  });

  it("T6: a slider change never writes immediately — pending + Changed badge, Apply hands the pending map to the S3 seam", async () => {
    const onApply = vi.fn();
    render(<RoundTableCard onApply={onApply} />);
    const momentum = await waitFor(
      () => screen.getByLabelText("MomentumAgent weight") as HTMLInputElement,
    );
    fireEvent.change(momentum, { target: { value: "0.6" } });

    // no immediate persistence of any kind
    expect(onApply).not.toHaveBeenCalled();
    await waitFor(() => screen.getByText("Changed"));

    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({ MOMENTUM_AGENT_WEIGHT: "0.6" }),
    );
  });

  it("T6b: disabling the Drawdown Guard stages the veto flag as pending — no immediate write", async () => {
    const onApply = vi.fn();
    render(<RoundTableCard onApply={onApply} />);
    const ddgToggle = await waitFor(() =>
      screen.getByRole("group", { name: "DrawdownGuardAgent enabled" }),
    );
    fireEvent.click(within(ddgToggle).getByRole("button", { name: "Off" }));

    expect(onApply).not.toHaveBeenCalled();
    await waitFor(() => screen.getByText("Changed"));

    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({ DRAWDOWN_GUARD_AGENT_ENABLED: "false" }),
    );
  });

  it("T7: Default settings resets pending values to the GET defaults — badges gone, nothing written", async () => {
    const onApply = vi.fn();
    render(<RoundTableCard onApply={onApply} />);
    const momentum = await waitFor(
      () => screen.getByLabelText("MomentumAgent weight") as HTMLInputElement,
    );
    fireEvent.change(momentum, { target: { value: "0.6" } });
    await waitFor(() => screen.getByText("Changed"));

    fireEvent.click(screen.getByRole("button", { name: /default settings/i }));
    await waitFor(() => expect(screen.queryByText("Changed")).toBeNull());
    expect((screen.getByLabelText("MomentumAgent weight") as HTMLInputElement).value).toBe("0.45");
    expect(onApply).not.toHaveBeenCalled();
  });

  it("T8: neutrality — descriptions carry no profile/advice wording; percent only in value cells", async () => {
    const { container } = render(<RoundTableCard />);
    await waitFor(() => screen.getByText("Round table"));
    const banned = [/profil/i, /empfohlen/i, /recommend/i, /aggressiv/i, /kapitalerhalt/i, /sharpe/i, /outperform/i, /guarantee/i, /drawdown/i];
    const prose = Array.from(
      container.querySelectorAll("[data-copy='description'], p"),
    )
      .map((n) => n.textContent ?? "")
      .join(" ");
    for (const re of banned) expect(prose).not.toMatch(re);
    // percent is allowed in value cells, banned in prose:
    expect(prose).not.toMatch(/%/);
  });

  it("T9: engine offline → neutral error state, no fabricated values", async () => {
    mockGet.mockRejectedValue(new Error("engine offline"));
    render(<RoundTableCard />);
    await waitFor(() => screen.getByText(/not available|offline/i));
    expect(screen.queryByLabelText("MomentumAgent weight")).toBeNull();
  });

  it("T10: the three pipeline stages render in engine-true order and group the agents by role", async () => {
    const { container } = render(<RoundTableCard />);
    await waitFor(() => screen.getByTestId("stage-selection"));

    const selection = screen.getByTestId("stage-selection");
    const overlay = screen.getByTestId("stage-overlay");
    const sizing = screen.getByTestId("stage-sizing");

    // order in the DOM: selection before overlay before sizing
    const order = Array.from(
      container.querySelectorAll("[data-testid^='stage-']"),
    ).map((n) => n.getAttribute("data-testid"));
    expect(order).toEqual(["stage-selection", "stage-overlay", "stage-sizing"]);

    expect(within(selection).getByText("Security selection")).toBeTruthy();
    expect(within(overlay).getByText("Risk overlay")).toBeTruthy();
    expect(within(sizing).getByText("Position sizing")).toBeTruthy();

    expect(within(selection).getByTestId("agent-row-MomentumAgent")).toBeTruthy();
    expect(within(overlay).getByTestId("agent-row-DrawdownGuardAgent")).toBeTruthy();
    expect(within(overlay).getByTestId("agent-row-RegimeDetectionAgent")).toBeTruthy();
    expect(within(sizing).getByTestId("agent-row-VIXAwareRiskAgent")).toBeTruthy();
  });

  it("T12: engine-inactive agents (disabled or weight 0) are collapsed behind a counter toggle", async () => {
    render(<RoundTableCard />);
    await waitFor(() => screen.getByTestId("stage-selection"));

    // UpsideSkew (disabled) and Fundamentals (weight 0) are hidden by default
    expect(screen.queryByTestId("agent-row-UpsideSkewAgent")).toBeNull();
    expect(screen.queryByTestId("agent-row-FundamentalsAgent")).toBeNull();
    // overlay rows are NEVER classified inactive by weight (their weights are inert)
    expect(screen.getByTestId("agent-row-DrawdownGuardAgent")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /show inactive agents \(2\)/i }));
    await waitFor(() => screen.getByTestId("agent-row-UpsideSkewAgent"));
    expect(screen.getByTestId("agent-row-FundamentalsAgent")).toBeTruthy();
    expect(screen.getByRole("button", { name: /hide inactive agents/i })).toBeTruthy();
  });

  it("T13: a visible Pending-changes list shows each staged change old to new and empties on reset", async () => {
    render(<RoundTableCard />);
    const momentum = await waitFor(
      () => screen.getByLabelText("MomentumAgent weight") as HTMLInputElement,
    );
    expect(screen.queryByText("Pending changes")).toBeNull();

    fireEvent.change(momentum, { target: { value: "0.6" } });
    await waitFor(() => screen.getByText("Pending changes"));
    const list = screen.getByTestId("pending-changes");
    expect(list.textContent).toMatch(/Momentum/);
    expect(list.textContent).toContain("0.45");
    expect(list.textContent).toContain("0.6");

    fireEvent.click(screen.getByRole("button", { name: /default settings/i }));
    await waitFor(() => expect(screen.queryByText("Pending changes")).toBeNull());
  });

  it("T11: role designations from the GET are shown per row (industry terminology, server-authoritative)", async () => {
    render(<RoundTableCard />);
    await waitFor(() => screen.getByText("Momentum Strategist"));
    expect(screen.getByText("Risk Manager")).toBeTruthy();
    expect(screen.getByText("Macro Strategist")).toBeTruthy();
    expect(screen.getByText("Volatility Strategist")).toBeTruthy();
  });

  it("T14: WITHOUT an onApply prop, Apply runs the REAL save path - modal, S2 POST, setup.json, restart", async () => {
    (postTradingSettings as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      applied: {},
      changes: [{ key: "MOMENTUM_AGENT_WEIGHT", from: "0.45", to: "0.6" }],
      detail: "recorded",
    });
    render(<RoundTableCard />);
    const momentum = await waitFor(
      () => screen.getByLabelText("MomentumAgent weight") as HTMLInputElement,
    );
    fireEvent.change(momentum, { target: { value: "0.6" } });
    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));

    // the shared WORM confirm opens with the old->new diff
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("0.45");
    expect(dialog.textContent).toContain("0.6");

    fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
    await waitFor(() => expect(restartMock).toHaveBeenCalled());

    // record -> persist -> restart, with the SERVER-confirmed values persisted 1:1
    expect(postTradingSettings).toHaveBeenCalledWith(
      { MOMENTUM_AGENT_WEIGHT: "0.6" },
      expect.stringMatching(/audit trail/i),
      expect.any(String),
    );
    expect(saveSetupMock).toHaveBeenCalledWith({ MOMENTUM_AGENT_WEIGHT: "0.6" });
    const persistOrder = saveSetupMock.mock.invocationCallOrder[0];
    const recordOrder = (postTradingSettings as unknown as ReturnType<typeof vi.fn>).mock
      .invocationCallOrder[0];
    const restartOrder = restartMock.mock.invocationCallOrder[0];
    expect(recordOrder).toBeLessThan(persistOrder);
    expect(persistOrder).toBeLessThan(restartOrder);
  });

  it("T15: a failed WORM record on the real path persists and restarts NOTHING", async () => {
    (postTradingSettings as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("chain not writable"),
    );
    render(<RoundTableCard />);
    const momentum = await waitFor(
      () => screen.getByLabelText("MomentumAgent weight") as HTMLInputElement,
    );
    fireEvent.change(momentum, { target: { value: "0.6" } });
    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));
    fireEvent.click(await screen.findByRole("button", { name: /change and restart/i }));

    await waitFor(() => screen.getByRole("alert"));
    expect(saveSetupMock).not.toHaveBeenCalled();
    expect(restartMock).not.toHaveBeenCalled();
  });

  it("T16 (#3187): after apply→restart the card RECONNECTS through the engine outage and recovers — never stranded on 'not available'", async () => {
    vi.useFakeTimers();
    try {
      (postTradingSettings as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
        success: true,
        applied: {},
        changes: [{ key: "MOMENTUM_AGENT_WEIGHT", from: "0.45", to: "0.6" }],
        detail: "recorded",
      });
      mockGet.mockResolvedValue(payload()); // initial load OK
      render(<RoundTableCard />);
      await vi.advanceTimersByTimeAsync(0); // settle initial load
      const momentum = screen.getByLabelText("MomentumAgent weight") as HTMLInputElement;

      // the post-restart refresh hits a restarting engine: fail once, then the engine returns.
      mockGet.mockReset();
      mockGet
        .mockRejectedValueOnce(new Error("engine restarting"))
        .mockResolvedValue(payload());

      fireEvent.change(momentum, { target: { value: "0.6" } });
      fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));
      await vi.advanceTimersByTimeAsync(0); // modal opens
      fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
      // flush record → persist → restart → onApplied → refresh's first GET (rejects)
      await vi.advanceTimersByTimeAsync(0);
      await vi.advanceTimersByTimeAsync(0);
      await vi.advanceTimersByTimeAsync(0);
      expect(restartMock).toHaveBeenCalled();

      // BUG WAS: setFailed(true) → "not available" + all rows hidden, stuck forever.
      // FIX: reconnecting state, last-known rows stay visible, no terminal error.
      expect(screen.getByRole("status").textContent).toMatch(/reconnect/i);
      expect(screen.queryByText(/not available/i)).toBeNull();
      expect(screen.getByLabelText("MomentumAgent weight")).toBeTruthy();

      // advance past the retry backoff → engine back → fully recovered, no error, rows present
      await vi.advanceTimersByTimeAsync(1600);
      expect(screen.queryByText(/reconnect/i)).toBeNull();
      expect(screen.queryByText(/not available/i)).toBeNull();
      expect(screen.getByLabelText("MomentumAgent weight")).toBeTruthy();
      // the roster was actually re-fetched during recovery (fail + retry-success)
      expect(mockGet.mock.calls.length).toBeGreaterThanOrEqual(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
