/**
 * Audit-chain adapter (G3d-2, #1050). Maps the engine's raw hash-linked
 * audit-log lines — the dicts LocalJSONAuditLogger._async_log_to_jsonl
 * (core/round_table/senate_log.py) appends to `audit_log_<date>.jsonl` — into
 * console AuditEvents. Each line is one SenateSession that passed (or was
 * blocked at) the risk gatekeeper.
 *
 * Honesty rule: derive only the kinds the contract actually carries. The bundle
 * UI had eight event kinds (fill/regime/guard/…); main's audit log records
 * senate decisions with a gatekeeper verdict only, so we emit exactly three —
 * approval, rejection, decision — and never fabricate the rest.
 */

/** Display kinds derivable from a senate audit entry. */
export type AuditKind = "approval" | "rejection" | "decision";

export interface AuditEvent {
  ts: Date;
  kind: AuditKind;
  symbol?: string;
  message: string;
  /** Full sha256 chain hash for the entry (integrity value, shown truncated). */
  hash: string;
}

/** The subset of senate_log.py's entry the console reads. */
interface RawAuditEntry {
  symbol?: unknown;
  timestamp?: unknown;
  signal_action?: unknown;
  consensus_score?: unknown;
  gatekeeper_approved?: unknown;
  gatekeeper_reason?: unknown;
  hash?: unknown;
}

const pct = (v: unknown) =>
  typeof v === "number" && Number.isFinite(v) ? `${Math.round(v * 100)}%` : "—";

const str = (v: unknown) => (typeof v === "string" ? v : undefined);

function toEvent(raw: RawAuditEntry): AuditEvent | null {
  const iso = str(raw.timestamp);
  if (!iso) return null;
  const ts = new Date(iso);
  if (Number.isNaN(ts.getTime())) return null; // skip undatable rows

  const hash = str(raw.hash) ?? "";
  const symbol = str(raw.symbol);
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
