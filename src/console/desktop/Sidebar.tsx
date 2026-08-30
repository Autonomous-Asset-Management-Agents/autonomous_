import { useState, useEffect } from "react";
import { useStore, type ConsolePage } from "@/console/store/useStore";
import { IconChat, IconChevronRight } from "@/console/shared/Icons";
import { StatusDot } from "@/console/shared/StatusDot";
import { navItems } from "./nav";
import { useEngine } from "@/console/live/useEngine";
import { useHealthPolling } from "@/console/live/useHealthPolling";
// #2743/S3: the /health/deep poll (market-open + broker label + components_degraded) mounted here at
// the PERSISTENT sidebar, not just the Overview page, so the run-status stays honest on every page.
import { useHealthPolling as useDeepHealthPolling } from "@/console/live/health";
import { useEntitlementPolling } from "@/console/live/useEntitlementPolling";
import { UpgradeModal } from "@/console/desktop/UpgradeModal";
import { tradingLabel } from "@/console/live/trading";
import { useKillSwitch } from "@/console/desktop/useKillSwitch";
import { EmergencyStopModal } from "@/console/desktop/EmergencyStopModal";

/**
 * Console sidebar (#1050) — faithful UX re-port of the bundle nav rail: the chat
 * launcher + page nav (unchanged) plus the system-status footer. The footer shows
 * the live engine state (useEngine), an "Agents" count (specialist reports +
 * senate members, from the store; "—" when none are active yet), and a "Live
 * Trading" state from /health's strategy_running ("Idle" when the loop isn't
 * running, "Paper" while paper-trading, "Live" in red for live trading). The
 * Decisions pending-count badge: the HITL endpoints exist since PR-0a-ii (#2660 renders
 * the queue on the Orders page); a badge here remains an optional follow-up.
 */
/**
 * Upgrade CTA (GTM-1 #1915 · ADR-GTM-1b) — the top-left sidebar slot for Junior
 * (BASIC) desktops. Clicking opens the tier {@link UpgradeModal} (Senior /
 * Institutional / Developer) which explains what each unlocks — instead of silently
 * claiming. The Senior claim (free-beta bundled OFFLINE license, no cloud/login)
 * happens inside the modal. Styled like the Overview kill-switch button — a solid
 * pill in our green (#00c27a) with white, bold, uppercase text.
 */
export function UpgradeCta({ refetch }: { refetch: () => Promise<void> }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="w-full rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide text-white bg-[#00c27a] hover:bg-[#00d687] border-none transition-all transform active:scale-[0.97] disabled:opacity-60"
        title="See what upgrading unlocks"
      >
        UPGRADE
      </button>
      {open && <UpgradeModal onClose={() => setOpen(false)} refetch={refetch} />}
    </>
  );
}

/**
 * Senior "Plans" entry — a discreet link (not the green Upgrade pill) that opens the SAME tier
 * modal, so Institutional / Developer stay reachable after the Senior upgrade. Styled like the
 * ENT Chat launcher, not the Junior UPGRADE CTA.
 */
function PlansCta({ refetch }: { refetch: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-[12.5px] bg-white/[0.04] text-white/80 border border-white/10 hover:bg-white/[0.07] hover:text-white/92 transition-all"
        title="Compare editions"
      >
        <span className="flex-1 text-left font-medium">Plans</span>
        <IconChevronRight width={11} height={11} className="opacity-40" />
      </button>
      {open && <UpgradeModal onClose={() => setOpen(false)} refetch={refetch} />}
    </>
  );
}

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

  const [activeSettingsTab, setActiveSettingsTab] = useState(() => {
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
      if (typeof window !== "undefined") {
        const hash = window.location.hash.replace("#", "");
        if (["trading", "system", "notifications", "about", "legal"].includes(hash)) {
          setActiveSettingsTab(hash);
        }
      }
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);
  const engine = useEngine();
  useHealthPolling();
  useDeepHealthPolling();
  const { refetch: refetchEntitlement } = useEntitlementPolling();
  const canUpgrade = useStore((s) => s.canUpgrade);
  const tier = useStore((s) => s.tier);
  // Simulation/backtest nav is entitlement-gated (hidden unless strictly enabled).
  const simulationEnabled = useStore((s) => s.simulationEnabled);
  // The "Chat" launcher is now ENT-only. Hide it for the gated desktop tiers
  // (Junior=BASIC, Senior=PRO); show it while the tier is unknown/ENT (null →
  // cloud/browser build, or a resolved ENT-eligible tier).
  const showChat = tier == null || (tier !== "BASIC" && tier !== "PRO");
  // Senior (PRO) keeps a discreet "Plans" entry to the tier modal so Institutional / Developer
  // stay reachable after the Senior upgrade (the green UPGRADE pill is Junior-only).
  const isSenior = tier === "PRO";
  const specialistReports = useStore((s) => s.specialistReports);
  const roundTable = useStore((s) => s.roundTable);
  const strategyRunning = useStore((s) => s.strategyRunning);
  const marketOpen = useStore((s) => s.marketOpen);
  const componentsDegraded = useStore((s) => s.componentsDegraded);
  const systemHalted = useStore((s) => s.systemHalted);
  const paperTrading = useStore((s) => s.paperTrading);
  // #2708 D2: a live-arming affordance must be refused while a mode switch is in flight.
  const switchReconciling = useStore((s) => s.switchReconciling);
  const modeSwitch = useStore((s) => s.modeSwitch);
  // Startup-race guard (P1): the engine PROCESS can be up (engine.status "running", port bound) while
  // the in-engine BotEngine is still being built by the fire-and-forget lifespan init task — /health
  // reports status:"starting" in that window, which useHealthPolling records as engineServing=false.
  // Pressing "Start" then hit POST /start-live before the engine existed → HTTP 500 → the UI showed
  // "failed to fetch". Gate the Start control on a CONFIRMED-serving engine so the click can't fire
  // during init; the backend also guards defensively. `engine.status === "running"` scopes this to a
  // live process, so the cloud/browser preview ("unavailable") and a stopped/errored engine are
  // unaffected (their existing labels/behaviour stand).
  const engineServing = useStore((s) => s.engineServing);
  // "unavailable" is the cloud/browser preview (no shell) — show it as running,
  // matching the bundle's static browser label.
  const online = engine.status === "running" || engine.status === "unavailable";

  // RC2-7: the footer engine pill mirrors the Overview #6 engine control EXACTLY — it reflects the
  // trading MACHINE state (loop running / halted / idle), not merely whether the python PROCESS is
  // up. `engineRunning` is the SAME definition Overview uses, so the two can never disagree:
  //   Kill Switch shown  ⇔ "Engine running" (green)
  //   Start Engine shown ⇔ "Engine idle" (neutral) | "Engine halted" (red) | "Engine offline"
  const engineRunning =
    strategyRunning === true && systemHalted !== true && componentsDegraded !== true;
  // True while a live engine process is up but /health has not yet confirmed a serving engine
  // (BotEngine still initializing) → the Start control must wait, not fire /start-live.
  const engineBooting = engine.status === "running" && !engineServing;
  // Cloud/browser preview (no shell) has no machine state → keep the static green "Engine running".
  const isPreview = engine.status === "unavailable";
  let engineLabel: string;
  let engineTone: "on" | "off" | "neutral";
  if (online) {
    if (isPreview) {
      engineLabel = "Engine active";
      engineTone = "on";
    } else if (systemHalted) {
      engineLabel = "Engine halted"; // ⇔ Overview shows Start Engine
      engineTone = "off";
    } else if (engineRunning) {
      engineLabel = "Engine active"; // ⇔ Overview shows Kill Switch
      engineTone = "on";
    } else {
      engineLabel = "System ready"; // process up but the loop isn't started ⇔ Overview shows Start Engine
      engineTone = "neutral";
    }
  } else if (engine.status === "starting") {
    engineLabel = "System booting…";
    engineTone = "neutral";
  } else if (engine.status === "restarting") {
    // LSR R8 (#2261): a benign, expected restart — neutral, not "offline".
    engineLabel = "System restarting…";
    engineTone = "neutral";
  } else if (engine.status === "error") {
    engineLabel = "System error";
    engineTone = "off";
  } else if (engine.status === "stopped") {
    engineLabel = "System stopped";
    engineTone = "neutral";
  } else {
    engineLabel = "System offline";
    engineTone = "neutral";
  }

  // Agents = active specialist reports + the senate roster (same across decisions).
  const agentCount = specialistReports.length + (roundTable[0]?.senators.length ?? 0);
  const agents = agentCount > 0 ? String(agentCount) : "—";
  // INC-2 (#2213): single source of truth. isLive reads /health.paper_trading
  // (store.paperTrading) — a genuine live engine must show 'Live', never a hardcoded
  // 'Paper'. paperTrading is null until the first health poll → treated as not-live.
  const trading = tradingLabel(strategyRunning, paperTrading === false, marketOpen, componentsDegraded);

  // ── Sidebar engine control (Layout F) ──────────────────────────────────────
  const { handleKill, handleResetKill, killResetting } = useKillSwitch();
  const sidebarStatusMsg = useStore((s) => s.sidebarStatusMsg);
  const setSidebarStatusMsg = useStore((s) => s.setSidebarStatusMsg);
  const [showConfirmModal, setShowConfirmModal] = useState(false);

  useEffect(() => {
    if (engineRunning) { setSidebarStatusMsg(null); }
  }, [engineRunning, setSidebarStatusMsg]);

  // #2441 INC-1: the sidebar no longer starts the loop directly (paper auto-starts on
  // boot; going live is a consented flow). "Start Live Trading" launches TODAY'S existing
  // #2465 go-live consent: just raise the one-shot signal — the global LiveConsentModal (mounted in
  // DesktopApp) opens the WORM/ACK confirm OVER the current page (typically the Overview). No Settings
  // detour anymore. Same WORM/restart path as elsewhere — never a second divergent live path.
  const setRequestLiveConsent = useStore((s) => s.setRequestLiveConsent);
  // #2708 D2: mirror LiveTradingSwitchCard.select() — refuse to raise a live-arming intent while the
  // account state is unknown, a switch is in flight, or the engine is halted. The handler is the
  // correctness boundary (a keyboard activation or a future caller can't bypass it); the button's
  // `disabled` (below) is the honesty boundary (don't invite a click that will be refused).
  const switching = switchReconciling || modeSwitch != null;
  const goToLiveConsent = () => {
    if (paperTrading == null || switching || systemHalted === true) return;
    setRequestLiveConsent(true);
  };

  return (
    <aside className="w-[210px] shrink-0 flex flex-col bg-black/40 border-r border-white/5">
      {/* Top-left slot (GTM-1 #1915): the Upgrade CTA for Junior desktops, else the
          Chat launcher (ENT-only). The brand mark lives in the title bar. */}
      {(canUpgrade || showChat || isSenior) && (
        <>
          <div className="px-4 pt-4 pb-3">
            {canUpgrade ? (
              <UpgradeCta refetch={refetchEntitlement} />
            ) : isSenior ? (
              <PlansCta refetch={refetchEntitlement} />
            ) : (
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
            )}
          </div>

          <div className="hairline mx-4" />
        </>
      )}

      <nav className="flex-1 px-3 py-3 space-y-0.5">
        {navItems(simulationEnabled === true).map(({ id, label, Icon }) => {
          const active = desktopPage === id;
          const isSettings = id === "settings";

          return (
            <div key={id} className="space-y-0.5">
              <button
                onClick={() => {
                  setDesktopPage(id);
                  if (isSettings) {
                    // Reset to trading when clicking main Settings button
                    if (typeof window !== "undefined") {
                      window.location.hash = "trading";
                      window.dispatchEvent(new Event("hashchange"));
                    }
                  }
                }}
                className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-[12.5px] transition-all ${
                  active
                    ? "bg-white/8 text-white/92 border border-white/10"
                    : "text-white/55 hover:text-white/92 hover:bg-white/4 border border-transparent"
                }`}
              >
                <Icon width={14} height={14} className={active ? "text-white/92" : ""} />
                <span className="flex-1 text-left">{label}</span>
              </button>

              {isSettings && active && (
                <div className="pl-4 pr-1 py-1 space-y-0.5 border-l border-white/5 ml-4 mt-1">
                  {[
                    { subId: "trading", subLabel: "Trading" },
                    { subId: "system", subLabel: "System" },
                    { subId: "notifications", subLabel: "Notifications", badge: "BETA" },
                    { subId: "about", subLabel: "About" },
                    { subId: "legal", subLabel: "Legal" },
                  ].map(({ subId, subLabel, badge }) => {
                    const subActive = activeSettingsTab === subId;
                    return (
                      <button
                        key={subId}
                        onClick={() => {
                          if (typeof window !== "undefined") {
                            window.location.hash = subId;
                            window.dispatchEvent(new Event("hashchange"));
                          }
                        }}
                        className={`w-full flex items-center px-3 py-1.5 rounded-md text-[11.5px] transition-all ${
                          subActive
                            ? "bg-white/8 text-white/90"
                            : "text-white/45 hover:text-white/80 hover:bg-white/[0.02]"
                        }`}
                      >
                        <span className="flex-1 text-left">{subLabel}</span>
                        {badge && (
                          <span aria-hidden="true" className="text-[7.5px] font-semibold tracking-wider px-1 py-px rounded bg-white/10 text-white/55 ml-1 leading-none">
                            {badge}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      <div className="hairline mx-4" />

      {/* System-status footer (Layout F: inline engine control) */}
      <div className="px-4 py-5 space-y-3.5">
        {/* #2441 INC-1: engine control is a Senior (PRO) affordance. Junior desktops run
            paper autonomously (auto-started on boot) and see NO engine-control button here.
            Senior: KILL SWITCH while the loop runs · RESET when halted · else the green
            "Start Live Trading" that opens the consent flow (never auto-arms live). */}
        {isSenior && (
          engineBooting ? (
            <button
              aria-label="Starting"
              disabled
              className="w-full rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase text-white bg-[#00c27a] transition-all disabled:cursor-not-allowed flex items-center justify-center gap-2"
              style={{ opacity: 0.6 }}
            >
              <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              STARTING&#8230;
            </button>
          ) : systemHalted === true ? (
            // #2708 D3: a halted engine offers RESET (clear the kill switch + resume — the resumed
            // loop stays PAPER, fixed by the boot-built broker client), NOT "go live". The RESETTING…
            // disabled state makes the /reset-kill-switch + /start-live pair non-double-submittable.
            <button
              onClick={handleResetKill}
              disabled={killResetting}
              className="w-full rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase text-white bg-white/10 hover:bg-white/15 border border-white/15 transition-all transform active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {killResetting ? "RESETTING…" : "Reset"}
            </button>
          ) : paperTrading === false ? (
            <button
              aria-label="Kill Switch"
              onClick={() => setShowConfirmModal(true)}
              className="w-full rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide text-white bg-[#ff5a52] hover:bg-[#ff6c65] transition-all transform active:scale-[0.97]"
            >
              KILL SWITCH
            </button>
          ) : (
            <button
              aria-label="Start live trading"
              onClick={goToLiveConsent}
              disabled={paperTrading == null || switching}
              className="w-full rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase text-white bg-[#00c27a] hover:bg-[#11d089] transition-all transform active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-60"
            >
              Live Trading
            </button>
          )
        )}

        {/* Engine still initializing — tell the operator to wait instead of letting the click 500. */}
        {engineBooting && !sidebarStatusMsg && (
          <div className="text-[11px] text-white/50 leading-relaxed">
            Engine is starting (a few seconds)
          </div>
        )}

        {/* Inline status message (directly under button) */}
        {sidebarStatusMsg && (
          <div className="text-[11px] text-white/50 leading-relaxed animate-pulse" title={sidebarStatusMsg}>
            {sidebarStatusMsg}
          </div>
        )}

        {/* Engine status */}
        <StatusDot tone={engineTone}>
          {engineLabel}
        </StatusDot>

        {/* Stats */}
        <div className="space-y-1.5 text-[11px] num text-white/35">
          <StatRow
            label="Agents"
            value={agents}
            title={agents === "—" ? "No specialist agents are active yet — they appear once the engine evaluates the market." : undefined}
          />
          <div
            className="flex justify-between gap-2"
            title={trading.text === "—" ? "Waiting for the engine — trading status appears once it is running (paper by default)." : undefined}
          >
            <span>Live Trading</span>
            <StatusDot tone={trading.live ? "off" : trading.text === "—" ? "neutral" : "on"}>
              {trading.text}
            </StatusDot>
          </div>
        </div>

        {/* In-app legal */}
        <div className="flex flex-wrap items-center gap-x-1.5 text-[9px] text-white/25 pt-1">
          <a href="/legal/imprint" className="hover:text-white/50">Imprint</a>
          <span>·</span>
          <a href="/legal/privacy" className="hover:text-white/50">Privacy</a>
          <span>·</span>
          <a href="/legal/risk-disclosure" className="hover:text-white/50">Risk</a>
        </div>
      </div>

      {/* Emergency-stop confirm (shared with Settings › Trading) — halt, or halt + sell all. */}
      <EmergencyStopModal
        open={showConfirmModal}
        onClose={() => setShowConfirmModal(false)}
        onHalt={handleKill}
      />
    </aside>
  );
}
