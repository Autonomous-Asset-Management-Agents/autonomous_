// #2666 (Epic #2655, Weg 1) — the LOCAL constraint-approval card: read-only review of the
// pending EOD sector-cap proposal (GET /api/admin/portfolio-constraints/pending) + an explicit
// Confirm that POSTs the Ed25519 approval token (#2653). Opening/reading NEVER mutates; only
// the Confirm click does. Renders nothing while no proposal is pending.
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const fetchPendingConstraints = vi.fn();
const approvePortfolioConstraints = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchPendingConstraints: () => fetchPendingConstraints(),
  approvePortfolioConstraints: (t: string, a: string) => approvePortfolioConstraints(t, a),
}));

import { ConstraintApprovalCard } from "../console/desktop/ConstraintApprovalCard";

const PENDING = {
  pending: {
    caps: { Technology: 0.3, Energy: 0.2 },
    actor: "eod_assessment",
    created_at: "2026-08-05T20:05:00Z",
  },
  approved: { Technology: 0.25 },
};

describe("ConstraintApprovalCard (#2666 — local review + confirm)", () => {
  beforeEach(() => {
    fetchPendingConstraints.mockReset().mockResolvedValue(PENDING);
    approvePortfolioConstraints
      .mockReset()
      .mockResolvedValue({ status: "approved", caps: PENDING.pending.caps });
  });

  it("renders NOTHING while no proposal is pending", async () => {
    fetchPendingConstraints.mockResolvedValue({ pending: null, approved: {} });
    const { container } = render(<ConstraintApprovalCard />);
    await waitFor(() => expect(fetchPendingConstraints).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("shows the read-only diff (current -> proposed) without mutating", async () => {
    render(<ConstraintApprovalCard />);
    await screen.findByText(/portfolio constraints/i);
    // Technology has a current cap (25%) and a proposed one (30%); Energy is new (— -> 20%).
    expect(screen.getByText("Technology")).toBeTruthy();
    expect(screen.getByText("25%")).toBeTruthy();
    expect(screen.getByText("30%")).toBeTruthy();
    expect(screen.getByText("Energy")).toBeTruthy();
    expect(screen.getByText("20%")).toBeTruthy();
    expect(approvePortfolioConstraints).not.toHaveBeenCalled();
  });

  it("Confirm is gated on the approval token from the notification", async () => {
    render(<ConstraintApprovalCard />);
    await screen.findByText(/portfolio constraints/i);
    const confirm = screen.getByRole("button", { name: /approve constraints/i });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("constraint-token"), {
      target: { value: "tok.sig" },
    });
    expect(confirm).not.toBeDisabled();
  });

  it("Confirm POSTs the token and shows the success state", async () => {
    render(<ConstraintApprovalCard />);
    await screen.findByText(/portfolio constraints/i);
    fireEvent.change(screen.getByLabelText("constraint-token"), {
      target: { value: "tok.sig" },
    });
    fireEvent.click(screen.getByRole("button", { name: /approve constraints/i }));
    await waitFor(() =>
      expect(approvePortfolioConstraints).toHaveBeenCalledWith("tok.sig", "operator"),
    );
    await screen.findByText(/constraints approved/i);
  });

  it("a rejected token surfaces the engine's error, nothing is masked", async () => {
    approvePortfolioConstraints.mockRejectedValue(new Error("HTTP 400 Token Expired"));
    render(<ConstraintApprovalCard />);
    await screen.findByText(/portfolio constraints/i);
    fireEvent.change(screen.getByLabelText("constraint-token"), {
      target: { value: "stale.tok" },
    });
    fireEvent.click(screen.getByRole("button", { name: /approve constraints/i }));
    await screen.findByText(/token expired|couldn't approve/i);
    expect(screen.queryByText(/constraints approved/i)).toBeNull();
  });
});
