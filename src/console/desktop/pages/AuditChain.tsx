import { useStore } from "@/console/store/useStore";
import { useAuditPolling } from "@/console/live/useAuditPolling";
import { streamPill } from "@/console/live/health";
import { fmtTime, ago } from "@/console/lib/format";
import { isDesktop } from "@/lib/desktopBridge";
import { IconCheck, IconX, IconQueue, IconLightbulb } from "@/console/shared/Icons";
import { StatusDot } from "@/console/shared/StatusDot";
import type { AuditKind } from "@/console/live/audit";

/**
 * Audit Chain page (G3d-2, #1050). Renders the engine's local hash-linked
 * decision log (audit_log_<date>.jsonl) — one row per senate decision, polled
 * through the desktop bridge. Only the three verdicts the contract carries are
 * shown (approval / rejection / decision); the bundle's richer event kinds have
 * no source on main. Desktop-only: the cloud build has no local file.
 */
const KIND_META: Record<AuditKind, { label: string; color: string; Icon: typeof IconCheck }> = {
  approval: { label: "Approved", color: "#00c27a", Icon: IconCheck },
  rejection: { label: "Blocked", color: "#ff453a", Icon: IconX },
  decision: { label: "Decision", color: "rgba(255,255,255,0.78)", Icon: IconQueue },
};

const shortHash = (h: string) => (h.length > 12 ? h.slice(0, 12) : h);

export function AuditChain() {
  useAuditPolling();
  const audit = useStore((s) => s.audit);
  const desktop = isDesktop();
  const stream = streamPill(desktop, audit.length);

  return (
    <div className="px-8 py-7 space-y-5 max-w-[1100px]">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="eyebrow mb-2">Security & Compliance</div>
          <h1 className="text-[26px] font-bold tracking-tight2 text-white/92">Audit Chain</h1>
        </div>
        <div className="flex items-center gap-2 mt-1">
          <StatusDot tone={stream.live ? "on" : "neutral"}>{stream.label}</StatusDot>
          <span className="inline-flex items-center text-[11px] text-white/80 px-2.5 py-1 rounded-full border border-white/15 bg-white/[0.04]">
            SHA-256 chained
          </span>
        </div>
      </div>

      {/* Consolidated explanation box (designed matching Decisions, Positions & Reports pages) */}
      <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
        <IconLightbulb width={16} height={16} className="text-white/70 mt-1 shrink-0" />
        <div className="text-[13.5px] text-white/70 leading-relaxed space-y-2">
          <p>
            <span className="font-semibold text-white/90">Tamper-Proof Audit Trail.</span>{" "}
            This page is your permanent security record. Unlike the Decisions page, which shows the active trading consensus, the Audit Chain acts as an unchangeable diary of all system actions. It includes blocked actions from safety guardrails (like the Iron Dome) that were stopped before execution, security seals (cryptographic hashes) that break if past logs are altered, and local files that provide an absolute source of truth.
          </p>
          <p className="text-[13px] text-white/45">
            <span className="text-white/60">Tip:</span> Look at the <span className="font-semibold text-white/70">Verdict</span> column: <span className="text-bull">Approved</span> means a decision passed all safety checks, while <span className="text-bear">Blocked</span> shows actions stopped by risk controls.
          </p>
        </div>
      </div>

      <div className="surface overflow-hidden">
        <div className="px-5 py-3 border-b border-white/5 flex items-center gap-3 text-[10px] text-white/16 uppercase tracking-[0.12em] font-semibold">
          <span className="w-16">Time</span>
          <span className="w-28">Verdict</span>
          <span className="w-16">Symbol</span>
          <span className="flex-1">Detail</span>
          <span className="w-28 text-right num">Hash</span>
        </div>

        {audit.length === 0 ? (
          <div className="px-5 py-10 text-center text-[12.5px] text-white/35">
            {desktop
              ? "No decisions recorded yet — the log fills as the engine runs."
              : "The audit log is available in the desktop app."}
          </div>
        ) : (
          <div>
            {audit.map((e, i) => {
              const meta = KIND_META[e.kind];
              const Icon = meta.Icon;
              return (
                <div
                  key={e.hash || `row-${i}`}
                  className="px-5 py-3 border-b border-white/5 last:border-0 flex items-center gap-3 text-[12px] hover:bg-white/[0.025] transition-colors"
                >
                  <span className="w-16 num text-[10.5px] text-white/55" title={ago(e.ts)}>
                    {fmtTime(e.ts)}
                  </span>
                  <span className="w-28 flex items-center gap-2">
                    <Icon width={11} height={11} style={{ color: meta.color }} />
                    <span className="text-[11px]" style={{ color: meta.color }}>
                      {meta.label}
                    </span>
                  </span>
                  <span className="w-16 font-semibold text-[12px] tracking-tight2 text-white/92">
                    {e.symbol ?? ""}
                  </span>
                  <span className="flex-1 text-white/55 leading-snug">{e.message}</span>
                  <span className="w-28 text-right num text-[10.5px] text-white/30" title={e.hash}>
                    {shortHash(e.hash)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
