import type { RoundTableDecisionsResponse } from "@/lib/api";
import { fmtTime } from "@/console/lib/format";

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
  /** The agent's vote weight in the consensus average (0 = abstains/dormant,
   *  excluded from Σweight). RPT-GOLD Wave 2 (#2732) — the transparency figure
   *  behind `consensus = Σ(score×weight) / Σ(weight)` (consensus.py:265-271). */
  weight: number;
  reasoning: string;
  hardVeto: boolean;
  /** #3084/#3094 audit role in the DIRECTION mean: "directional" (counted) ·
   *  "conditioner"/"veto_guard" (base non-directional) · "sizer" (VIXAware when
   *  its IV is redirected to the size-scaler). Drives the Direction-vs-Sizing
   *  split in the console. Absent on very old records → treated as "directional",
   *  reconciled against the served consensus by `partitionRoundTable`. */
  role?: string;
}

export interface ConsoleRoundTableDecision {
  symbol: string;
  action: "BUY" | "SELL" | "HOLD" | string;
  passed: boolean;
  /** Signed conviction in [-1, 1] for the meter (weighted_score rebased around 0.5). */
  conviction: number;
  /** Raw consensus score in [0, 1] for the honest "N% consensus" label — 0.72 → "72%", NOT the
   *  meter's rebased 0.44. Missing → 0.5 (neutral). Kept separate so the meter stays unchanged. */
  consensusScore: number;
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

// Engine timestamps are UTC ISO ("2026-06-12T10:00:00+00:00"). Render in the
// user's LOCAL timezone via the shared fmtTime helper — a raw slice(11,16) showed
// UTC on the Decisions page while every other view (Audit Chain, Reports, Overview)
// localized, so the same event appeared at two different clock times (live find
// 2026-07-24). A non-ISO/legacy or unparseable value passes through unchanged.
function shortTime(ts: string): string {
  if (!ts) return ts;
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : fmtTime(d);
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
      consensusScore: clamp(typeof d.consensus_score === "number" ? d.consensus_score : 0.5, 0, 1),
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
        weight: typeof v.weight === "number" ? v.weight : 0,
        reasoning: v.reasoning ?? "",
        hardVeto: !!v.vetoed,
        role: typeof v.role === "string" ? v.role : undefined,
      })),
      // RQ-1 (#1516): the final execution-gate outcome, joined server-side. Absent
      // (HOLD / older engine) → undefined, and the badge renders nothing.
      executionOutcome: d.execution_outcome ?? undefined,
      executionOutcomeReason: d.execution_outcome_reason ?? undefined,
    };
  });
}

/** Roles that DON'T enter the directional consensus mean — mirrors the engine's
 *  `consensus_exclusions()` (base conditioners + the IV-driven sizer). */
const NON_DIRECTIONAL_ROLES = new Set(["conditioner", "veto_guard", "sizer"]);

export interface RoundTablePartition {
  /** Voters counted in the direction mean — reproduces the served consensus. */
  direction: RoundTableSenator[];
  /** Not in the vote: the IV sizer + the base conditioners/veto-guard. */
  sizingRisk: RoundTableSenator[];
  /** Directional agents that are dormant this cycle (weight 0 → no vote). */
  dormant: RoundTableSenator[];
  /** Σ(conviction×weight) and Σweight over `direction` (the transparency figures). */
  wSum: number;
  wTot: number;
}

function weightedSums(arr: RoundTableSenator[]): { wSum: number; wTot: number; mean: number | null } {
  const wSum = arr.reduce((a, s) => a + s.conviction * s.weight, 0);
  const wTot = arr.reduce((a, s) => a + s.weight, 0);
  return { wSum, wTot, mean: wTot > 0 ? wSum / wTot : null };
}

/**
 * Split the board into Direction (counted in the vote) vs Sizing & Risk (not) vs
 * dormant, anchored to the authoritative served `consensusScore`.
 *
 * Primary signal is each senator's `role` (the engine tags the IV-driven VIXAware
 * as "sizer" and the base agents as "conditioner"/"veto_guard"). But records
 * written before the role fix (#3094) still tag a runtime-excluded sizer as
 * "directional" with a real weight — so if the direction mean does not reproduce
 * the served consensus, the single weight>0 member whose removal DOES reproduce it
 * is the sizer, and it is moved to Sizing & Risk. This keeps the console honest for
 * both old and new record shapes without duplicating the gate's flag logic.
 */
export function partitionRoundTable(
  senators: RoundTableSenator[],
  consensusScore?: number,
): RoundTablePartition {
  const EPS = 0.005; // 0.5pp — float/rounding slack vs the served score
  const isNonDir = (s: RoundTableSenator) => NON_DIRECTIONAL_ROLES.has(s.role ?? "directional");

  let direction = senators.filter((s) => s.weight > 0 && !isNonDir(s));
  let sizingRisk = senators.filter((s) => isNonDir(s));
  const dormant = senators.filter((s) => s.weight === 0 && !isNonDir(s));

  // Anchor to the served consensus (old-record reconciliation, see docstring).
  if (typeof consensusScore === "number") {
    const m = weightedSums(direction).mean;
    if (m == null || Math.abs(m - consensusScore) > EPS) {
      const culprit = direction.find((x) => {
        const rest = direction.filter((y) => y !== x);
        const rm = weightedSums(rest).mean;
        return rm != null && Math.abs(rm - consensusScore) <= EPS;
      });
      if (culprit) {
        direction = direction.filter((y) => y !== culprit);
        sizingRisk = [culprit, ...sizingRisk];
      }
    }
  }

  const { wSum, wTot } = weightedSums(direction);
  return { direction, sizingRisk, dormant, wSum, wTot };
}
