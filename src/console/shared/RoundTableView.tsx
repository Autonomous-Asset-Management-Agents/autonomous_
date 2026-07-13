import { useState } from "react";
import type { ConsoleRoundTableDecision, RoundTableSenator } from "@/console/live/roundTable";
import { agentInfo } from "@/console/live/agentGlossary";

/**
 * Round-Table verdict views (G3c, #1050) — ported from the bundle's SenateView
 * under main's Round-Table nomenclature (no "senate" vocabulary on main).
 */

/** Vote-tally bar: BULL (buy) / ABSTAIN (hold) / BEAR (sell). */
export function RoundTableBar({
  votesFor,
  votesAbstain,
  votesAgainst,
}: {
  votesFor: number;
  votesAbstain: number;
  votesAgainst: number;
}) {
  const total = votesFor + votesAbstain + votesAgainst;
  if (total <= 0) return null;
  const f = (votesFor / total) * 100;
  const a = (votesAbstain / total) * 100;
  const ag = (votesAgainst / total) * 100;
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-1.5 rounded-full overflow-hidden flex" style={{ background: "rgba(255,255,255,0.04)" }}>
        <div style={{ width: `${f}%`, background: "linear-gradient(90deg, #00c27a, #00c27a)" }} />
        <div style={{ width: `${a}%`, background: "rgba(255,255,255,0.18)" }} />
        <div style={{ width: `${ag}%`, background: "linear-gradient(90deg, #ff453a, #b8312a)" }} />
      </div>
      <div className="num text-[10px] flex gap-2 tabular-nums">
        <span className="text-bull">{votesFor}</span>
        <span className="text-white/30">{votesAbstain}</span>
        <span className="text-bear">{votesAgainst}</span>
      </div>
    </div>
  );
}

/** Signed conviction meter; score in [-1, 1]. */
export function ConvictionMeter({ score }: { score: number }) {
  const left = ((score + 1) / 2) * 100;
  const color = score > 0.15 ? "#00c27a" : score < -0.15 ? "#ff453a" : "rgba(255,255,255,0.6)";
  return (
    <div className="relative h-1.5 w-full rounded-full overflow-visible" style={{ background: "rgba(255,255,255,0.04)" }}>
      <div className="absolute inset-y-0 left-1/2 w-px bg-white/16" />
      <div
        className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full"
        style={{ left: `calc(${left}% - 5px)`, background: color, boxShadow: `0 0 0 4px ${color}33, 0 0 8px ${color}66` }}
      />
    </div>
  );
}

const tickFor = (vote: RoundTableSenator["vote"]) =>
  vote === "BULL" ? { sym: "✓", className: "text-bull" } :
  vote === "BEAR" ? { sym: "×", className: "text-bear" } :
                    { sym: "·", className: "text-white/30" };

/** Per-agent vote rows for one decision. */
/** One agent's vote row; the agent name is a link that reveals its profile
 *  (role / tasks / decision criteria) — deterministic, never generated. */
function AgentRow({ s }: { s: RoundTableSenator }) {
  const [open, setOpen] = useState(false);
  const tick = tickFor(s.vote);
  const a = agentInfo(s.name);
  return (
    <div className="text-[11.5px]">
      <div className="flex items-baseline gap-2">
        <span className={`text-[12px] w-3.5 shrink-0 ${tick.className}`} aria-hidden>{tick.sym}</span>
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="text-white/80 hover:text-[#00c27a] underline decoration-dotted decoration-white/25 underline-offset-2 shrink-0 text-left"
        >
          {a.label}
        </button>
        {s.hardVeto && <span className="text-bear text-[9px] shrink-0">VETO</span>}
        <span className="text-white/40 num leading-snug truncate min-w-0 flex-1">{s.reasoning}</span>
        <span className="num text-white/35 shrink-0">{s.conviction.toFixed(2)}</span>
      </div>
      {open && (
        <div className="mt-1.5 ml-5 mb-2 text-[11px] bg-white/[0.03] rounded-md px-3 py-2.5 space-y-1.5 leading-snug">
          {([["Role", a.role], ["Tasks", a.tasks], ["Criteria", a.criteria]] as const).map(([k, v]) => (
            <div key={k}>
              <span className="text-white/30 uppercase tracking-wider text-[9px]">{k}</span>
              <span className="text-white/60"> · {v}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function RoundTableSenators({ senators }: { senators: RoundTableSenator[] }) {
  if (senators.length === 0) return null;
  return (
    <div className="space-y-1">
      {senators.map((s) => (
        <AgentRow key={s.name} s={s} />
      ))}
    </div>
  );
}

/** Compact one-line summary for a decision in a list. */
export function RoundTableDecisionRow({ d }: { d: ConsoleRoundTableDecision }) {
  return (
    <div className="surface-flat rounded-xl px-4 py-3 space-y-2.5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="font-bold tracking-tight2 text-white/92 text-[14px]">{d.symbol}</span>
          <span className={`text-[11px] num ${d.action === "BUY" ? "text-bull" : d.action === "SELL" ? "text-bear" : "text-white/55"}`}>{d.action}</span>
          {d.sector && <span className="text-white/30 text-[11px]">{d.sector}</span>}
        </div>
        <span className="num text-white/30 text-[10px]">{d.ts}</span>
      </div>
      <div className="w-56"><ConvictionMeter score={d.conviction} /></div>
      <RoundTableBar votesFor={d.votesFor} votesAbstain={d.votesAbstain} votesAgainst={d.votesAgainst} />
      {d.vetoReason && <div className="text-bear text-[11px]">{d.vetoReason}</div>}
      <RoundTableSenators senators={d.senators} />
    </div>
  );
}
