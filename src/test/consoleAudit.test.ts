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

// ── #2779: the chain carries more than senate decisions ──────────────────────────────────────
//
// Reported 2026-08-11: two rows at 06:35 showed "Decision · Hold · — consensus" with no symbol.
// They were not empty and not decisions — read back from audit_log_2026-08-11.jsonl by their
// displayed hashes, they were the operator's live-trading enablement and the approval-policy
// change four seconds before it:
//
//   74e9e813382d  event_type: "hitl_policy"      actor: "api"        04:35:39Z
//   fd672ffc9911  event_type: "live_enablement"  action: "enable"    04:35:43Z
//
// This adapter's docstring assumed "main's audit log records senate decisions with a gatekeeper
// verdict only". That expired: the engine writes hitl_policy and live_enablement into the SAME
// chain. `RawAuditEntry` has no `event_type`, so those records lost every field the adapter looks
// for — and, worst of all, a MISSING `gatekeeper_approved` is "treated as approved", so the record
// stating that real-money trading was armed rendered as an ordinary approved Hold.
//
// The fixtures below are the shape of the real records, not invented ones.

const liveEnable = (over: Record<string, unknown> = {}) => ({
  event_type: "live_enablement",
  timestamp: "2026-08-11T04:35:43.266288+00:00",
  actor: "operator",
  action: "enable",
  acknowledgment: "I understand this enables LIVE trading with real money on my Alpaca live account…",
  nonce: "b2c4e0f3-9421-4087-a11b-ab60db303da0",
  switch_id: "b2c4e0f3-9421-4087-a11b-ab60db303da0",
  prev_hash: "7".repeat(64),
  hash: "f".repeat(64),
  ...over,
});

const policyChange = (over: Record<string, unknown> = {}) => ({
  event_type: "hitl_policy",
  timestamp: "2026-08-11T04:35:39.123000+00:00",
  actor: "api",
  old_policy: { HITL_ENABLED: false, HITL_AUTONOMOUS_UNLIMITED: false },
  new_policy: { HITL_ENABLED: false, HITL_AUTONOMOUS_UNLIMITED: true },
  prev_hash: "5".repeat(64),
  hash: "7".repeat(64),
  ...over,
});

describe("#2779 non-decision record kinds", () => {
  it("a live-enablement is NOT a decision and NOT reported as approved", () => {
    const [e] = adaptAuditEntries([liveEnable()]);
    expect(e.kind).toBe("live_enable");
    expect(e.kind).not.toBe("decision");
    expect(e.kind).not.toBe("approval"); // the missing-gatekeeper heuristic must not apply here
    expect(e.message).not.toMatch(/hold/i);
    expect(e.message).not.toMatch(/consensus/i);
    expect(e.hash).toBe("f".repeat(64));
  });

  it("a live-enablement names the actor and stays legible without the acknowledgment prose", () => {
    const [e] = adaptAuditEntries([liveEnable()]);
    expect(e.message).toMatch(/operator/i);
    // The Art-14 acknowledgment must not be dumped verbatim into a table row…
    expect(e.message).not.toContain("I understand this enables LIVE trading");
    // …but the row must be long enough to be meaningful, not a bare label.
    expect(e.message.length).toBeGreaterThan(10);
  });

  it("a REVOCATION is visually distinct from an enablement — conflating them would be worse", () => {
    const [enabled] = adaptAuditEntries([liveEnable()]);
    const [revoked] = adaptAuditEntries([liveEnable({ action: "disable" })]);
    expect(revoked.kind).toBe("live_disable");
    expect(revoked.kind).not.toBe(enabled.kind);
    expect(revoked.message).not.toBe(enabled.message);
  });

  it("a policy change is its own kind and names what changed", () => {
    const [e] = adaptAuditEntries([policyChange()]);
    expect(e.kind).toBe("policy");
    expect(e.message).not.toMatch(/hold|consensus/i);
    // The one key that actually flipped must be visible; unchanged keys must not be noise.
    expect(e.message).toContain("HITL_AUTONOMOUS_UNLIMITED");
    expect(e.message).not.toContain("HITL_ENABLED");
  });

  it("an UNKNOWN event_type is surfaced, never dropped and never disguised as a decision", () => {
    const [e] = adaptAuditEntries([
      { event_type: "some_future_kind", timestamp: "2026-08-11T05:00:00Z", hash: "c".repeat(64) },
    ]);
    expect(e).toBeTruthy();
    expect(e.kind).toBe("unknown");
    expect(e.message).toContain("some_future_kind"); // say what it was
    expect(e.hash).toBe("c".repeat(64));
  });

  it("senate decisions are unchanged — the three original kinds still derive as before", () => {
    const [buy] = adaptAuditEntries([entry()]);
    expect(buy.kind).toBe("approval");
    expect(buy.message).toContain("BUY");

    const [hold] = adaptAuditEntries([entry({ signal_action: "HOLD" })]);
    expect(hold.kind).toBe("decision");
    expect(hold.message).toMatch(/hold/i);

    const [blocked] = adaptAuditEntries([
      entry({ gatekeeper_approved: false, gatekeeper_reason: "Agent VETO" }),
    ]);
    expect(blocked.kind).toBe("rejection");
    expect(blocked.message).toBe("Agent VETO");
  });

  it("the missing-gatekeeper heuristic still applies to real senate decisions only", () => {
    // A decision row with no gatekeeper field is still treated as approved — that behaviour is
    // correct for senate entries (the engine only writes the field when it ran the gate) and must
    // survive. The bug was applying it to records that never went through the gate at all.
    const [e] = adaptAuditEntries([entry({ gatekeeper_approved: undefined })]);
    expect(e.kind).toBe("approval");
  });

  it("the real morning pair renders as two distinct, non-empty rows", () => {
    // End to end, in the order the reader returns them (oldest→newest); the feed is newest-first.
    const evs = adaptAuditEntries([policyChange(), liveEnable()]);
    expect(evs).toHaveLength(2);
    expect(evs[0].kind).toBe("live_enable"); // newest first
    expect(evs[1].kind).toBe("policy");
    for (const e of evs) {
      expect(e.symbol ?? "").toBe(""); // these records genuinely have no symbol…
      expect(e.message).not.toMatch(/^Hold · — consensus$/); // …but must not read as an empty Hold
    }
  });
});

// ── #2780 follow-up: hitl_execution was the type this adapter forgot ────────────────────────────
//
// Measured on the installation 2026-08-12: the chain holds 10,589 `hitl_execution` records against
// 17 `live_enablement` and 12 `hitl_policy`. The two kinds #2779 taught the adapter account for
// 0.3 % of the typed records; the one it did not know accounts for 99.5 %, and every one of them
// rendered as "Unrecognised audit event: hitl_execution" with verdict "Unknown".
//
// The dangerous half is not the noise. `branch` has four values (core/hitl_gate.py:360/375/430/476,
// core/engine/order_executor.py:178) and two of them mean an order WAS submitted with no operator
// approval — `under_limit` (below the approval threshold) and `risk_off_exempt` (risk-reducing exit).
// Rendering those as "Unknown" hides an autonomous execution from the audit reader, which is the
// same failure #2779 fixed for live-enablement: this file's rule is that a reader must see at a
// glance whether real money moved.
//
// The fixture is the shape of a real record (read from audit_log_2026-08-12.jsonl).

const hitlExec = (over: Record<string, unknown> = {}) => ({
  event_type: "hitl_execution",
  timestamp: "2026-08-12T14:00:36.700287+00:00",
  symbol: "CTAS",
  action: "BUY",
  branch: "skipped",
  policy_hash: "e165f923fbe8f9b5d4aa91704a5cf5d65dd186a62832b1e780ae5756fbf0d57e",
  order_value: 0.0,
  day_notional_after: null,
  approval_id: null,
  threshold_breached: null,
  reason: "SKIPPED: sizing_zero -- position size resolved to 0 (risk limits / exposure cap)",
  prev_hash: "6".repeat(64),
  hash: "69bc2391744b".padEnd(64, "0"),
  ...over,
});

describe("#2780 follow-up: hitl_execution", () => {
  it("is never 'unknown' and never reads as an approved decision", () => {
    const [e] = adaptAuditEntries([hitlExec()]);
    expect(e.kind).not.toBe("unknown");
    expect(e.kind).not.toBe("approval");
    expect(e.kind).not.toBe("decision");
    expect(e.message).not.toMatch(/unrecognised/i);
  });

  it("a skipped execution says nothing happened, and carries the reason", () => {
    const [e] = adaptAuditEntries([hitlExec()]);
    expect(e.kind).toBe("skipped");
    expect(e.symbol).toBe("CTAS");
    expect(e.message).toMatch(/sizing_zero/); // the reason is the whole value of the row
  });

  it("an order submitted WITHOUT approval is marked as an execution, not a skip", () => {
    // `under_limit`: below the approval threshold, so the gate let it through autonomously.
    const [e] = adaptAuditEntries([
      hitlExec({ branch: "under_limit", order_value: 34.9, reason: "", action: "BUY" }),
    ]);
    expect(e.kind).toBe("execution");
    expect(e.message).toMatch(/BUY/);
    expect(e.message).toMatch(/34\.9/); // the amount must be visible: real money moved
  });

  it("a risk-off exemption is also an execution", () => {
    const [e] = adaptAuditEntries([
      hitlExec({ branch: "risk_off_exempt", order_value: 61.86, action: "SELL" }),
    ]);
    expect(e.kind).toBe("execution");
    expect(e.message).toMatch(/exempt/i);
  });

  it("a queued order is pending, not executed and not skipped", () => {
    const [e] = adaptAuditEntries([
      hitlExec({ branch: "queued", order_value: 120.5, approval_id: "ap-7" }),
    ]);
    expect(e.kind).toBe("pending");
    expect(e.message).toMatch(/approval/i);
  });

  it("an unseen branch degrades to the safe side rather than claiming a skip", () => {
    // A new branch must not silently read as "nothing happened".
    const [e] = adaptAuditEntries([hitlExec({ branch: "some_future_branch" })]);
    expect(e.kind).not.toBe("skipped");
    expect(e.message).toMatch(/some_future_branch/);
  });
});

// ── #3181: the adapter recognised only 4 of 18 event_type classes ──────────────────────────────
//
// The engine writes at least 18 event_type classes; audit.ts handled live_enablement, hitl_policy,
// hitl_execution and decision. The other 14 (settings_change, trading_settings_seal, circuit_breaker,
// portfolio_stop_loss, …) all rendered "Unrecognised audit event: <type>" with verdict Unknown — an
// incomplete WORM-view. Reproduced live via the agent-settings restart flow (settings_change +
// trading_settings_seal). The record itself is correct on-chain; only the console view was blind.

const settingsChange = (over: Record<string, unknown> = {}) => ({
  event_type: "settings_change",
  timestamp: "2026-09-03T09:35:05Z",
  actor: "operator",
  acknowledgment: "I confirm this trading-settings change.",
  nonce: "31aee326-fff2-42a2-b8f0-b3c1409d3b5e",
  changes:
    '[{"key":"FUNDAMENTALS_AGENT_WEIGHT","from":"0.0","to":"0.35"},{"key":"VALUATION_AGENT_WEIGHT","from":"0.0","to":"0.35"}]',
  switch_id: "31aee326-fff2-42a2-b8f0-b3c1409d3b5e",
  prev_hash: "0".repeat(64),
  hash: "c".repeat(64),
  ...over,
});

describe("#3181: previously-unrecognised event types", () => {
  it("settings_change is recognised, names the changed keys and the actor", () => {
    const [e] = adaptAuditEntries([settingsChange()]);
    expect(e.kind).toBe("settings");
    expect(e.kind).not.toBe("unknown");
    expect(e.message).not.toMatch(/unrecognised/i);
    expect(e.message).toMatch(/FUNDAMENTALS_AGENT_WEIGHT/);
    expect(e.message).toMatch(/VALUATION_AGENT_WEIGHT/);
    expect(e.message).toMatch(/operator/);
  });

  it("trading_settings_seal is a settings event, not Unknown", () => {
    const [e] = adaptAuditEntries([
      { event_type: "trading_settings_seal", timestamp: "2026-09-03T09:35:00Z", actor: "system", app_version: "0.4.11", settings: "{}", hash: "d".repeat(64) },
    ]);
    expect(e.kind).toBe("settings");
    expect(e.message).not.toMatch(/unrecognised/i);
  });

  it("risk-control events (circuit_breaker, portfolio_stop_loss) render as risk, carrying the reason", () => {
    const [cb] = adaptAuditEntries([
      { event_type: "circuit_breaker", timestamp: "2026-09-03T09:00:00Z", reason: "daily drawdown -7% breached", hash: "e".repeat(64) },
    ]);
    expect(cb.kind).toBe("risk");
    expect(cb.message).toMatch(/drawdown/);
    const [sl] = adaptAuditEntries([
      { event_type: "portfolio_stop_loss", timestamp: "2026-09-03T09:01:00Z", symbol: "ABNB", hash: "f".repeat(64) },
    ]);
    expect(sl.kind).toBe("risk");
    expect(sl.message).not.toMatch(/unrecognised/i);
  });

  it("compliance & system events are typed, never Unknown", () => {
    const [eula] = adaptAuditEntries([
      { event_type: "eula_acceptance", timestamp: "2026-09-03T08:00:00Z", actor: "operator", hash: "1".repeat(64) },
    ]);
    expect(eula.kind).toBe("compliance");
    const [warn] = adaptAuditEntries([
      { event_type: "warning", timestamp: "2026-09-03T08:10:00Z", reason: "market data stale", hash: "2".repeat(64) },
    ]);
    expect(warn.kind).toBe("system");
    for (const e of [eula, warn]) expect(e.message).not.toMatch(/unrecognised/i);
  });

  it("a genuinely unknown future type still degrades to the honest 'Unrecognised' fallback", () => {
    const [e] = adaptAuditEntries([
      { event_type: "some_future_event_2099", timestamp: "2026-09-03T09:00:00Z", hash: "3".repeat(64) },
    ]);
    expect(e.kind).toBe("unknown");
    expect(e.message).toMatch(/unrecognised/i);
    expect(e.message).toMatch(/some_future_event_2099/);
  });
});
