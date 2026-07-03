import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
    await waitFor(() => expect(screen.getByText("Blocked")).toBeTruthy());
    expect(readAuditChain).toHaveBeenCalled();
    expect(screen.getByText("Max order value exceeded")).toBeTruthy();
    expect(screen.getByText("Approved")).toBeTruthy();
    expect(screen.getAllByText("AAPL").length).toBe(2);
  });

  it("desktop build with an empty log: shows the 'no decisions yet' state", async () => {
    setBridge({ isDesktop: true, readAuditChain: vi.fn().mockResolvedValue([]) });
    render(<AuditChain />);
    await waitFor(() => expect(screen.getByText(/no decisions recorded yet/i)).toBeTruthy());
  });
});
