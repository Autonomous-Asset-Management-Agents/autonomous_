import { useStore, type ConsolePage } from "@/console/store/useStore";
import {
  IconDashboard, IconQueue, IconPositions, IconReports, IconAudit, IconSettings, IconChat, IconChevronRight,
} from "@/console/shared/Icons";
import { useEngine } from "@/console/live/useEngine";

/**
 * Console sidebar (#1050) — faithful UX re-port of the bundle nav rail: the
 * chat launcher + page nav (unchanged) plus the bundle's system-status footer
 * (engine state + Specialists / Senate / LLM / GPU). Figures main's engine
 * doesn't expose yet (specialist warmup, senate size, LLM name, GPU info) render
 * an honest "—" rather than the bundle's static demo values; the engine state is
 * wired live through useEngine. The Decisions pending-count badge returns with
 * the HITL endpoint (GAP2).
 */
const items: { id: ConsolePage; label: string; Icon: typeof IconDashboard }[] = [
  { id: "overview", label: "Overview", Icon: IconDashboard },
  { id: "decisions", label: "Decisions", Icon: IconQueue },
  { id: "positions", label: "Positions", Icon: IconPositions },
  { id: "reports", label: "Reports", Icon: IconReports },
  { id: "audit", label: "Audit chain", Icon: IconAudit },
  { id: "settings", label: "Settings", Icon: IconSettings },
];

function StatRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <span>{label}</span>
      <span className="text-white/55 min-w-0 truncate text-right">{value}</span>
    </div>
  );
}

export function Sidebar() {
  const desktopPage = useStore((s) => s.desktopPage);
  const setDesktopPage = useStore((s) => s.setDesktopPage);
  const engine = useEngine();
  // "unavailable" is the cloud/browser preview (no shell) — show it as running,
  // matching the bundle's static browser label.
  const online = engine.status === "running" || engine.status === "unavailable";

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
          <StatRow label="Specialists" value="—" />
          <StatRow label="Senate" value="—" />
          <StatRow label="LLM" value="—" />
          <StatRow label="GPU" value="—" />
        </div>
      </div>
    </aside>
  );
}
