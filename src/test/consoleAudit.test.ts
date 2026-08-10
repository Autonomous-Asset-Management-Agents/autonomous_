import { describe, it, expect } from "vitest";
import { adaptAuditEntries, type AuditEvent } from "../console/live/audit";

/**
 * G3d-2 (#1050): the audit-chain adapter maps the engine's raw audit-log lines
 * — exactly the dicts core/round_table/senate_log.py::_async_log_to_jsonl
 * writes (SenateSession fields + prev_hash + hash) — into console AuditEvents.
 * Only signals the contract actually carries are derived (no fabricated kinds).
 */
const entry = (over: Record<string, unknown> = {}) => ({
  session_id: "s1",
  symbol: "AAPL",
  timestamp: "2026-06-13T14:32:00Z",
  consensus_score: 0.62,
  gatekeeper_approved: true,
  gatekeeper_reason: "",
  signal_action: "BUY",
  votes: [],
  prev_hash: "0".repeat(64),
  hash: "a".repeat(64),
  ...over,
});

describe("adaptAuditEntries", () => {
  it("returns [] for non-array / junk input", () => {
    expect(adaptAuditEntries(null)).toEqual([]);
    expect(adaptAuditEntries(undefined)).toEqual([]);
    expect(adaptAuditEntries({})).toEqual([]);
    expect(adaptAuditEntries([null, 42, "x"])).toEqual([]);
  });

  it("maps an approved BUY to an approval event with a real Date + hash", () => {
    const [e] = adaptAuditEntries([entry()]);
    expect(e.kind).toBe("approval");
    expect(e.symbol).toBe("AAPL");
    expect(e.message).toContain("BUY");
    expect(e.message).toContain("62%"); // consensus, deterministic numeric
    expect(e.hash).toBe("a".repeat(64));
    expect(e.ts).toBeInstanceOf(Date);
    expect(Number.isNaN(e.ts.getTime())).toBe(false);
  });

  it("maps a gatekeeper-blocked entry to a rejection carrying the reason verbatim", () => {
    const [e] = adaptAuditEntries([
      entry({ gatekeeper_approved: false, gatekeeper_reason: "Max order value exceeded" }),
    ]);
    expect(e.kind).toBe("rejection");
    expect(e.message).toBe("Max order value exceeded");
  });

  it("falls back to a generic block message when no reason is given", () => {
    const [e] = adaptAuditEntries([entry({ gatekeeper_approved: false, gatekeeper_reason: "" })]);
    expect(e.kind).toBe("rejection");
    expect(e.message.length).toBeGreaterThan(0);
  });

  it("maps an approved HOLD to a neutral decision (not a green 'approval')", () => {
    const [e] = adaptAuditEntries([entry({ signal_action: "HOLD" })]);
    expect(e.kind).toBe("decision");
    expect(e.message.toLowerCase()).toContain("hold");
  });

  it("orders newest-first (input arrives oldest→newest from the reader)", () => {
    const out = adaptAuditEntries([
      entry({ timestamp: "2026-06-13T10:00:00Z", hash: "1".repeat(64) }),
      entry({ timestamp: "2026-06-13T11:00:00Z", hash: "2".repeat(64) }),
    ]);
    expect(out.map((e: AuditEvent) => e.hash)).toEqual(["2".repeat(64), "1".repeat(64)]);
  });

  it("drops undatable rows rather than emitting Invalid Date", () => {
    const out = adaptAuditEntries([entry({ timestamp: undefined }), entry({ timestamp: "nonsense" })]);
    expect(out).toEqual([]);
  });
});
