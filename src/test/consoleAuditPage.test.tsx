import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { AuditChain } from "../console/desktop/pages/AuditChain";
import { useStore } from "../console/store/useStore";

/**
 * G3d-2 (#1050): the Audit Chain page polls the local hash-linked audit log
 * through the desktop bridge and renders one row per senate decision. The cloud
 * build has no local file → a desktop-only note, no rows.
 */
const setBridge = (impl: Record<string, unknown> | undefined) => {
  (window as unknown as { aaagents?: unknown }).aaagents = impl;
};

const entry = (over: Record<string, unknown> = {}) => ({
  symbol: "AAPL",
  timestamp: "2026-06-13T14:32:00Z",
  consensus_score: 0.62,
  gatekeeper_approved: true,
  gatekeeper_reason: "",
  signal_action: "BUY",
  hash: "a".repeat(64),
  ...over,
});

describe("AuditChain page", () => {
  beforeEach(() => {
    useStore.setState({ audit: [] });
    setBridge(undefined);
  });
  afterEach(() => setBridge(undefined));

  it("cloud build: shows a desktop-only note and no rows", () => {
    render(<AuditChain />);
    expect(screen.getByText(/available in the desktop app/i)).toBeTruthy();
  });

  it("desktop build: polls the bridge and renders approval + rejection rows", async () => {
    const readAuditChain = vi.fn().mockResolvedValue([
      entry({ signal_action: "BUY" }),
      entry({
        gatekeeper_approved: false,
        gatekeeper_reason: "Max order value exceeded",
        hash: "b".repeat(64),
      }),
    ]);
    setBridge({ isDesktop: true, readAuditChain });

    render(<AuditChain />);
    await waitFor(() => expect(screen.getAllByText("Blocked").length).toBeGreaterThan(0));
    expect(readAuditChain).toHaveBeenCalled();
    expect(screen.getByText("Max order value exceeded")).toBeTruthy();
    expect(screen.getAllByText("Approved").length).toBeGreaterThan(0);
    expect(screen.getAllByText("AAPL").length).toBe(2);
  });

  it("desktop build with an empty log: shows the 'no decisions yet' state", async () => {
    setBridge({ isDesktop: true, readAuditChain: vi.fn().mockResolvedValue([]) });
    render(<AuditChain />);
    await waitFor(() => expect(screen.getByText(/no decisions recorded yet/i)).toBeTruthy());
  });

  // ── #2779: the two records the page used to flatten into empty approved decisions ──────────
  //
  // Reported 2026-08-11: the operator's 06:35 live-trading enablement and the policy change four
  // seconds before it both rendered as "Decision · Hold · — consensus" with no symbol. Shapes below
  // are the real records from audit_log_2026-08-11.jsonl.
  it("desktop build: renders a live-enablement and a policy change as their own verdicts", async () => {
    const readAuditChain = vi.fn().mockResolvedValue([
      {
        event_type: "hitl_policy",
        timestamp: "2026-08-11T04:35:39.123000+00:00",
        actor: "api",
        old_policy: { HITL_AUTONOMOUS_UNLIMITED: false },
        new_policy: { HITL_AUTONOMOUS_UNLIMITED: true },
        hash: "7".repeat(64),
      },
      {
        event_type: "live_enablement",
        timestamp: "2026-08-11T04:35:43.266288+00:00",
        actor: "operator",
        action: "enable",
        acknowledgment: "I understand this enables LIVE trading with real money…",
        switch_id: "b2c4e0f3-9421-4087-a11b-ab60db303da0",
        hash: "f".repeat(64),
      },
    ]);
    setBridge({ isDesktop: true, readAuditChain });

    render(<AuditChain />);
    const rows = () => within(screen.getByTestId("audit-rows"));
    await waitFor(() => expect(rows().getAllByText("Live ON").length).toBeGreaterThan(0));
    expect(rows().getAllByText("Policy").length).toBeGreaterThan(0);
    // The regression itself: neither row may read as an empty Hold decision any more.
    expect(rows().queryByText("Hold · — consensus")).toBeNull();
    expect(rows().queryAllByText("Decision").length).toBe(0);
    // The Art-14 acknowledgment paragraph must not be dumped into a table row.
    expect(screen.queryByText(/I understand this enables LIVE trading/)).toBeNull();
    // …but the actor must be visible, and the changed policy key named.
    expect(rows().getByText(/LIVE trading enabled by operator/)).toBeTruthy();
    expect(rows().getByText(/HITL_AUTONOMOUS_UNLIMITED/)).toBeTruthy();
  });

  it("desktop build: a revocation is labelled distinctly from an enablement", async () => {
    const readAuditChain = vi.fn().mockResolvedValue([
      {
        event_type: "live_enablement",
        timestamp: "2026-08-11T05:00:00Z",
        actor: "operator",
        action: "disable",
        hash: "d".repeat(64),
      },
    ]);
    setBridge({ isDesktop: true, readAuditChain });
    render(<AuditChain />);
    const rows2 = () => within(screen.getByTestId("audit-rows"));
    await waitFor(() => expect(rows2().getAllByText("Live OFF").length).toBeGreaterThan(0));
    expect(rows2().queryAllByText("Live ON").length).toBe(0);
    expect(rows2().getByText(/LIVE trading revoked by operator/)).toBeTruthy();
  });

  it("desktop build: an unknown event kind is shown, not dropped and not disguised", async () => {
    const readAuditChain = vi.fn().mockResolvedValue([
      { event_type: "some_future_kind", timestamp: "2026-08-11T05:10:00Z", hash: "c".repeat(64) },
    ]);
    setBridge({ isDesktop: true, readAuditChain });
    render(<AuditChain />);
    const rows3 = () => within(screen.getByTestId("audit-rows"));
    await waitFor(() => expect(rows3().getAllByText("Unknown").length).toBeGreaterThan(0));
    expect(rows3().getByText(/some_future_kind/)).toBeTruthy();
    expect(screen.queryByText(/no decisions recorded yet/i)).toBeNull(); // not dropped
  });
});
