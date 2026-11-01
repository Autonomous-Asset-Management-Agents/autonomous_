/**
 * STW-4 (#4630) — "Live account strategy" card (UX-Konzept #4628, Ablauf 7).
 *
 * Assigning a named strategy to the live account goes through the shared
 * SettingsApplyModal: confirm → record (POST /api/strategies/{name}/assign-live, WORM)
 * → persist (exactly the server's `changes` to setup.json) → restart. The preview diff
 * is computed client-side from the strategy values over the delivery state; the server
 * diff is the authoritative one that gets persisted.
 */
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useStore } from "@/console/store/useStore";
import { HttpError } from "@/lib/api";

const restartEngine = vi.fn();
const saveSetupState = vi.fn();
vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return {
    ...actual,
    isDesktop: () => true,
    restartEngine: (...a: unknown[]) => restartEngine(...a),
    saveSetupState: (...a: unknown[]) => saveSetupState(...a),
  };
});

const listStrategies = vi.fn();
const assignLiveStrategy = vi.fn();
const getTradingSettings = vi.fn();
const getHitlPolicy = vi.fn();
const listStrategyAssignments = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listStrategies: (...a: unknown[]) => listStrategies(...a),
    listStrategyAssignments: (...a: unknown[]) => listStrategyAssignments(...a),
    assignLiveStrategy: (...a: unknown[]) => assignLiveStrategy(...a),
    getTradingSettings: (...a: unknown[]) => getTradingSettings(...a),
    getHitlPolicy: (...a: unknown[]) => getHitlPolicy(...a),
    fetchHealth: vi.fn().mockResolvedValue({ status: "healthy", paper_trading: false }),
    postSwitchReconcile: vi.fn().mockResolvedValue(undefined),
  };
});

import { LiveStrategyAssignment } from "@/console/desktop/strategyWorkshop/LiveStrategyAssignment";

const STRATEGIES = {
  strategien: [
    { name: "A", werte: { STOP_LOSS_PCT: "6.0" } },
    { name: "B", werte: { STOP_LOSS_PCT: "5.0" } },
  ],
  max_strategien: 3,
};

const SETTINGS = {
  agents: [],
  implied_vol_forecast_enabled: true,
  guardrails: {},
  settings: { STOP_LOSS_PCT: "7.0", SIGNAL_BUY_THRESHOLD: "0.60" },
  meta: [
    { key: "STOP_LOSS_PCT", kind: "float", default: "7.0", min: 1, max: 7.9 },
    { key: "SIGNAL_BUY_THRESHOLD", kind: "float", default: "0.73", min: 0.55, max: 0.75 },
  ],
};

const SERVER_CHANGES = [
  { key: "STOP_LOSS_PCT", from: "7.0", to: "5.0" },
  { key: "SIGNAL_BUY_THRESHOLD", from: "0.60", to: "0.73" },
];

const LIVE_CONSENT = /continues trading LIVE/i;

async function renderCard() {
  render(<LiveStrategyAssignment />);
  await screen.findByText("Live account strategy");
}

async function openReview(name = "B") {
  await renderCard();
  fireEvent.change(screen.getByLabelText("Strategy for the live account"), {
    target: { value: name },
  });
  fireEvent.click(screen.getByRole("button", { name: "Review changes" }));
  return screen.findByRole("dialog", { name: "Assign strategy to the live account?" });
}

function confirmLive() {
  fireEvent.click(screen.getByRole("checkbox", { name: LIVE_CONSENT }));
  fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
}

describe("LiveStrategyAssignment (STW-4 #4630)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    act(() => useStore.setState({ paperTrading: false }));
    listStrategies.mockResolvedValue(STRATEGIES);
    getTradingSettings.mockResolvedValue(SETTINGS);
    getHitlPolicy.mockResolvedValue({ HITL_ENABLED: true });
    listStrategyAssignments.mockResolvedValue([]);
    assignLiveStrategy.mockResolvedValue({
      success: true,
      strategie: "B",
      applied: {},
      changes: SERVER_CHANGES,
      detail: "strategy assignment recorded on the WORM chain",
    });
    restartEngine.mockResolvedValue(undefined);
    saveSetupState.mockResolvedValue(undefined);
  });

  it("A7-P2: Review changes shows the diff of the chosen strategy over the delivery state BEFORE confirm", async () => {
    await openReview();
    // STOP_LOSS_PCT 7.0 → 5.0 (strategy value), SIGNAL_BUY_THRESHOLD 0.60 → 0.73 (delivery state).
    expect(screen.getByText("5.0")).toBeTruthy();
    expect(screen.getByText("0.73")).toBeTruthy();
    expect(screen.getByText("0.60")).toBeTruthy();
    expect(assignLiveStrategy).not.toHaveBeenCalled();
  });

  it("A7-P3: Change and restart stays blocked until the live checkbox is ticked", async () => {
    await openReview();
    const confirm = screen.getByRole("button", { name: /change and restart/i }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: LIVE_CONSENT }));
    expect(confirm.disabled).toBe(false);
  });

  it("A7-P4: confirm runs record → persist → restart(goLive) in that order", async () => {
    const order: string[] = [];
    assignLiveStrategy.mockImplementation(async () => {
      order.push("record");
      return { success: true, strategie: "B", applied: {}, changes: SERVER_CHANGES, detail: "" };
    });
    saveSetupState.mockImplementation(async () => {
      order.push("persist");
    });
    restartEngine.mockImplementation(async () => {
      order.push("restart");
    });
    await openReview();
    confirmLive();
    await waitFor(() => expect(order).toEqual(["record", "persist", "restart"]));

    const [name, ack, nonce] = assignLiveStrategy.mock.calls[0];
    expect(name).toBe("B");
    expect(typeof ack).toBe("string");
    expect(ack.length).toBeGreaterThan(0);
    const [restartNonce, opts] = restartEngine.mock.calls[0];
    expect(restartNonce).toBe(nonce);
    expect(opts).toEqual({ goLive: true });
  });

  it("#4989: a strategy with another sizing method sends the method acknowledgment", async () => {
    const ack =
      'I confirm changing the live sizing method from "b" to "a". The change applies to real-money trading and is recorded on a tamper-evident audit trail.';
    listStrategies.mockResolvedValue({
      ...STRATEGIES,
      strategien: [{ name: "B", werte: { STOP_LOSS_PCT: "7.0", CLEAN_WEIGHT_SIZING: "a" } }],
    });
    getTradingSettings.mockResolvedValue({
      ...SETTINGS,
      settings: { ...SETTINGS.settings, CLEAN_WEIGHT_SIZING: "b" },
      meta: [...SETTINGS.meta, { key: "CLEAN_WEIGHT_SIZING", kind: "choice", default: "b" }],
    });
    await openReview();
    fireEvent.click(screen.getByRole("checkbox", { name: ack }));
    confirmLive();
    await waitFor(() => expect(assignLiveStrategy).toHaveBeenCalledTimes(1));
    expect(assignLiveStrategy.mock.calls[0][1]).toBe(ack);
  });

  it("persists exactly the changes of the server response", async () => {
    await openReview();
    confirmLive();
    await waitFor(() => expect(saveSetupState).toHaveBeenCalledTimes(1));
    expect(saveSetupState).toHaveBeenCalledWith({
      STOP_LOSS_PCT: "5.0",
      SIGNAL_BUY_THRESHOLD: "0.73",
    });
  });

  it("a failed record runs neither persist nor restart", async () => {
    assignLiveStrategy.mockRejectedValue(new Error("audit write failed — nothing assigned"));
    await openReview();
    confirmLive();
    await screen.findByRole("alert");
    expect(saveSetupState).not.toHaveBeenCalled();
    expect(restartEngine).not.toHaveBeenCalled();
  });

  it("an empty server diff persists nothing but still restarts; the modal shows T42", async () => {
    getTradingSettings.mockResolvedValue({
      ...SETTINGS,
      settings: { STOP_LOSS_PCT: "5.0", SIGNAL_BUY_THRESHOLD: "0.73" },
    });
    assignLiveStrategy.mockResolvedValue({
      success: true,
      strategie: "B",
      applied: {},
      changes: [],
      detail: "",
    });
    await openReview();
    expect(
      screen.getByText(
        "No setting values change. The assignment is still recorded in the audit chain.",
      ),
    ).toBeTruthy();
    confirmLive();
    await waitFor(() => expect(restartEngine).toHaveBeenCalled());
    expect(assignLiveStrategy).toHaveBeenCalled();
    expect(saveSetupState).not.toHaveBeenCalled();
  });

  it("after the assignment the card shows the assigned strategy", async () => {
    await openReview();
    confirmLive();
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Assign strategy to the live account?" })).toBeNull(),
    );
    expect(screen.getByText(/Assigned: B/)).toBeTruthy();
  });

  // #4891 (STW-4d): GET /api/strategy-assignments — the card knows the assignment after a reload.
  it("after a reload the card shows the strategy assigned to the live account", async () => {
    listStrategyAssignments.mockResolvedValue([
      { konto: "live", strategie: "A", strategie_hash: "sha256:a", timestamp: "2026-10-29T10:00:00+00:00" },
    ]);
    await renderCard();
    expect(await screen.findByText(/Assigned: A/)).toBeTruthy();
    // Still neutral: knowing the assignment preselects nothing (ADR-027).
    expect((screen.getByLabelText("Strategy for the live account") as HTMLSelectElement).value).toBe("");
  });

  it("an assignment of another account does not show as the live assignment", async () => {
    listStrategyAssignments.mockResolvedValue([
      { konto: "paper-1", strategie: "A", strategie_hash: "sha256:a", timestamp: "t" },
    ]);
    await renderCard();
    await waitFor(() => expect(listStrategyAssignments).toHaveBeenCalled());
    expect(screen.queryByText(/Assigned:/)).toBeNull();
  });

  it("an unreadable assignment list keeps the card usable and shows no Assigned line", async () => {
    listStrategyAssignments.mockRejectedValue(new HttpError(503, "not readable", "/api/strategy-assignments"));
    await renderCard();
    await waitFor(() => expect(listStrategyAssignments).toHaveBeenCalled());
    expect(screen.getByLabelText("Strategy for the live account")).toBeTruthy();
    expect(screen.queryByText("Could not load the data.")).toBeNull();
    expect(screen.queryByText(/Assigned:/)).toBeNull();
  });

  it("A7-P5: Cancel writes nothing and closes the modal", async () => {
    await openReview();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(assignLiveStrategy).not.toHaveBeenCalled();
    expect(saveSetupState).not.toHaveBeenCalled();
    expect(restartEngine).not.toHaveBeenCalled();
  });

  it("A7-P6: with HITL off Review changes is blocked and the hint is visible", async () => {
    getHitlPolicy.mockResolvedValue({ HITL_ENABLED: false });
    await renderCard();
    fireEvent.change(screen.getByLabelText("Strategy for the live account"), {
      target: { value: "B" },
    });
    expect(
      await screen.findByText("Live assignment requires human approval of orders to be on."),
    ).toBeTruthy();
    const review = screen.getByRole("button", { name: "Review changes" }) as HTMLButtonElement;
    expect(review.disabled).toBe(true);
  });

  it("flag off: GET /api/strategies answers 404 → the card is not rendered", async () => {
    listStrategies.mockRejectedValue(new HttpError(404, "Not Found", "/api/strategies"));
    const { container } = render(<LiveStrategyAssignment />);
    await waitFor(() => expect(listStrategies).toHaveBeenCalled());
    await act(async () => {});
    expect(container.innerHTML).toBe("");
    expect(screen.queryByText("Live account strategy")).toBeNull();
  });

  it("lists the strategies in the order the engine returns them, without preselection", async () => {
    await renderCard();
    const select = screen.getByLabelText("Strategy for the live account") as HTMLSelectElement;
    const names = Array.from(select.options)
      .map((o) => o.value)
      .filter(Boolean);
    expect(names).toEqual(["A", "B"]);
    expect(select.value).toBe("");
    expect((screen.getByRole("button", { name: "Review changes" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
