import { useState } from "react";
import { agentInfo } from "@/console/live/agentGlossary";
import { RoundTableBar } from "@/console/shared/RoundTableView";
import {
  humanizeReasoning,
  stripAgentPrefix,
} from "@/console/shared/reasoning";
import {
  partitionRoundTable,
  directionTally,
  type ConsoleRoundTableDecision,
  type RoundTableSenator,
} from "@/console/live/roundTable";

// The DrawdownGuard veto reasoning ("…drawdown=X%…") reads as a plain sentence (raw string
// stays in the audit); mirrors RoundTableView.displayReasoning so both surfaces read the same.
const VETO_RE = /drawdown=([\d.]+)\s*%/i;
function reasoningOf(s: RoundTableSenator): string {
  if (s.hardVeto) {
    const m = VETO_RE.exec(s.reasoning);
    if (m) {
      return `Stock ${Math.round(parseFloat(m[1]))}% below its 30-day high — blocks new buys, not a risk-reducing sell.`;
    }
  }
  return (
    humanizeReasoning(s.name, s.reasoning) ?? stripAgentPrefix(s.reasoning)
  );
}

const voteWord = (v: string) =>
  v === "BULL" ? "BUY" : v === "BEAR" ? "SELL" : "HOLD";
const voteCls = (v: string) =>
  v === "BULL" ? "text-bull" : v === "BEAR" ? "text-bear" : "text-white/55";
const ROLE_LABEL: Record<string, string> = {
  sizer: "sizer",
  conditioner: "conditioner",
  veto_guard: "veto guard",
};
const GRID =
  "grid grid-cols-[minmax(104px,1fr)_40px_42px_44px_52px_minmax(0,1.2fr)] gap-2.5";

/**
 * Shared Round-Table vote breakdown (#3354) — the SINGLE partitioned view rendered by BOTH the
 * Decisions page and the Reports synthesis, so the two surfaces are identical. It fixes the
 * Decisions-only bugs (overlays got directional ✓/×/· glyphs; the tally counted overlays + dark
 * abstains) and the Reports-only bug (raw class names instead of agentGlossary labels), by
 * partitioning role-aware into Direction / Sizing & Risk / Dormant, labelling every row via
 * `agentInfo`, and tallying only the directional voters. Pure/presentational.
 */
export function RoundTableVote({
  decision: d,
}: {
  decision: ConsoleRoundTableDecision;
}) {
  if (!d.senators || d.senators.length === 0) return null;
  const { direction, sizingRisk, dormant, wSum, wTot } = partitionRoundTable(
    d.senators,
    d.consensusScore,
  );
  // Headline = the AUTHORITATIVE consensus the ENGINE served (never a frontend re-compute);
  // fall back to the signed-conviction rebasing only when the raw score is absent (older engine).
  const consensusPct = Math.round(
    (typeof d.consensusScore === "number"
      ? d.consensusScore
      : (d.conviction + 1) / 2) * 100,
  );
  const tally = directionTally(d);
  const actCls =
    d.action === "BUY"
      ? "text-bull"
      : d.action === "SELL"
        ? "text-bear"
        : "text-white/55";

  const headerRow = (
    <div
      className={`${GRID} border-b border-white/10 pb-1.5 num text-[9px] uppercase tracking-wide text-white/35`}
    >
      <span>Agent</span>
      <span>Vote</span>
      <span className="text-right">Score</span>
      <span className="text-right">Weight</span>
      <span className="text-right">Contrib.</span>
      <span>Rationale</span>
    </div>
  );
  return (
    <div className="space-y-3">
      {/* Consensus headline — the weighted DIRECTION vote */}
      <div className="flex items-baseline gap-2.5 flex-wrap">
        <span className={`num text-[22px] font-bold leading-none ${actCls}`}>
          {consensusPct}%
        </span>
        <span className="text-[12px] text-white/55">
          board consensus — the weighted direction vote (
          <span className={`num font-semibold ${actCls}`}>{d.action}</span>)
        </span>
      </div>
      {/* Tally over the DIRECTION set only (not the mixed full-board count) */}
      <RoundTableBar
        votesFor={tally.votesFor}
        votesAbstain={tally.votesAbstain}
        votesAgainst={tally.votesAgainst}
      />
      <div className="num text-[11px] text-white/55">
        <span className="text-bull">{tally.votesFor} buy</span> ·{" "}
        <span className="text-white/45">{tally.votesAbstain} hold</span> ·{" "}
        <span className="text-bear">{tally.votesAgainst} sell</span>
        <span className="text-white/30"> · directional voters only</span>
      </div>

      {/* DIRECTION — the voters counted in the consensus */}
      <div className="mt-1 space-y-1">
        <div className="num text-[10px] uppercase tracking-wide text-white/40">
          Direction — counted in the {consensusPct}%
        </div>
        {headerRow}
        {direction.map((s, i) => (
          <AgentVoteRow key={i} s={s} counted />
        ))}
        <div className="border-t border-white/10 pt-2 num text-[11.5px] leading-relaxed text-white/55">
          consensus = Σ(score × weight){" "}
          <span className="text-white/85">{wSum.toFixed(2)}</span> ÷ Σ weight{" "}
          <span className="text-white/85">{wTot.toFixed(2)}</span> ={" "}
          <span className="text-white/85">{consensusPct}%</span> · thresholds:
          BUY ≥ 65% · SELL ≤ 35% · else HOLD
        </div>
      </div>

      {/* SIZING & RISK — not in the vote (IV sizer + base conditioners/veto-guard) */}
      {sizingRisk.length > 0 && (
        <div className="mt-2 space-y-1">
          <div className="num text-[10px] uppercase tracking-wide text-white/40">
            Sizing &amp; risk — not in the vote{" "}
            <span className="tracking-normal normal-case text-white/30">
              · informs position size, not direction
            </span>
          </div>
          {headerRow}
          {sizingRisk.map((s, i) => (
            <AgentVoteRow key={i} s={s} counted={false} />
          ))}
        </div>
      )}

      {/* Dormant directional agents this cycle (weight 0 → no vote) */}
      {dormant.length > 0 && (
        <div className="num text-[10.5px] text-white/35">
          {dormant.length} dormant this cycle (weight 0 → no vote):{" "}
          {dormant.map((s) => agentInfo(s.name).label).join(", ")}
        </div>
      )}
    </div>
  );
}

/**
 * One agent's row in the partitioned breakdown. The label is a click-to-reveal button
 * (XAI-1 glass box, #569) exposing the agent's role / tasks / decision criteria from the
 * deterministic agentGlossary — never generated. Shared by Decisions and Reports so the
 * glass box is identical on both surfaces (#3354).
 */
function AgentVoteRow({
  s,
  counted,
}: {
  s: RoundTableSenator;
  counted: boolean;
}) {
  const [open, setOpen] = useState(false);
  const a = agentInfo(s.name);
  return (
    <div className={`border-t border-white/6 ${counted ? "" : "opacity-70"}`}>
      <div className={`${GRID} items-start py-1`}>
        <span className="text-[12px] text-white/70 min-w-0">
          <button
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="text-left text-white/80 hover:text-[#00c27a] underline decoration-dotted decoration-white/25 underline-offset-2"
          >
            {a.label}
          </button>
          {!counted && s.role && ROLE_LABEL[s.role] && (
            <span className="ml-1.5 num text-[9px] uppercase tracking-wide text-white/35">
              {ROLE_LABEL[s.role]}
            </span>
          )}
        </span>
        <span
          className={`num text-[11.5px] font-semibold ${counted ? voteCls(s.vote) : "text-white/40"}`}
        >
          {/* #3369: sizing/risk rows carry NO directional vote — their score is a size
              input, not a BUY/SELL call. Render "—" (like weight/contrib already do for
              non-counted rows) so a sizer's BEAR score never shows as a red "SELL" that
              contradicts the "0 sell · directional voters only" tally above. */}
          {counted ? voteWord(s.vote) : "—"}
        </span>
        <span className="num text-right text-[11.5px] text-white/45">
          {s.conviction.toFixed(2)}
        </span>
        <span className="num text-right text-[11.5px] text-white/45">
          {counted ? s.weight.toFixed(2) : "—"}
        </span>
        <span
          className="num text-right text-[11.5px] text-white/60"
          title="score × weight"
        >
          {counted && s.weight > 0 ? (s.conviction * s.weight).toFixed(2) : "—"}
        </span>
        <span className="text-[11.5px] leading-snug text-white/45">
          {reasoningOf(s)}
        </span>
      </div>
      {open && (
        <div className="ml-1 mb-2 mt-0.5 text-[11px] bg-white/[0.03] rounded-md px-3 py-2.5 space-y-1.5 leading-snug">
          {(
            [
              ["Role", a.role],
              ["Tasks", a.tasks],
              ["Criteria", a.criteria],
            ] as const
          ).map(([k, v]) => (
            <div key={k}>
              <span className="text-white/30 uppercase tracking-wider text-[9px]">
                {k}
              </span>
              <span className="text-white/60"> · {v}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
