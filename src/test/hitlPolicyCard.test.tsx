// LIVE-1 T2 (#1425): the honest HITL-policy card. Replaces the hardcoded "Auto-approve under
// €250 with senate ≥ 0.65" localStorage placeholder with the REAL engine policy — read from and
// written to GET/POST /api/hitl/policy. HITL_ENABLED is env-only (C2), so it is shown read-only.
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const getHitlPolicy = vi.fn();
const updateHitlPolicy = vi.fn();
vi.mock("@/lib/api", () => ({
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

  it("loads the real policy and shows HITL_ENABLED read-only (env-only, not a fake toggle)", async () => {
    render(<HitlPolicyCard />);
    await waitFor(() => expect(getHitlPolicy).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/human approval: on/i)).toBeTruthy();
    // No toggle for HITL_ENABLED — it is read-only (env+redeploy, C2).
    expect(screen.queryByLabelText(/toggle-hitl-enabled/i)).toBeNull();
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

  it("shows an unavailable state when the policy fetch fails (engine offline)", async () => {
    getHitlPolicy.mockRejectedValue(new Error("offline"));
    render(<HitlPolicyCard />);
    await waitFor(() =>
      expect(screen.getByText(/unavailable|couldn't reach|offline/i)).toBeTruthy(),
    );
  });
});
