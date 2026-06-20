import { useStore } from "@/console/store/useStore";
import { useAuditPolling } from "@/console/live/useAuditPolling";
import { fmtTime, ago } from "@/console/lib/format";
import { isDesktop } from "@/lib/desktopBridge";
import { IconCheck, IconX, IconQueue } from "@/console/shared/Icons";
import type { AuditKind } from "@/console/live/audit";

/**
 * Audit Chain page (G3d-2, #1050). Renders the engine's local hash-linked
 * decision log (audit_log_<date>.jsonl) — one row per senate decision, polled
 * through the desktop bridge. Only the three verdicts the contract carries are
 * shown (approval / rejection / decision); the bundle's richer event kinds have
 * no source on main. Desktop-only: the cloud build has no local file.
 */
const KIND_META: Record<AuditKind, { label: string; color: string; Icon: typeof IconCheck }> = {
  approval: { label: "Approved", color: "#30d158", Icon: IconCheck },
  rejection: { label: "Blocked", color: "#ff453a", Icon: IconX },
  decision: { label: "Decision", color: "rgba(255,255,255,0.78)", Icon: IconQueue },
};

const shortHash = (h: string) => (h.length > 12 ? h.slice(0, 12) : h);

export function AuditChain() {
  useAuditPolling();
  const audit = useStore((s) => s.audit);
  const desktop = isDesktop();

  return (
    <div className="px-8 py-7 space-y-5">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="eyebrow mb-2">Audit chain</div>
          <h1 className="text-[26px] font-bold tracking-tight2 text-white/92">Hash-linked decision log</h1>
          <p className="text-white/55 text-[13px] mt-2 max-w-xl">
            Every senate decision is appended locally to{" "}
            <span className="num text-white/92">audit_log_&lt;date&gt;.jsonl</span>. Each entry is hashed
            with its predecessor — tampering breaks the chain.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 text-[11px] text-white/55 px-2.5 py-1 rounded-full border border-white/10">
            <span className="live-dot" /> Stream live
          </span>
          <span className="inline-flex items-center text-[11px] text-white/80 px-2.5 py-1 rounded-full border border-white/15 bg-white/[0.04]">
            SHA-256 chained
          </span>
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
