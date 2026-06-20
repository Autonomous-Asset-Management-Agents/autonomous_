import { useState, useEffect, useRef, useCallback } from "react";
import { IconCheck, IconFingerprint, IconShield, IconBolt } from "@/console/shared/Icons";
import { useStore } from "@/console/store/useStore";
import { useEngine } from "@/console/live/useEngine";
import { getApiBase, stop as haltEngineApi } from "@/lib/api";
import { exportTelemetry, type EngineStatus } from "@/lib/desktopBridge";

/**
 * Console Settings page (#1050) — faithful UX re-port of the bundle dashboard's
 * "Engine, broker & safety" screen, scoped to the desktop edition. Cloud-only
 * surfaces from the bundle (mobile companion, push/haptic toggles, auto-update
 * from cloud, anonymous telemetry) are intentionally omitted per the desktop
 * re-port scope.
 *
 * Everything is wired to main's real surface where it exists — the engine
 * lifecycle (useEngine), the broker tags (store), the round-trip latency ping
 * (getApiBase) and the kill switch (POST /stop). The execution-mode selector is
 * a local preference: live HITL routing enforcement ships with the engine's
 * approval endpoint, so the card persists the choice and says so honestly
 * rather than pretending to flip engine behaviour.
 */

/** Toggle state persisted to localStorage so preferences survive restarts. */
function usePersistedToggle(key: string, initial: boolean): [boolean, (v: boolean) => void] {
  const storageKey = `aaa.settings.${key}`;
  const [v, setV] = useState<boolean>(() => {
    if (typeof window === "undefined") return initial;
    try {
      const raw = window.localStorage.getItem(storageKey);
      return raw === null ? initial : raw === "true";
    } catch {
      return initial;
    }
  });
  const set = useCallback(
    (nv: boolean) => {
      setV(nv);
      try {
        window.localStorage.setItem(storageKey, String(nv));
      } catch {
        /* storage unavailable — keep in-memory value */
      }
    },
    [storageKey]
  );
  return [v, set];
}

/** Execution-mode preference persisted to localStorage (default = safe HITL). */
function usePersistedMode(): ["auto" | "hitl", (v: "auto" | "hitl") => void] {
  const storageKey = "aaa.settings.executionMode";
  const [v, setV] = useState<"auto" | "hitl">(() => {
    if (typeof window === "undefined") return "hitl";
    try {
      return window.localStorage.getItem(storageKey) === "auto" ? "auto" : "hitl";
    } catch {
      return "hitl";
    }
  });
  const set = useCallback((nv: "auto" | "hitl") => {
    setV(nv);
    try {
      window.localStorage.setItem(storageKey, nv);
    } catch {
      /* storage unavailable — keep in-memory value */
    }
  }, []);
  return [v, set];
}

// ─── Engine status helpers (inlined; main has no engineStatusView) ──────────

const STATUS_LABEL: Record<EngineStatus, string> = {
  stopped: "OFFLINE",
  starting: "STARTING…",
  running: "RUNNING",
  stopping: "STOPPING…",
  error: "ERROR",
  unavailable: "UNAVAILABLE",
};

const STATUS_COLOR: Record<EngineStatus, string> = {
  stopped: "rgba(255,255,255,0.35)",
  starting: "#febc2e",
  running: "#28c840",
  stopping: "#febc2e",
  error: "rgba(255,69,58,0.9)",
  unavailable: "rgba(255,255,255,0.25)",
};

function EngineLogView({ logs }: { logs: string[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [logs.length]);

  return (
    <div className="rounded-lg border border-white/8 bg-black/40 overflow-y-auto" style={{ maxHeight: 180, minHeight: 60 }}>
      {logs.length === 0 ? (
        <p className="text-[10px] text-white/20 p-3 num">No engine output yet.</p>
      ) : (
        <div className="p-2">
          {logs.map((line, i) => (
            <div key={i} className="text-[10px] num text-white/45 leading-relaxed whitespace-pre-wrap break-all">
              {line}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  );
}

function EngineCard() {
  const engine = useEngine();
  if (!engine.isDesktop) return null;

  const { status, detail, logs, start, stop } = engine;
  const isStartDisabled = status === "starting" || status === "running";
  const isStopDisabled = status === "stopped" || status === "stopping";
  const dotColor = STATUS_COLOR[status];
  const detailVisible = !!detail && (status === "error" || status === "stopped");

  return (
    <div className="surface p-6" style={status === "error" ? { borderColor: "rgba(255,69,58,0.22)" } : {}}>
      <div className="flex items-center justify-between mb-4">
        <div className="eyebrow">Engine</div>
        <span className="text-[10px] font-semibold tracking-widest uppercase num flex items-center gap-1.5" style={{ color: dotColor }}>
          <span className="inline-block w-2 h-2 rounded-full shrink-0" style={{ background: dotColor, boxShadow: status === "running" ? `0 0 5px ${dotColor}` : "none" }} />
          {STATUS_LABEL[status]}
        </span>
      </div>

      {detailVisible && (
        <div className={status === "error" ? "flex items-start gap-2 px-4 py-3 rounded-lg border mb-4" : "flex items-start gap-2 px-4 py-3 rounded-lg border mb-4"} style={status === "error" ? { background: "rgba(255,69,58,0.10)", borderColor: "rgba(255,69,58,0.25)" } : { background: "rgba(255,159,10,0.10)", borderColor: "rgba(255,159,10,0.25)" }}>
          <span className="text-[12px] leading-relaxed break-all" style={{ color: status === "error" ? "rgba(255,122,114,0.9)" : "rgba(255,210,138,0.9)" }}>
            {detail}
          </span>
        </div>
      )}

      <div className="flex gap-2 mb-4">
        <button className="btn" onClick={() => void start()} disabled={isStartDisabled} style={{ opacity: isStartDisabled ? 0.4 : 1 }}>
          Start engine
        </button>
        <button className="btn" onClick={() => void stop()} disabled={isStopDisabled} style={{ opacity: isStopDisabled ? 0.4 : 1 }}>
          Stop engine
        </button>
      </div>

      <EngineLogView logs={logs} />
    </div>
  );
}

// ─── Diagnostics (desktop only) — INF-13 (a) #1372 ──────────────────────────

function DiagnosticsCard() {
  const engine = useEngine();
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!engine.isDesktop) return null;

  async function handleExport() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await exportTelemetry();
      if (r.ok) setMsg(`Exported ${r.records ?? 0} record(s) to ${r.path}.`);
      else if (r.empty) setMsg("No diagnostics recorded yet — nothing to export.");
      else if (r.canceled) setMsg(null);
      else setMsg(`Export failed: ${r.error ?? "unknown"}.`);
    } catch (err) {
      // Review #1376: surface an IPC rejection instead of an unhandled rejection + stuck UI.
      setMsg(`Export failed: ${err instanceof Error ? err.message : String(err)}.`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="surface p-6">
      <div className="eyebrow mb-4">Diagnostics</div>
      <div className="flex items-start gap-4">
        <div className="flex-1">
          <div className="text-[13px] font-medium text-white/92">Export diagnostics</div>
          <div className="text-[11px] text-white/30 mt-0.5 max-w-md leading-relaxed">
            Local crash &amp; stability records — already scrubbed of names, file paths and
            secrets. Nothing is sent automatically; you pick the file to share with support.
          </div>
          {msg && <div className="text-[11px] text-white/55 mt-2">{msg}</div>}
        </div>
        <button
          className="btn shrink-0"
          onClick={() => void handleExport()}
          disabled={busy}
          style={{ opacity: busy ? 0.4 : 1 }}
        >
          {busy ? "Exporting…" : "Export diagnostics"}
        </button>
      </div>
    </div>
  );
}

// ─── Toggle ────────────────────────────────────────────────────────────────

function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!on)}
      className="relative w-[42px] h-[26px] rounded-full transition-colors shrink-0"
      style={{ background: on ? "rgba(255,255,255,0.28)" : "rgba(255,255,255,0.10)" }}
    >
      <span
        className="absolute top-[3px] left-[3px] w-5 h-5 rounded-full transition-transform"
        style={{ background: "#fff", transform: on ? "translateX(16px)" : "translateX(0)", boxShadow: "0 1px 3px rgba(0,0,0,0.25)" }}
      />
    </button>
  );
}

interface Row {
  l: string;
  d: string;
  on: boolean;
  locked?: boolean;
  onChange?: (v: boolean) => void;
}

export function Settings() {
  // Preferences persist across restarts (localStorage).
  const [autoApproveSmall, setAutoApproveSmall] = usePersistedToggle("autoApproveSmall", false);
  const [paperMode, setPaperMode] = usePersistedToggle("paperMode", true);
  const [executionMode, setExecutionMode] = usePersistedMode();

  const brokerName = useStore((s) => s.brokerName);
  const accountTag = useStore((s) => s.accountTag);
  const engine = useEngine();
  const engineOnline = engine.status === "running";
  const [modeConfirmation, setModeConfirmation] = useState<string | null>(null);

  // Real broker round-trip latency (ping /health every 10s).
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    const ping = async () => {
      const t0 = performance.now();
      try {
        await fetch(`${getApiBase()}/health`, { method: "GET" });
        if (!cancelled) setLatencyMs(Math.round(performance.now() - t0));
      } catch {
        if (!cancelled) setLatencyMs(null);
      }
    };
    void ping();
    const id = setInterval(() => void ping(), 10000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // Emergency kill switch — two-step confirm, then POST /stop (halt trading; the
  // engine keeps running and open positions are left untouched, per the copy).
  const [killArmed, setKillArmed] = useState(false);
  const [killMsg, setKillMsg] = useState<string | null>(null);
  const armTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Clear the 4s auto-disarm timer on unmount so it never fires on a dead component.
  useEffect(() => () => {
    if (armTimer.current) clearTimeout(armTimer.current);
  }, []);
  async function handleKill() {
    if (!killArmed) {
      setKillArmed(true);
      setKillMsg(null);
      armTimer.current = setTimeout(() => setKillArmed(false), 4000);
      return;
    }
    if (armTimer.current) clearTimeout(armTimer.current);
    setKillArmed(false);
    try {
      await haltEngineApi();
      setKillMsg("Engine halted — trading stopped. Open positions left untouched.");
    } catch {
      setKillMsg("Could not reach the engine — nothing changed.");
    }
  }

  function handleModeChange(newMode: "auto" | "hitl") {
    if (newMode === executionMode) return;
    setExecutionMode(newMode);
    setModeConfirmation(
      newMode === "hitl"
        ? "Human-in-the-loop preferred. Preference saved locally — live routing enforcement activates with the engine's approval endpoint."
        : "Full Autonomous preferred. Preference saved locally — live routing enforcement activates with the engine's approval endpoint."
    );
    setTimeout(() => setModeConfirmation(null), 5000);
  }

  const rows: Row[] = [
    { l: "Require approval for all orders", d: "Recommended. AAAgents never routes without your explicit go.", on: true, locked: true },
    { l: "Auto-approve under €250 with senate ≥ 0.65", d: "Reduce friction on small high-conviction decisions.", on: autoApproveSmall, onChange: setAutoApproveSmall },
  ];

  return (
    <div className="px-8 py-7 space-y-6 max-w-[820px]">
      <div>
        <div className="eyebrow mb-2">Settings</div>
        <h1 className="text-[26px] font-bold tracking-tight2 text-white/92">Engine, broker &amp; safety</h1>
      </div>

      {/* ─── Decision routing ─────────────────────────────────────────── */}
      <div className="surface p-6">
        <div className="eyebrow mb-4">Decision routing</div>
        <div className="space-y-4">
          {rows.map((row, i) => (
            <div key={i} className="flex items-start gap-4 py-2 border-b border-white/5 last:border-0">
              <div className="flex-1">
                <div className="text-[13px] font-medium text-white/92">{row.l}</div>
                <div className="text-[11px] text-white/30 mt-0.5">{row.d}</div>
              </div>
              {row.locked ? (
                <span className="pill pill-strong">policy · always on</span>
              ) : (
                <Toggle on={row.on} onChange={row.onChange!} />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ─── Execution mode ──────────────────────────────────────────── */}
      <div className="surface p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="eyebrow">Execution mode</div>
          <span className={`pill ${executionMode === "hitl" ? "pill-warn" : "pill-bull"}`}>
            {executionMode === "hitl" ? "Human-in-the-loop" : "Full Autonomous"}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-3 mb-4">
          <button
            onClick={() => handleModeChange("auto")}
            disabled={executionMode === "auto"}
            className={`p-4 rounded-xl border text-left transition-all ${executionMode === "auto" ? "border-bull/50 bg-bull/10" : "border-white/10 hover:border-white/20 bg-white/[0.03]"}`}
          >
            <div className="flex items-center gap-2 mb-1.5">
              <IconBolt width={13} height={13} className={executionMode === "auto" ? "text-bull" : "text-white/30"} />
              <span className={`text-[13px] font-semibold ${executionMode === "auto" ? "text-bull" : "text-white/70"}`}>Full Autonomous</span>
              {executionMode === "auto" && <span className="ml-auto pill pill-bull">active</span>}
            </div>
            <div className="text-[11px] text-white/30 leading-relaxed">
              The senate executes trades immediately when conviction is met. No manual step required.
            </div>
          </button>

          <button
            onClick={() => handleModeChange("hitl")}
            disabled={executionMode === "hitl"}
            className={`p-4 rounded-xl border text-left transition-all ${executionMode === "hitl" ? "border-amber/50 bg-amber/10" : "border-white/10 hover:border-white/20 bg-white/[0.03]"}`}
          >
            <div className="flex items-center gap-2 mb-1.5">
              <IconFingerprint width={13} height={13} className={executionMode === "hitl" ? "text-amber" : "text-white/30"} />
              <span className={`text-[13px] font-semibold ${executionMode === "hitl" ? "text-amber" : "text-white/70"}`}>Human-in-the-loop</span>
              {executionMode === "hitl" && <span className="ml-auto pill pill-warn">active</span>}
            </div>
            <div className="text-[11px] text-white/30 leading-relaxed">
              Every senate decision waits for your approval before routing. Review them in the Decisions queue.
            </div>
          </button>
        </div>

        {modeConfirmation && (
          <div className="flex items-start gap-2 px-4 py-3 rounded-lg bg-white/[0.05] border border-white/10">
            <IconCheck width={12} height={12} className="text-bull shrink-0 mt-0.5" />
            <span className="text-[12px] text-white/70 leading-relaxed">{modeConfirmation}</span>
          </div>
        )}
      </div>

      {/* ─── Engine controls (desktop only) ───────────────────────────── */}
      <EngineCard />

      {/* ─── Broker ───────────────────────────────────────────────────── */}
      <div className="surface p-6">
        <div className="eyebrow mb-4">Broker</div>
        <div className="grid grid-cols-3 gap-4 mb-4">
          <div>
            <div className="text-[10px] text-white/30 uppercase tracking-wider mb-1">Connected</div>
            <div className="text-[14px] font-semibold text-white/92 flex items-center gap-2">
              <span className="inline-block w-2 h-2 rounded-full shrink-0" style={{ background: engineOnline ? "#28c840" : "rgba(255,255,255,0.3)" }} />
              {brokerName ?? "—"}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-white/30 uppercase tracking-wider mb-1">Account</div>
            <div className="num text-[14px] font-semibold text-white/92">{accountTag ?? "—"}</div>
          </div>
          <div>
            <div className="text-[10px] text-white/30 uppercase tracking-wider mb-1">Latency</div>
            <div className={`num text-[14px] font-semibold ${latencyMs === null ? "text-white/40" : "text-bull"}`}>
              {latencyMs === null ? "—" : `${latencyMs} ms`}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-4 py-2 border-t border-white/5">
          <div className="flex-1">
            <div className="text-[13px] font-medium text-white/92">Paper trading mode</div>
            <div className="text-[11px] text-white/30 mt-0.5">Switch off only after you have reviewed 30 days of paper performance.</div>
          </div>
          <Toggle on={paperMode} onChange={setPaperMode} />
        </div>
      </div>

      {/* ─── Emergency kill switch ────────────────────────────────────── */}
      <div className="surface p-6" style={{ borderColor: "rgba(255,69,58,0.22)" }}>
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 rounded-xl flex items-center justify-center" style={{ background: "rgba(255,69,58,0.15)", border: "1px solid rgba(255,69,58,0.30)" }}>
            <IconShield width={20} height={20} className="text-bear" />
          </div>
          <div className="flex-1">
            <div className="text-[14px] font-semibold text-white/92">Emergency kill switch</div>
            <div className="text-[12px] text-white/55 mt-1 max-w-md">
              Halts the engine immediately — no new orders are placed. Open positions are left untouched; you decide whether to liquidate.
            </div>
            {killMsg && <div className="text-[11px] text-white/55 mt-2">{killMsg}</div>}
          </div>
          <button className="btn btn-bear shrink-0" onClick={() => void handleKill()}>
            {killArmed ? "Confirm — halt now" : "Arm kill switch"}
          </button>
        </div>
      </div>

      {/* ─── Diagnostics (desktop only) ───────────────────────────────── */}
      <DiagnosticsCard />

      <div className="text-[10.5px] text-white/30 leading-relaxed pt-4">
        AAAgents Desktop · running on your hardware, no compute leaves this machine.
      </div>
    </div>
  );
}
