import { useState } from "react";
import { useRoundTablePolling } from "@/console/live/useRoundTablePolling";
import { useStore } from "@/console/store/useStore";
import { RoundTableBar, ConvictionMeter, RoundTableSenators } from "@/console/shared/RoundTableView";
import { IconLightbulb } from "@/console/shared/Icons";
import { StatusDot } from "@/console/shared/StatusDot";
import { getCompanyName } from "@/console/lib/companyName";
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

/**
 * RQ-1 (#1516): the FINAL execution-gate outcome (Iron-Dome / risk / kill-switch) —
 * the last gate before the broker. Makes "approved verdict != actually traded"
 * visible: a gatekeeper-approved BUY can still be blocked/resized here. Returns null
 * for HOLD / no-outcome so nothing renders. Codes come from the engine verbatim.
 */
function outcomeBadge(code?: string | null): { label: string; tone: "on" | "off" | "neutral" } | null {
  switch (code) {
    case "executed":
      return { label: "Executed", tone: "on" };
    case "resized":
      return { label: "Resized ↓", tone: "neutral" };
    case "blocked:order_value":
      return { label: "Blocked · order-value", tone: "off" };
    case "blocked:daily_limit":
      return { label: "Blocked · daily limit", tone: "off" };
    case "blocked:kill_switch":
      return { label: "Halted", tone: "off" };
    case "blocked:risk":
      return { label: "Blocked · risk sizing", tone: "off" };
    case "blocked:churn":
      return { label: "Blocked · anti-churn", tone: "off" };
    case "blocked:portfolio":
      return { label: "Blocked · portfolio", tone: "off" };
    case "hitl_held":
      return { label: "Awaiting approval", tone: "neutral" };
    case "pending":
      return { label: "Pending", tone: "neutral" };
    default:
      return code ? { label: code, tone: "neutral" } : null;
  }
}

function DecisionCard({ d }: { d: ConsoleRoundTableDecision }) {
  const [open, setOpen] = useState(false);
  const outcome = outcomeBadge(d.executionOutcome);
  const name = getCompanyName(d.symbol);

  return (
    <div className="surface-flat rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-white/[0.02] transition-colors"
      >
        <span className="text-white/30 text-[11px] w-3">{open ? "▾" : "▸"}</span>
        <span className="w-32 min-w-0">
          <span className="block font-bold tracking-tight2 text-white/92 text-[14px] truncate">{d.symbol}</span>
          {name && <span className="block text-[10px] font-normal text-white/40 truncate leading-tight">{name}</span>}
        </span>
        <span className={`text-[12px] num font-semibold w-12 ${actionClass(d.action)}`}>{d.action}</span>
        {outcome && (
          <span
            className="whitespace-nowrap"
            title={
              d.executionOutcomeReason ||
              `Execution gate (Iron Dome / risk / kill-switch): ${d.executionOutcome}`
            }
          >
            <StatusDot tone={outcome.tone} className="!text-[9px]">
              {outcome.label}
            </StatusDot>
          </span>
        )}
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
    <div className="px-8 py-7 space-y-6 max-w-[1100px]">
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
      </div>

      {/* RQ-1 (#1516): verdict explainer. The action is a WEIGHTED vote, not the raw
          head-count shown in the vote bar — a high-weight agent can carry BUY/SELL even
          against a HOLD majority. Static: always visible, independent of the list state. */}
      <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
        <IconLightbulb width={16} height={16} className="text-white/70 mt-1 shrink-0" />
        <div className="text-[13.5px] text-white/70 leading-relaxed space-y-2">
          <p>
            <span className="font-semibold text-white/90">How a verdict is reached.</span>{" "}
            The action is a risk-weighted vote of the agents — not a simple head-count. A
            high-conviction, heavily-weighted agent can carry the verdict to BUY or SELL even
            when most agents read HOLD.
          </p>
          <p className="text-[13px] text-white/45">
            <span className="text-white/60">Tip:</span> Expand any card to see every agent's vote; click an agent's name for its role, tasks and criteria.
          </p>
        </div>
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
