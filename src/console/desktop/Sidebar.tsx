import { useStore, type ConsolePage } from "@/console/store/useStore";
import {
  IconDashboard, IconQueue, IconPositions, IconReports, IconAudit, IconSettings, IconChat, IconChevronRight, IconBolt,
} from "@/console/shared/Icons";
import { useEngine } from "@/console/live/useEngine";
import { useHealthPolling } from "@/console/live/useHealthPolling";
import { tradingLabel } from "@/console/live/trading";

/**
 * Console sidebar (#1050) — faithful UX re-port of the bundle nav rail: the chat
 * launcher + page nav (unchanged) plus the system-status footer. The footer shows
 * the live engine state (useEngine), an "Agents" count (specialist reports +
 * senate members, from the store; "—" when none are active yet), and a "Live
 * Trading" state from /health's strategy_running ("Idle" when the loop isn't
 * running, "Paper" while paper-trading, "Live" in red for live trading). The
 * Decisions pending-count badge returns with the HITL endpoint (GAP2).
 */
const items: { id: ConsolePage; label: string; Icon: typeof IconDashboard }[] = [
  { id: "overview", label: "Overview", Icon: IconDashboard },
  { id: "decisions", label: "Decisions", Icon: IconQueue },
  { id: "positions", label: "Positions", Icon: IconPositions },
  { id: "reports", label: "Reports", Icon: IconReports },
  { id: "simulation", label: "Simulation", Icon: IconBolt },
  { id: "audit", label: "Audit chain", Icon: IconAudit },
  { id: "settings", label: "Settings", Icon: IconSettings },
];

function StatRow({ label, value, valueClass, title }: { label: string; value: string; valueClass?: string; title?: string }) {
  return (
    <div className="flex justify-between gap-2" title={title}>
      <span>{label}</span>
      <span className={`min-w-0 truncate text-right ${valueClass ?? "text-white/55"}`}>{value}</span>
    </div>
  );
}

export function Sidebar() {
  const desktopPage = useStore((s) => s.desktopPage);
  const setDesktopPage = useStore((s) => s.setDesktopPage);
  const engine = useEngine();
  useHealthPolling();
  const specialistReports = useStore((s) => s.specialistReports);
  const roundTable = useStore((s) => s.roundTable);
  const strategyRunning = useStore((s) => s.strategyRunning);
  // "unavailable" is the cloud/browser preview (no shell) — show it as running,
  // matching the bundle's static browser label.
  const online = engine.status === "running" || engine.status === "unavailable";

  // Agents = active specialist reports + the senate roster (same across decisions).
  const agentCount = specialistReports.length + (roundTable[0]?.senators.length ?? 0);
  const agents = agentCount > 0 ? String(agentCount) : "—";
  // OSS desktop is paper-only → isLive is false; the live edition wires it.
  const trading = tradingLabel(strategyRunning, false);

  return (
    <aside className="w-[210px] shrink-0 flex flex-col bg-black/40 border-r border-white/5">
      {/* Chat / system-communication launcher (the brand mark lives in the title bar). */}
      <div className="px-3 pt-4 pb-3">
        <button
          onClick={() => setDesktopPage("chat")}
          className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-[12.5px] transition-all ${
            desktopPage === "chat"
              ? "bg-[#00c27a]/12 text-[#00c27a] border border-[#00c27a]/25"
              : "bg-white/[0.04] text-white/80 border border-white/10 hover:bg-white/[0.07] hover:text-white/92"
          }`}
          title="System communication"
        >
          <IconChat width={15} height={15} />
          <span className="flex-1 text-left font-medium">Chat</span>
          <span aria-hidden="true" className="text-[8px] font-semibold tracking-wider px-1 py-px rounded bg-white/10 text-white/55 mr-0.5">BETA</span>
          <IconChevronRight width={11} height={11} className="opacity-40" />
        </button>
      </div>

      <div className="hairline mx-4" />

      <nav className="flex-1 px-3 py-3 space-y-0.5">
        {items.map(({ id, label, Icon }) => {
          const active = desktopPage === id;
          return (
            <button
              key={id}
              onClick={() => setDesktopPage(id)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-[12.5px] transition-all ${
                active
                  ? "bg-white/8 text-white/92 border border-white/10"
                  : "text-white/55 hover:text-white/92 hover:bg-white/4 border border-transparent"
              }`}
            >
              <Icon width={14} height={14} className={active ? "text-white/92" : ""} />
              <span className="flex-1 text-left">{label}</span>
            </button>
          );
        })}
      </nav>

      <div className="hairline mx-4" />

      {/* System-status footer */}
      <div className="p-4 space-y-2.5">
        <div className="flex items-center gap-2">
          {online ? (
            <span className="live-dot" />
          ) : (
            <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${engine.status === "error" ? "bg-[#ff453a]" : "bg-white/25"}`} />
          )}
          <span className="text-[10px] text-white/55 tracking-wider uppercase">
            {online ? "Engine running" : engine.status === "error" ? "Engine error" : "Engine offline"}
          </span>
        </div>
        <div className="space-y-1 text-[10px] num text-white/30">
          {/* C-D: a tooltip turns the ambiguous "—" into "not started yet", not "broken". */}
          <StatRow
            label="Agents"
            value={agents}
            title={agents === "—" ? "No specialist agents are active yet — they appear once the engine evaluates the market." : undefined}
          />
          <StatRow
            label="Live Trading"
            value={trading.text}
            valueClass={trading.live ? "text-[#ff453a]" : "text-white/55"}
            title={trading.text === "—" ? "Waiting for the engine — trading status appears once it is running (paper by default)." : undefined}
          />
        </div>
        {/* In-app legal — bundled /legal/* routes (offline-safe; GTM-1 T2 #1465) */}
        <div className="flex flex-wrap items-center gap-x-1.5 text-[9px] text-white/25 pt-1">
          <a href="/legal/imprint" className="hover:text-white/50">Imprint</a>
          <span>·</span>
          <a href="/legal/privacy" className="hover:text-white/50">Privacy</a>
          <span>·</span>
          <a href="/legal/risk-disclosure" className="hover:text-white/50">Risk</a>
        </div>
      </div>
    </aside>
  );
}
