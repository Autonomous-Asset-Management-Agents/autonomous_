// LIVE-1 T2 (#1425): the honest HITL-policy card. Replaces the hardcoded "Auto-approve under
// €250 with senate ≥ 0.65" localStorage placeholder with the REAL engine policy — read from and
// written to GET/POST /api/hitl/policy. HITL_ENABLED is env-only (C2), so it is shown read-only.
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const getHitlPolicy = vi.fn();
const updateHitlPolicy = vi.fn();
vi.mock("@/lib/api", () => ({
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  getHitlPolicy: () => getHitlPolicy(),
  updateHitlPolicy: (b: unknown) => updateHitlPolicy(b),
}));

import { HitlPolicyCard } from "../console/desktop/HitlPolicyCard";

const POLICY = {
  HITL_ENABLED: true,
  HITL_MAX_VALUE_PER_TRADE: 0,
  HITL_MAX_VALUE_PER_DAY: 0,
  HITL_AUTONOMOUS_UNLIMITED: false,
  HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: true,
  HITL_EXPIRY_SECONDS: 900,
};

describe("HitlPolicyCard (#1425 — honest HITL policy, real /api/hitl/policy)", () => {
  beforeEach(() => {
    getHitlPolicy.mockReset().mockResolvedValue({ ...POLICY });
    updateHitlPolicy.mockReset().mockResolvedValue({ ...POLICY });
  });

  it("is the ONE autonomy control: mode tiles + honest state, no fake 'Human approval' pill (INC-7)", async () => {
    render(<HitlPolicyCard />);
    await waitFor(() => expect(getHitlPolicy).toHaveBeenCalledTimes(1));
    // The two-axis mode selector is the single real autonomy control.
    expect(screen.getByLabelText("mode-auto")).toBeTruthy();
    expect(screen.getByLabelText("mode-hitl")).toBeTruthy();
    // The misleading standalone "Human approval: ON" pill (master flag, not effective state) is gone;
    // an honest state line says what actually happens (default policy = HITL, limit 0 = every order).
    expect(screen.queryByText(/human approval: on/i)).toBeNull();
    expect(screen.getByText(/human approval required/i)).toBeTruthy();
  });

  it("has NO hardcoded €250 placeholder — exposes the REAL per-trade limit field", async () => {
    render(<HitlPolicyCard />);
    await waitFor(() => expect(getHitlPolicy).toHaveBeenCalled());
    expect(screen.queryByText(/€250/)).toBeNull();
    expect(screen.queryByText(/senate ≥ 0\.65/i)).toBeNull();
    expect(screen.getByLabelText(/max-per-trade/i)).toBeTruthy();
  });

  it("saves edited limits via POST /api/hitl/policy — never sends HITL_ENABLED (env-only)", async () => {
    render(<HitlPolicyCard />);
    await waitFor(() => expect(getHitlPolicy).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText(/max-per-trade/i), { target: { value: "5000" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(updateHitlPolicy).toHaveBeenCalledTimes(1));
    const body = updateHitlPolicy.mock.calls[0][0] as Record<string, unknown>;
    expect(body.HITL_MAX_VALUE_PER_TRADE).toBe(5000);
    expect("HITL_ENABLED" in body).toBe(false);
  });

  it("HITL_ENABLED=false ⇒ Autonomous (no approval gate ⇒ effectively autonomous) — INC-4/F4", async () => {
    // Paper default: HITL_ENABLED false (engine sets it only on live). With no master gate there is
    // NO per-order approval step, so the honest state is Autonomous — even though UNLIMITED is false.
    getHitlPolicy.mockResolvedValue({
      ...POLICY,
      HITL_ENABLED: false,
      HITL_AUTONOMOUS_UNLIMITED: false,
    });
    render(<HitlPolicyCard />);
    await waitFor(() => expect(getHitlPolicy).toHaveBeenCalled());
    expect(screen.getByText(/autonomous — every order auto-executes/i)).toBeTruthy();
    // The old bug showed "Human approval required" on paper (derived from UNLIMITED only).
    expect(screen.queryByText(/human approval required/i)).toBeNull();
  });

  it("HITL_ENABLED=true + UNLIMITED=false ⇒ Human-in-the-loop (real gate active)", async () => {
    getHitlPolicy.mockResolvedValue({
      ...POLICY,
      HITL_ENABLED: true,
      HITL_AUTONOMOUS_UNLIMITED: false,
    });
    render(<HitlPolicyCard />);
    await waitFor(() => expect(getHitlPolicy).toHaveBeenCalled());
    expect(screen.getByText(/human approval required/i)).toBeTruthy();
  });

  // ── RC2-4: optimistic toggle resilience (works server-side but the POST can take 13-39s) ──────
  it("RC2-4 optimistic: clicking a mode flips the tile INSTANTLY (before the slow POST resolves)", async () => {
    // gate ON so both tiles are live; start in hitl (UNLIMITED false)
    getHitlPolicy.mockResolvedValue({ ...POLICY, HITL_ENABLED: true, HITL_AUTONOMOUS_UNLIMITED: false });
    let resolveUpdate!: (v: unknown) => void;
    updateHitlPolicy.mockReset().mockReturnValue(new Promise((r) => { resolveUpdate = r; }));
    render(<HitlPolicyCard />);
    await waitFor(() => expect(getHitlPolicy).toHaveBeenCalled());

    fireEvent.click(screen.getByLabelText("mode-auto"));
    // Optimistic: the clicked tile shows "Saving…" immediately, WITHOUT awaiting the POST.
    expect(within(screen.getByLabelText("mode-auto")).getByText(/saving/i)).toBeTruthy();
    // Both tiles disabled while the save is inflight.
    expect(screen.getByLabelText("mode-auto")).toBeDisabled();
    expect(screen.getByLabelText("mode-hitl")).toBeDisabled();

    // Server confirms → reconcile from the response.
    resolveUpdate({ ...POLICY, HITL_ENABLED: true, HITL_AUTONOMOUS_UNLIMITED: true });
    await waitFor(() =>
      expect(within(screen.getByLabelText("mode-auto")).getByText(/^active$/i)).toBeTruthy(),
    );
  });

  it("RC2-4 optimistic revert: a failed save reverts the tile + surfaces the error", async () => {
    getHitlPolicy.mockResolvedValue({ ...POLICY, HITL_ENABLED: true, HITL_AUTONOMOUS_UNLIMITED: false }); // hitl
    updateHitlPolicy.mockReset().mockRejectedValue(new Error("engine down"));
    render(<HitlPolicyCard />);
    await waitFor(() => expect(getHitlPolicy).toHaveBeenCalled());

    fireEvent.click(screen.getByLabelText("mode-auto")); // optimistic → auto
    await waitFor(() => expect(screen.getByText(/couldn't save the policy/i)).toBeTruthy());
    // Reverted: hitl is active again; auto is not (the optimistic flip rolled back).
    expect(within(screen.getByLabelText("mode-hitl")).getByText(/^active$/i)).toBeTruthy();
    expect(within(screen.getByLabelText("mode-auto")).queryByText(/^active$/i)).toBeNull();
  });

  it("RC2-4 mode-derivation: HITL_ENABLED=false disables + annotates the HITL tile (no silent collapse)", async () => {
    getHitlPolicy.mockResolvedValue({ ...POLICY, HITL_ENABLED: false, HITL_AUTONOMOUS_UNLIMITED: false });
    render(<HitlPolicyCard />);
    await waitFor(() => expect(getHitlPolicy).toHaveBeenCalled());
    // The HITL tile no longer silently collapses to auto on click — it is disabled + annotated.
    expect(screen.getByLabelText("mode-hitl")).toBeDisabled();
    expect(screen.getByText(/enforced automatically on live/i)).toBeTruthy();
    // Effective state line is still Autonomous (INC-4/F4 semantics preserved).
    expect(screen.getByText(/autonomous — every order auto-executes/i)).toBeTruthy();
  });

  it("shows an unavailable state when the policy fetch fails (engine offline)", async () => {
    getHitlPolicy.mockRejectedValue(new Error("offline"));
    render(<HitlPolicyCard />);
    await waitFor(() =>
      expect(screen.getByText(/unavailable|couldn't reach|offline/i)).toBeTruthy(),
    );
  });
});
