import { useState, useEffect, useRef, useCallback } from "react";
import { IconLightbulb, IconShield } from "@/console/shared/Icons";
import { StatusDot } from "@/console/shared/StatusDot";
import { useStore } from "@/console/store/useStore";
import { useKillSwitch } from "@/console/desktop/useKillSwitch";
import { startLive } from "@/lib/api";
import { EmergencyStopModal } from "@/console/desktop/EmergencyStopModal";
import { useEngine } from "@/console/live/useEngine";
import { exportTelemetry, getAppVersion, isDesktop, type EngineStatus } from "@/lib/desktopBridge";
import { LiveTradingSwitchCard } from "@/console/desktop/LiveTradingSwitchCard";
import { HitlPolicyCard } from "@/console/desktop/HitlPolicyCard";
import { ConstraintApprovalCard } from "@/console/desktop/ConstraintApprovalCard";
import { SidebarNav } from "@/console/desktop/SidebarNav";
import { LlmProviderCard } from "@/console/desktop/LlmProviderCard";
import { DailyUpdatesCard } from "@/console/desktop/DailyUpdatesCard";
import { UpdateCard } from "@/console/desktop/UpdateCard";
import { LegalTab } from "@/console/desktop/LegalTab";

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

// ─── Engine status helpers (inlined; main has no engineStatusView) ──────────

const STATUS_LABEL: Record<EngineStatus, string> = {
  stopped: "OFFLINE",
  starting: "STARTING…",
  running: "RUNNING",
  stopping: "STOPPING…",
  // LSR R8 (#2261): a benign, expected restart — neutral label, not an error.
  restarting: "RESTARTING…",
  error: "ERROR",
  unavailable: "UNAVAILABLE",
};

function EngineLogView({ logs }: { logs: string[] }) {
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Auto-follow the newest line by scrolling the log box's OWN content — NOT scrollIntoView, which
    // also scrolls every ancestor (the whole Settings page) to the bottom on every streamed line, so
    // the live engine log yanked the page down and made picking an LLM tile above impossible. Only
    // follow when already near the bottom, so scrolling up to read the log stays put.
    const el = boxRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [logs.length]);

  return (
    <div
      ref={boxRef}
      className="rounded-lg border border-white/8 bg-black/40 overflow-y-auto"
      style={{ maxHeight: 180, minHeight: 60 }}
    >
      {logs.length === 0 ? (
        <p className="text-[10px] text-white/20 p-3 num">No engine output yet.</p>
      ) : (
        <div className="p-2">
          {logs.map((line, i) => (
            <div key={i} className="text-[10px] num text-white/45 leading-relaxed whitespace-pre-wrap break-all">
              {line}
            </div>
          ))}
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
  // "restarting" (LSR R8 #2261): a restart is already in flight — no racy Start.
  const isStartDisabled = status === "starting" || status === "running" || status === "restarting";
  const isStopDisabled = status === "stopped" || status === "stopping";
  const detailVisible = !!detail && (status === "error" || status === "stopped");

  return (
    <div className="surface p-6" data-testid="engine-card" style={status === "error" ? { borderColor: "rgba(255,69,58,0.22)" } : {}}>
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

// ─── Emergency Safety Control (Kill Switch) — moved from Overview (#1642) ────

function SafetyControlCard() {
  const systemHalted = useStore((s) => s.systemHalted);
  const strategyRunning = useStore((s) => s.strategyRunning);
  // Startup-race guard (P1): engineServing is true only once /health confirmed a serving engine
  // (reachable AND status !== "starting"). While the engine is still initializing (the fire-and-forget
  // lifespan task hasn't built BotEngine yet) it is false, and pressing "Start" would POST /start-live
  // at a not-yet-existing engine → HTTP 500 → "failed to fetch". The always-on Sidebar health poll
  // keeps this fresh app-wide. Gate the Start button on it (the backend also guards defensively).
  const engineServing = useStore((s) => s.engineServing);
  const { killArmed, killMsg, killResetting, armTimeLeft, handleKill, handleResetKill } = useKillSwitch();
  const engineRunning = strategyRunning === true && systemHalted !== true;
  // Idle engine that has NOT yet confirmed serving → still booting; wait instead of firing /start-live.
  const engineBooting = !engineRunning && !engineServing;

  const [showConfirmModal, setShowConfirmModal] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  // Desktop-only: the cloud edition does not expose engine controls.
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;

  const handleStartEngine = async () => {
    // Defensive: the button is already disabled while booting, but never fire /start-live at an
    // engine that isn't serving yet (the init-window 500 this fix removes).
    if (engineBooting) {
      setStartError("Engine is starting… please wait a few seconds and try again.");
      return;
    }
    setStarting(true);
    setStartError(null);
    try {
      if (systemHalted) {
        await handleResetKill();
      } else {
        const res = await startLive();
        if (res && res.status === "error") {
          setStartError((res as { message?: string }).message || "Engine failed to start. Check your configuration.");
        }
      }
    } catch (e: unknown) {
      setStartError(e instanceof Error ? e.message : "Engine failed to start.");
    } finally {
      setStarting(false);
    }
  };

  return (
    <>
      <div className="surface p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-white/[0.04] border border-white/10 shrink-0">
              <IconShield width={16} height={16} className="text-white/70" />
            </div>
            <h3 className="text-[14px] font-semibold text-white/92">Emergency Safety Control</h3>
          </div>
          <div className="flex items-center gap-2 text-[12px] font-medium text-white/70">
            {systemHalted == null ? (
              <span className="text-white/40">—</span>
            ) : systemHalted ? (
              <>
                <span className="w-1.5 h-1.5 rounded-full bg-[#ff5a52] shrink-0" />
                <span>System Status: Halted</span>
              </>
            ) : (
              <>
                <span className="w-1.5 h-1.5 rounded-full bg-[#00c27a] shrink-0" />
                <span>System Status: Active</span>
              </>
            )}
          </div>
        </div>

        <p className="text-[13px] text-white/60 leading-relaxed max-w-2xl">
          This safety control allows you to immediately halt all automated trading operations. Open positions in your Alpaca account remain unaffected and must be managed manually. Halt and reset actions are permanently recorded on the WORM compliance audit log.
        </p>

        {killMsg && (
          <div className="text-[13px] px-3 py-2 rounded bg-white/[0.03] border border-white/5 text-white/70">
            {killMsg}
          </div>
        )}

        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-3 border-t border-white/5">
          <div className="text-[12px] text-white/45 text-left w-full sm:w-auto">
            {engineRunning
              ? "Click to initiate emergency stop. Confirmation will be required."
              : engineBooting
                ? "Engine is starting… (a few seconds)"
                : "Start the engine to resume automated trading."}
          </div>
          <div className="flex items-center gap-3 w-full sm:w-auto justify-end">
            {engineRunning ? (
              <button
                aria-label="Kill Switch"
                className="w-[178px] rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase text-white bg-[#ff5a52] hover:bg-[#ff6c65] border-none transition-all transform active:scale-[0.97]"
                onClick={() => setShowConfirmModal(true)}
              >
                Kill Switch
              </button>
            ) : (
              <button
                aria-label="Start engine"
                onClick={() => void handleStartEngine()}
                disabled={starting || killResetting || engineBooting}
                className="w-[178px] rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase text-white bg-[#00c27a] hover:bg-[#11d089] border-none transition-all transform active:scale-[0.97] disabled:cursor-not-allowed flex items-center justify-center gap-2"
                style={{ opacity: starting || killResetting || engineBooting ? 0.6 : 1 }}
              >
                {(starting || killResetting || engineBooting) && (
                  <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                )}
                {engineBooting ? "Engine starting…" : starting || killResetting ? "Starting…" : "Start Engine"}
              </button>
            )}
          </div>
          {startError && (
            <div className="w-full mt-3 flex justify-end">
              <span className="text-[12px] text-[#ff8a80] font-medium">{startError}</span>
            </div>
          )}
        </div>
      </div>

      <EmergencyStopModal
        open={showConfirmModal}
        onClose={() => setShowConfirmModal(false)}
        onHalt={handleKill}
      />
    </>
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

      {/* INC-7 (#2213): the cosmetic localStorage "Execution mode" tiles were removed — the ONE
          real autonomy control now lives in <HitlPolicyCard/> below (wired to /api/hitl/policy).
          Two-axis layout: account (Paper/Live) here, then autonomy below. */}

      {/* Trading account (#60) — Paper|Live toggle + key slots in one card. */}
      <LiveTradingSwitchCard />

      {/* ─── Human-in-the-loop policy (LIVE-1 T2, #1425): the REAL engine limits ── */}
      <HitlPolicyCard />

      {/* #2666 (Weg 1): pending EOD sector-cap proposal — invisible unless one is staged. */}
      <ConstraintApprovalCard />

      {/* ─── Emergency Safety Control (Kill Switch) — #1642 ──────────────── */}
      <SafetyControlCard />

    </>
    )}



          {activeTab === "system" && (
            <div className="space-y-6">
              {/* Consolidated explanation box */}
              <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
                <IconLightbulb width={16} height={16} className="text-white/70 mt-1 shrink-0" />
                <div className="text-[13.5px] text-white/70 leading-relaxed space-y-2">
                  <p>
                    <span className="font-semibold text-white/90">System Configuration.</span>{" "}
                    Manage the underlying AI model, control the engine lifecycle, and export local diagnostics.
                  </p>
                </div>
              </div>

              {/* ─── LLM · vendor-independent provider picker (desktop · #1705) ──── */}
              <LlmProviderCard />

              {/* ─── Engine controls (desktop only) ───────────────────────────── */}
              <EngineCard />

              {/* ─── Diagnostics (desktop only) ───────────────────────────────── */}
              <DiagnosticsCard />

              {/* ─── Update (desktop only, #2077) ─────────────────────────────── */}
              <UpdateCard />
            </div>
          )}

          {activeTab === "notifications" && (
            <div className="space-y-6">
              {/* Consolidated explanation box (designed matching Decisions, Positions & Reports pages) */}
              <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
                <IconLightbulb width={16} height={16} className="text-white/70 mt-1 shrink-0" />
                <div className="text-[13.5px] text-white/70 leading-relaxed space-y-2">
                  <p>
                    <span className="font-semibold text-white/90">Daily Report.</span>{" "}
                    A sober daily summary of your own account activity, delivered automatically (up to 3× a day) to your own private channels — a chat app (Slack, Discord, Mattermost, Teams or Telegram) and/or your email. Generated locally on your device; nothing is posted publicly, and nothing is sent to autonomous_.
                  </p>
                  <p className="text-[13px] text-white/45">
                    <span className="text-white/60">Note:</span> This is not investment advice. The report is sent only to the private channels you configure.
                  </p>
                </div>
              </div>

              {/* ─── Daily report — forward your own summary to your own contacts ──── */}
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
