import { useState, useEffect, useRef, useCallback } from "react";
import { IconCheck, IconFingerprint, IconShield, IconBolt, IconLightbulb } from "@/console/shared/Icons";
import { StatusDot } from "@/console/shared/StatusDot";
import { useStore } from "@/console/store/useStore";
import { useEngine } from "@/console/live/useEngine";
import { exportTelemetry, getAppVersion, type EngineStatus } from "@/lib/desktopBridge";
import { LiveTradingSwitchCard } from "@/console/desktop/LiveTradingSwitchCard";
import { HitlPolicyCard } from "@/console/desktop/HitlPolicyCard";
import { SidebarNav } from "@/console/desktop/SidebarNav";
import { LlmProviderCard } from "@/console/desktop/LlmProviderCard";
import { DailyUpdatesCard } from "@/console/desktop/DailyUpdatesCard";
import { LegalTab } from "@/console/desktop/LegalTab";
import { resolveExecutionMode } from "@/console/desktop/executionMode";

/**
 * Console Settings page (#1050) — faithful UX re-port of the bundle dashboard's
 * "Engine, broker & safety" screen, scoped to the desktop edition. Cloud-only
 * surfaces from the bundle (mobile companion, push/haptic toggles, auto-update
 * from cloud, anonymous telemetry) are intentionally omitted per the desktop
 * re-port scope.
 *
 * Everything is wired to main's real surface where it exists — the engine
 * lifecycle (useEngine), the broker tags (store) and the kill switch
 * (POST /stop). The execution-mode selector is
 * a local preference: live HITL routing enforcement ships with the engine's
 * approval endpoint, so the card persists the choice and says so honestly
 * rather than pretending to flip engine behaviour.
 */

/** Execution-mode preference persisted to localStorage. Default = autonomous (#1653): paper runs
 *  autonomously (#1442); live is HITL-enforced at boot regardless of this UI value. */
function usePersistedMode(): ["auto" | "hitl", (v: "auto" | "hitl") => void] {
  const storageKey = "aaa.settings.executionMode";
  const [v, setV] = useState<"auto" | "hitl">(() => {
    if (typeof window === "undefined") return "auto";
    try {
      return resolveExecutionMode(window.localStorage.getItem(storageKey));
    } catch {
      return "auto";
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
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!engine.isDesktop && !(!!import.meta.env.DEV && !isTest)) return null;

  const { status, detail, logs, start, stop } = engine;
  const isStartDisabled = status === "starting" || status === "running";
  const isStopDisabled = status === "stopped" || status === "stopping";
  const detailVisible = !!detail && (status === "error" || status === "stopped");

  return (
    <div className="surface p-6" style={status === "error" ? { borderColor: "rgba(255,69,58,0.22)" } : {}}>
      <div className="flex items-center justify-between mb-4">
        <div className="eyebrow">Engine</div>
        <StatusDot
          tone={
            status === "running" || status === "starting"
              ? "on"
              : status === "stopped" || status === "error" || status === "unavailable"
              ? "off"
              : "neutral"
          }
          className="!text-[10px]"
        >
          {STATUS_LABEL[status]}
        </StatusDot>
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
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!engine.isDesktop && !(!!import.meta.env.DEV && !isTest)) return null;

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

const TAB_TITLES: Record<string, string> = {
  trading: "Trading",
  system: "System",
  notifications: "Notifications",
  about: "About",
  legal: "Legal",
};

export function Settings() {
  // Preferences persist across restarts (localStorage).
  const [executionMode, setExecutionMode] = usePersistedMode();

  const [modeConfirmation, setModeConfirmation] = useState<string | null>(null);
  // App version shown in the About card — desktop only (null in the browser, #1939).
  const [appVersion, setAppVersion] = useState<string | null>(null);
  useEffect(() => {
    void getAppVersion().then(setAppVersion);
  }, []);

  // Tab Routing (Phase 3 & 3.3)
  const [activeTab, setActiveTab] = useState(() => {
    if (typeof window !== "undefined" && window.location.hash) {
      const hash = window.location.hash.replace("#", "");
      if (["trading", "system", "notifications", "about", "legal"].includes(hash)) {
        return hash;
      }
    }
    return "trading";
  });

  useEffect(() => {
    const handleHashChange = () => {
      const hash = window.location.hash.replace("#", "");
      if (["trading", "system", "notifications", "about", "legal"].includes(hash)) {
        setActiveTab(hash);
      }
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  const handleTabChange = useCallback((tab: string) => {
    setActiveTab(tab);
    window.location.hash = tab;
  }, []);




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



  return (
    <div className="px-8 py-7 space-y-6 max-w-[1100px]">
      <div className="mb-8">
        <div className="eyebrow mb-2">Settings</div>
        <h1 className="text-[26px] font-bold tracking-tight2 text-white/92">
          {TAB_TITLES[activeTab] || "Settings"}
        </h1>
      </div>

      <div className="space-y-6 pb-24">
          {activeTab === "trading" && (
            <>

      {/* Consolidated explanation box (designed matching Decisions, Positions & Reports pages) */}
      <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
        <IconLightbulb width={16} height={16} className="text-white/70 mt-1 shrink-0" />
        <div className="text-[13.5px] text-white/70 leading-relaxed space-y-2">
          <p>
            <span className="font-semibold text-white/90">Autonomous Execution.</span>{" "}
            The trading engine acts on its own calculations in real-time. This is not investment advice and every trade is executed solely at your own responsibility, as market movements can lead to capital losses.
          </p>
          <p className="text-[13px] text-white/45">
            <span className="text-white/60">Tip:</span> Review our detailed{" "}
            <a href="/legal/risk-disclosure" className="underline hover:text-white">
              risk disclosure
            </a>{" "}
            to understand the safety parameters before connecting live keys.
          </p>
        </div>
      </div>

      {/* ─── Execution mode ──────────────────────────────────────────── */}
      <div className="surface p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="eyebrow">Execution mode</div>
          <StatusDot tone="on">
            {executionMode === "hitl" ? "Human-in-the-loop" : "Full Autonomous"}
          </StatusDot>
        </div>

        <div className="grid grid-cols-2 gap-3 mb-4">
          <button
            onClick={() => handleModeChange("auto")}
            disabled={executionMode === "auto"}
            className={`p-4 rounded-xl border text-left transition-all ${executionMode === "auto" ? "border-white/20 bg-white/[0.06]" : "border-white/10 hover:border-white/20 bg-white/[0.03]"}`}
          >
            <div className="flex items-center gap-2 mb-1.5">
              <IconBolt width={13} height={13} className={executionMode === "auto" ? "text-white/70" : "text-white/30"} />
              <span className={`text-[13px] font-semibold ${executionMode === "auto" ? "text-white" : "text-white/70"}`}>Full Autonomous</span>
              {executionMode === "auto" && <StatusDot tone="on" className="ml-auto !text-[11px]">active</StatusDot>}
            </div>
            <div className="text-[11px] text-white/30 leading-relaxed">
              The senate executes trades immediately when conviction is met. No manual step required.
            </div>
          </button>

          <button
            onClick={() => handleModeChange("hitl")}
            disabled={executionMode === "hitl"}
            className={`p-4 rounded-xl border text-left transition-all ${executionMode === "hitl" ? "border-white/20 bg-white/[0.06]" : "border-white/10 hover:border-white/20 bg-white/[0.03]"}`}
          >
            <div className="flex items-center gap-2 mb-1.5">
              <IconFingerprint width={13} height={13} className={executionMode === "hitl" ? "text-white/70" : "text-white/30"} />
              <span className={`text-[13px] font-semibold ${executionMode === "hitl" ? "text-white" : "text-white/70"}`}>Human-in-the-loop</span>
              {executionMode === "hitl" && <StatusDot tone="on" className="ml-auto !text-[11px]">active</StatusDot>}
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

      {/* Trading account (#60) — Paper|Live toggle + key slots in one card. */}
      <LiveTradingSwitchCard />

      {/* ─── Human-in-the-loop policy (LIVE-1 T2, #1425): the REAL engine limits ── */}
      <HitlPolicyCard />

    </>
    )}



          {activeTab === "system" && (
            <div className="space-y-6">
              {/* ─── LLM · vendor-independent provider picker (desktop · #1705) ──── */}
              <LlmProviderCard />

              {/* ─── Engine controls (desktop only) ───────────────────────────── */}
              <EngineCard />

              {/* ─── Diagnostics (desktop only) ───────────────────────────────── */}
              <DiagnosticsCard />
            </div>
          )}

          {activeTab === "notifications" && (
            <div className="space-y-6">
              {/* Consolidated explanation box (designed matching Decisions, Positions & Reports pages) */}
              <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
                <IconLightbulb width={16} height={16} className="text-white/70 mt-1 shrink-0" />
                <div className="text-[13.5px] text-white/70 leading-relaxed space-y-2">
                  <p>
                    <span className="font-semibold text-white/90">Daily Video Recaps.</span>{" "}
                    Lassen Sie den Bot tägliche Video-Recaps und Performance-Berichte generieren und direkt auf Ihren konfigurierten Kanälen veröffentlichen. Perfekt, um die täglichen Handelsaktivitäten des Modells visuell zu protokollieren und zu teilen.
                  </p>
                  <p className="text-[13px] text-white/45">
                    <span className="text-white/60">Tipp:</span> Nutzen Sie den untenstehenden Zeitplaner, um den exakten Zeitpunkt festzulegen, an dem die Tageszusammenfassungen generiert und hochgeladen werden sollen.
                  </p>
                </div>
              </div>

              {/* ─── Daily social updates — channels + credentials (SHORTS-1 · #1680) ──── */}
              <DailyUpdatesCard />
            </div>
          )}

          {activeTab === "legal" && (
            <div className="space-y-6">
              <LegalTab />
            </div>
          )}

          {activeTab === "about" && (
            <div className="space-y-6">
              {/* ─── About (intent · name · company · founders — from NOTICE) ─── */}
              <div className="surface p-6">
                <div className="text-[14px] font-semibold text-white/92">About autonomous_</div>
                <p className="text-[12px] text-white/55 mt-2 leading-relaxed max-w-lg">
                  An open-source, on-device autonomous trading system — the Community Edition of the{" "}
                  <span className="text-white/70">autonomous_trading solution</span>. The intent: a transparent
                  trading agent you fully own and run on your own hardware — no compute or data leaves this
                  machine, and it trades in <span className="text-white/70">paper mode</span> by default.
                  Released under the Apache License 2.0.
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-3 mt-4">
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-white/35">Company</div>
                    <div className="text-[12px] text-white/80 mt-0.5">
                      Autonomous Asset Management Agents UG (haftungsbeschränkt)
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-white/35">Founders &amp; developers</div>
                    <div className="text-[12px] text-white/80 mt-0.5">Andreas Apeldorn · Georg Apeldorn</div>
                  </div>
                  {appVersion && (
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-white/35">Version</div>
                      <div className="text-[12px] text-white/80 mt-0.5">{appVersion}</div>
                    </div>
                  )}
                </div>
                <div className="text-[11px] text-white/40 mt-4">
                  <a href="https://autonomous-trading.de" target="_blank" rel="noreferrer" className="text-[#00c27a] underline">
                    autonomous-trading.de
                  </a>
                  {" · "}
                  <a href="/legal/imprint" className="text-[#00c27a] underline">Legal &amp; imprint</a>
                  {" · © 2026 Autonomous Asset Management Agents UG"}
                </div>
              </div>

              <div className="text-[10.5px] text-white/30 leading-relaxed pt-4">
                autonomous_ Desktop · running on your hardware, no compute leaves this machine.
              </div>
            </div>
          )}
      </div>
    </div>
  );
}
