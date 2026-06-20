import type { ConsoleRoundTableDecision, RoundTableSenator } from "@/console/live/roundTable";

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
        <div style={{ width: `${f}%`, background: "linear-gradient(90deg, #1d8d3f, #30d158)" }} />
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
  const color = score > 0.15 ? "#30d158" : score < -0.15 ? "#ff453a" : "rgba(255,255,255,0.6)";
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
export function RoundTableSenators({ senators }: { senators: RoundTableSenator[] }) {
  if (senators.length === 0) return null;
  return (
    <div>
      {senators.map((s) => {
        const tick = tickFor(s.vote);
        return (
          <div
            key={s.name}
            className="grid grid-cols-[16px_64px_1fr_46px] items-start gap-3 py-2 font-mono text-[11.5px] border-b border-white/5 last:border-0"
          >
            <span className={`text-center text-[12px] leading-none ${tick.className}`} aria-hidden>{tick.sym}</span>
            <span className="text-white/92">
              {s.name}
              {s.hardVeto && <span className="text-bear text-[9px] ml-1">VETO</span>}
            </span>
            <span className="text-white/55 leading-snug">{s.reasoning}</span>
            <span className="num text-right text-white/92">{s.conviction.toFixed(2)}</span>
          </div>
        );
      })}
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
