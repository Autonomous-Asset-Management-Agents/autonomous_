import type { RoundTableDecisionsResponse } from "@/lib/api";

/**
 * Round-Table adapter (G3c, #1050). Maps the engine's /round-table-decisions
 * DTO (G1b) to the console display shape, under main's Round-Table nomenclature
 * (the bundle's "senate" vocabulary is NOT carried over — see ADR/AGENTS.md).
 *
 * Vote vocabulary: the engine emits BUY/SELL/HOLD; the console renders
 * BULL/BEAR/ABSTAIN (the round-table verdict colours).
 */
export type RTVote = "BULL" | "BEAR" | "ABSTAIN";

export interface RoundTableSenator {
  name: string;
  vote: RTVote;
  conviction: number;
  reasoning: string;
  hardVeto: boolean;
}

export interface ConsoleRoundTableDecision {
  symbol: string;
  action: "BUY" | "SELL" | "HOLD" | string;
  passed: boolean;
  /** Signed conviction in [-1, 1] for the meter (weighted_score rebased around 0.5). */
  conviction: number;
  sector: string;
  votesFor: number; // BUY
  votesAbstain: number; // HOLD
  votesAgainst: number; // SELL
  vetoReason: string;
  ts: string;
  senators: RoundTableSenator[];
  /** Decision source. Today always autonomous (no human-in-the-loop path exists
   *  yet — GAP2); set to "hitl" ONLY once a real HITL approval lands. The
   *  Decisions page renders the marker from this — never fabricated. */
  source?: "autonomous" | "hitl";
  /** RQ-1 (#1516): the FINAL execution-gate outcome (Iron-Dome / risk / kill-switch).
   *  "executed" | "blocked:*" | "hitl_held" | "pending"; null/undefined for a HOLD
   *  (no order attempted) → the Decisions badge renders nothing. */
  executionOutcome?: string | null;
  executionOutcomeReason?: string;
}

export function actionToVote(signal: string): RTVote {
  if (signal === "BUY") return "BULL";
  if (signal === "SELL") return "BEAR";
  return "ABSTAIN";
}

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

// Engine timestamps are ISO ("2026-06-12T10:00:00+00:00"); show just HH:MM.
// A non-ISO/legacy value passes through unchanged. Deterministic (no locale).
function shortTime(ts: string): string {
  return ts.includes("T") ? ts.slice(11, 16) : ts;
}

export function adaptRoundTableDecisions(
  resp: RoundTableDecisionsResponse | null | undefined,
): ConsoleRoundTableDecision[] {
  const rows = Array.isArray(resp?.decisions) ? resp!.decisions! : [];
  return rows.map((d) => {
    // The engine emits no pre-aggregated tally — derive it from the votes.
    const votes = Array.isArray(d.votes) ? d.votes : [];
    const tally = (sig: string) => votes.filter((v) => v.signal === sig).length;
    return {
      symbol: d.symbol,
      action: d.signal_action ?? "HOLD",
      passed: !!d.gatekeeper_approved,
      // consensus_score is 0..1 (>0.5 = bullish); rebase to a signed [-1,1]
      // meter value. Missing → neutral 0 (not masked as bullish).
      conviction: clamp((typeof d.consensus_score === "number" ? d.consensus_score : 0.5) * 2 - 1, -1, 1),
      sector: "", // not part of the SenateSession DTO
      votesFor: tally("BUY"),
      votesAbstain: tally("HOLD"),
      votesAgainst: tally("SELL"),
      // The gatekeeper reason is only a "veto" when the decision didn't pass.
      vetoReason: d.gatekeeper_approved ? "" : (d.gatekeeper_reason ?? ""),
      ts: shortTime(d.timestamp ?? ""),
      senators: votes.map((v) => ({
        name: v.agent_name ?? v.name ?? "",
        vote: actionToVote((v.signal ?? "").toUpperCase()),
        conviction: typeof v.score === "number" ? v.score : 0,
        reasoning: v.reasoning ?? "",
        hardVeto: !!v.vetoed,
      })),
      // RQ-1 (#1516): the final execution-gate outcome, joined server-side. Absent
      // (HOLD / older engine) → undefined, and the badge renders nothing.
      executionOutcome: d.execution_outcome ?? undefined,
      executionOutcomeReason: d.execution_outcome_reason ?? undefined,
    };
  });
}
