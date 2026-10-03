/**
 * Audit-chain adapter (G3d-2, #1050). Maps the engine's raw hash-linked
 * audit-log lines — the dicts LocalJSONAuditLogger._async_log_to_jsonl
 * (core/round_table/senate_log.py) appends to `audit_log_<date>.jsonl` — into
 * console AuditEvents. Each line is one SenateSession that passed (or was
 * blocked at) the risk gatekeeper.
 *
 * Honesty rule: derive only the kinds the contract actually carries, and never
 * fabricate the rest. The bundle UI had eight event kinds (fill/regime/guard/…)
 * with no source here.
 *
 * #2779 — the "senate decisions only" premise EXPIRED. The engine writes
 * `hitl_policy` and `live_enablement` records into the SAME chain
 * (`hitl_gate.log_live_enablement_event` + the policy sink). This adapter had no
 * `event_type`, so those records lost every field it looks for — no symbol, no
 * action, no consensus — and, worst of all, the "missing gatekeeper_approved
 * means approved" heuristic swallowed them into `decision`. Reported live:
 * the operator's 06:35 live-trading enablement and the policy change four
 * seconds before it both rendered as "Decision · Hold · — consensus".
 *
 * So `event_type` is now read FIRST, before any senate heuristic. The heuristic
 * is correct for senate entries and stays — it just may not be the default for
 * records that never went through the gate.
 */

/** Display kinds derivable from an audit entry.
 *
 *  `live_enable` and `live_disable` are deliberately SEPARATE: an enablement and
 *  a revocation are opposite facts, and collapsing them into one "live" kind
 *  would be worse than the bug this replaces — an audit reader must see at a
 *  glance whether real money was armed or stood down.
 *
 *  `unknown` exists so a future `event_type` can never vanish from the view nor
 *  masquerade as an approved decision. That is the honesty rule above, applied
 *  to records this file has not met yet. */
export type AuditKind =
  | "approval"
  | "rejection"
  | "decision"
  | "live_enable"
  | "live_disable"
  | "policy"
  /** #2811: hitl_execution — the per-order execution decision (99.5 % of typed records).
   *  `execution` = an order WAS submitted (autonomous under-limit or risk-off exempt);
   *  `pending`   = queued for operator approval;
   *  `skipped`   = approved by the board but NOT submitted (the reason says why). */
  | "execution"
  | "pending"
  | "skipped"
  /** #3181: classes the engine writes that this adapter forgot — now typed so the WORM-view is
   *  complete. `settings` = a governing-parameter/control change; `risk` = a risk-control event
   *  (breaker/stop); `compliance` = a legal/compliance record; `system` = an operational/lifecycle
   *  event. Each is distinct from `unknown`, which stays only for genuinely unseen future types. */
  | "settings"
  | "risk"
  | "compliance"
  | "system"
  | "unknown";

export interface AuditEvent {
  ts: Date;
  kind: AuditKind;
  symbol?: string;
  message: string;
  /** Full sha256 chain hash for the entry (integrity value, shown truncated). */
  hash: string;
}

/** The subset of the chain's entries the console reads. */
interface RawAuditEntry {
  /** #2779: the discriminator. Absent on senate decisions (they predate it). */
  event_type?: unknown;
  symbol?: unknown;
  timestamp?: unknown;
  signal_action?: unknown;
  consensus_score?: unknown;
  gatekeeper_approved?: unknown;
  gatekeeper_reason?: unknown;
  hash?: unknown;
  /** live_enablement / hitl_policy fields (#2779). */
  actor?: unknown;
  action?: unknown;
  switch_id?: unknown;
  old_policy?: unknown;
  new_policy?: unknown;
  /** #3181: settings_change carries a JSON `changes` string of [{"key","from","to"}, …]. */
  changes?: unknown;
  /** hitl_execution fields (#2811). */
  branch?: unknown;
  order_value?: unknown;
  reason?: unknown;
  approval_id?: unknown;
}

const pct = (v: unknown) =>
  typeof v === "number" && Number.isFinite(v) ? `${Math.round(v * 100)}%` : "—";

const str = (v: unknown) => (typeof v === "string" ? v : undefined);

/** #2779: which policy keys actually changed. Unchanged keys are noise in an audit row — naming
 *  only the flips is what makes the entry readable, and it is the fact a reviewer is after. */
function changedPolicyKeys(oldP: unknown, newP: unknown): string[] {
  if (!oldP || !newP || typeof oldP !== "object" || typeof newP !== "object") return [];
  const a = oldP as Record<string, unknown>;
  const b = newP as Record<string, unknown>;
  return [...new Set([...Object.keys(a), ...Object.keys(b)])]
    .filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k]))
    .sort();
}

/** #3181: the changed registry keys inside a settings_change record's `changes` JSON text
 *  ([{"key","from","to"}, …]). Naming the flipped keys is the fact a reviewer is after. */
function settingsChangeKeys(changes: unknown): string[] {
  const s = str(changes);
  if (!s) return [];
  try {
    const arr = JSON.parse(s);
    if (Array.isArray(arr)) {
      return arr
        .map((c) => (c && typeof c === "object" ? str((c as Record<string, unknown>).key) : undefined))
        .filter((k): k is string => !!k);
    }
  } catch {
    /* not JSON — fall through to no keys */
  }
  return [];
}

/** #3181: event_type classes the engine writes that are neither senate decisions nor the
 *  live/policy/execution kinds handled in toEvent. Each gets an honest kind + label so a known
 *  record can never render as "Unrecognised / Unknown" (an incomplete WORM-view). The generic
 *  fallback in toEvent stays only for genuinely future, unseen types. */
const KNOWN_EVENTS: Record<string, { kind: AuditKind; label: string }> = {
  settings_change: { kind: "settings", label: "Settings changed" },
  trading_settings_seal: { kind: "settings", label: "Trading settings sealed" },
  settings_deviation_ack: { kind: "settings", label: "Settings deviation acknowledged" },
  trading_control: { kind: "settings", label: "Trading control changed" },
  portfolio_shape_change: { kind: "settings", label: "Portfolio shape changed" },
  strategy_swap: { kind: "settings", label: "Strategy swapped" },
  circuit_breaker: { kind: "risk", label: "Circuit breaker" },
  portfolio_stop_loss: { kind: "risk", label: "Portfolio stop-loss" },
  recovery: { kind: "system", label: "Recovery" },
  unlock: { kind: "system", label: "Unlocked" },
  manual_order_cancel: { kind: "system", label: "Order cancelled" },
  performance_metric: { kind: "system", label: "Performance metric" },
  warning: { kind: "system", label: "Warning" },
  compliance_check: { kind: "compliance", label: "Compliance check" },
  eula_acceptance: { kind: "compliance", label: "Terms accepted" },
};

function toEvent(raw: RawAuditEntry): AuditEvent | null {
  const iso = str(raw.timestamp);
  if (!iso) return null;
  const ts = new Date(iso);
  if (Number.isNaN(ts.getTime())) return null; // skip undatable rows

  const hash = str(raw.hash) ?? "";
  const symbol = str(raw.symbol);

  // ── #2779: discriminate on event_type BEFORE any senate heuristic ──────────────────────────
  // These records never passed the risk gatekeeper, so the "missing gatekeeper_approved means
  // approved" rule below must not reach them. The Art-14 acknowledgment text is deliberately NOT
  // rendered: it is a paragraph, and a table row is not where it belongs — its presence is implied
  // by the kind, and the full record stays on disk and in the chain.
  const eventType = str(raw.event_type);
  if (eventType === "live_enablement") {
    const enabling = str(raw.action) !== "disable";
    const actor = str(raw.actor) || "unknown actor";
    const sw = str(raw.switch_id);
    const tail = sw ? ` · switch ${sw.slice(0, 8)}` : "";
    return {
      ts,
      kind: enabling ? "live_enable" : "live_disable",
      symbol: undefined, // these records are account-wide, not per-symbol
      message: enabling
        ? `LIVE trading enabled by ${actor}${tail}`
        : `LIVE trading revoked by ${actor}${tail}`,
      hash,
    };
  }
  if (eventType === "hitl_policy") {
    const keys = changedPolicyKeys(raw.old_policy, raw.new_policy);
    const actor = str(raw.actor) || "unknown actor";
    return {
      ts,
      kind: "policy",
      symbol: undefined,
      message: keys.length
        ? `Approval policy changed by ${actor}: ${keys.join(", ")}`
        : `Approval policy re-applied by ${actor} (no effective change)`,
      hash,
    };
  }
  if (eventType === "hitl_execution") {
    // #2811: the per-order execution decision — 10,589 records in this machine's chain
    // (99.5 % of typed entries), every one rendered "Unrecognised" until now. Kind maps
    // from `branch` (hitl_gate.py:360/375/430/476, order_executor.py:178); the honesty
    // rule (see AuditKind) demands money movement be visible at a glance.
    const branch = str(raw.branch) ?? "";
    const value =
      typeof raw.order_value === "number" && Number.isFinite(raw.order_value)
        ? raw.order_value
        : 0;
    const action = (str(raw.action) ?? "").toUpperCase();
    const reason = str(raw.reason) ?? "";
    if (branch === "under_limit" || branch === "risk_off_exempt") {
      const why =
        branch === "risk_off_exempt"
          ? "risk-off exempt"
          : "under approval limit";
      return {
        ts,
        kind: "execution",
        symbol,
        message: `${action} $${value.toFixed(2)} submitted autonomously (${why})`,
        hash,
      };
    }
    if (branch === "queued") {
      return {
        ts,
        kind: "pending",
        symbol,
        message: `${action} $${value.toFixed(2)} queued for approval`,
        hash,
      };
    }
    if (branch === "skipped") {
      return {
        ts,
        kind: "skipped",
        symbol,
        message: reason || `${action} approved but not submitted`,
        hash,
      };
    }
    // A branch this build has not met: NEVER read as "nothing happened" — an unseen
    // branch could be a new execution path. Surface it verbatim, like unknown events.
    return {
      ts,
      kind: "unknown",
      symbol,
      message: `Unrecognised hitl_execution branch: ${branch || "(none)"}${reason ? ` — ${reason}` : ""}`,
      hash,
    };
  }
  // #3181: a known non-senate class (settings/risk/compliance/system). Give it an honest kind +
  // label so the WORM-view is complete; name the changed keys (settings_change) or the reason.
  const known = eventType ? KNOWN_EVENTS[eventType] : undefined;
  if (known) {
    const actor = str(raw.actor);
    const reason = str(raw.reason);
    const keys = settingsChangeKeys(raw.changes);
    const detail = keys.length ? `: ${keys.join(", ")}` : reason ? ` — ${reason}` : "";
    const by = actor ? ` by ${actor}` : "";
    return { ts, kind: known.kind, symbol, message: `${known.label}${by}${detail}`, hash };
  }
  if (eventType && eventType !== "decision") {
    // An event kind this build has not met. Surface it verbatim rather than dropping it or letting
    // it fall through into the senate branches, where it would read as an approved decision.
    return { ts, kind: "unknown", symbol, message: `Unrecognised audit event: ${eventType}`, hash };
  }
  const action = (str(raw.signal_action) ?? "").toUpperCase();
  const consensus = pct(raw.consensus_score);

  // gatekeeper_approved === false is the only blocked state; treat missing as
  // approved (the engine only writes the field when it ran the gate).
  if (raw.gatekeeper_approved === false) {
    return {
      ts,
      kind: "rejection",
      symbol,
      message: str(raw.gatekeeper_reason)?.trim() || "Blocked by risk gatekeeper",
      hash,
    };
  }
  if (action === "BUY" || action === "SELL") {
    return { ts, kind: "approval", symbol, message: `${action} · ${consensus} consensus`, hash };
  }
  return { ts, kind: "decision", symbol, message: `Hold · ${consensus} consensus`, hash };
}

/**
 * Map raw audit-log lines (oldest→newest, as the Electron reader returns them)
 * to AuditEvents, newest-first for the feed. Junk/undatable rows are dropped.
 */
export function adaptAuditEntries(raw: unknown): AuditEvent[] {
  if (!Array.isArray(raw)) return [];
  const out: AuditEvent[] = [];
  for (const r of raw) {
    if (r && typeof r === "object") {
      const ev = toEvent(r as RawAuditEntry);
      if (ev) out.push(ev);
    }
  }
  return out.reverse(); // newest-first
}
