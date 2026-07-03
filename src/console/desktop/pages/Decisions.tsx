import { useState } from "react";
import { useRoundTablePolling } from "@/console/live/useRoundTablePolling";
import { useStore } from "@/console/store/useStore";
import { RoundTableBar, ConvictionMeter, RoundTableSenators } from "@/console/shared/RoundTableView";
import type { ConsoleRoundTableDecision } from "@/console/live/roundTable";

/**
 * Decisions page (#1435). The live Round-Table verdicts (BUY/SELL/HOLD), each a
 * compact, collapsible card so the list stays scannable. Expanding shows the
 * agents' raw per-decision read (terse). The *why* is generated on demand by the
 * XAI-1 glass-box (#569) via /chat — a per-decision "Erklären" button — instead
 * of a static, redundant frontend description.
 *
 * Source marker: every decision today is autonomous (no human-in-the-loop path —
 * GAP2). "HITL" renders only for a decision that carries `source: "hitl"`; never
 * fabricated.
 */
const FILTERS = ["All", "BUY", "SELL", "HOLD"] as const;
type Filter = (typeof FILTERS)[number];

const actionClass = (a: string) => (a === "BUY" ? "text-bull" : a === "SELL" ? "text-bear" : "text-white/55");
const sourceLabel = (d: ConsoleRoundTableDecision) => (d.source === "hitl" ? "HITL" : "Autonom");

function DecisionCard({ d }: { d: ConsoleRoundTableDecision }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="surface-flat rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-white/[0.02] transition-colors"
      >
        <span className="text-white/30 text-[11px] w-3">{open ? "▾" : "▸"}</span>
        <span className="font-bold tracking-tight2 text-white/92 text-[14px] w-16">{d.symbol}</span>
        <span className={`text-[12px] num font-semibold w-12 ${actionClass(d.action)}`}>{d.action}</span>
        <div className="w-36 hidden sm:block">
          <RoundTableBar votesFor={d.votesFor} votesAbstain={d.votesAbstain} votesAgainst={d.votesAgainst} />
        </div>
        <span className="text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-white/[0.06] text-white/45 ml-auto">
          {sourceLabel(d)}
        </span>
        <span className="num text-white/30 text-[10px] w-10 text-right">{d.ts}</span>
      </button>

      {open && (
        <div className="px-4 pb-3.5 pt-1 space-y-3 border-t border-white/5">
          <div className="w-56">
            <ConvictionMeter score={d.conviction} />
          </div>
          {d.vetoReason && <div className="text-bear text-[11px]">{d.vetoReason}</div>}
          <RoundTableSenators senators={d.senators} />
        </div>
      )}
    </div>
  );
}

export function Decisions() {
  useRoundTablePolling();
  const roundTable = useStore((s) => s.roundTable);
  const [filter, setFilter] = useState<Filter>("All");
  const shown = filter === "All" ? roundTable : roundTable.filter((d) => d.action === filter);

  return (
    <div className="px-8 py-7 space-y-6 max-w-[1000px]">
      <div>
        <div className="eyebrow mb-2">Round-Table decisions</div>
        <div className="flex items-baseline gap-6 flex-wrap">
          <h1 className="text-[26px] font-bold tracking-tight2 text-white/92">{roundTable.length} decisions</h1>
          <div className="flex gap-1.5">
            {FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`text-[11px] px-2.5 py-1 rounded-md border transition-colors ${
                  f === filter
                    ? "border-transparent text-[#00c27a] bg-[#00c27a]/12"
                    : "border-white/5 text-white/55 hover:text-white/92 hover:border-white/15"
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
        <p className="text-white/45 text-[13px] mt-1.5">
          Each verdict — expand to see every agent's vote; click an agent's name for its role, tasks and criteria.
        </p>
      </div>

      {shown.length === 0 ? (
        <div className="surface px-8 py-14 text-center text-white/35 text-[13px]">
          No decisions yet — the round table runs once the strategy is live.
        </div>
      ) : (
        <div className="space-y-2">
          {shown.map((d) => (
            <DecisionCard key={`${d.symbol}-${d.ts}`} d={d} />
          ))}
        </div>
      )}
    </div>
  );
}
