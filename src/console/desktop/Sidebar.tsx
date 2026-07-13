import { useState, useEffect } from "react";
import { useStore, type ConsolePage } from "@/console/store/useStore";
import { IconChat, IconChevronRight } from "@/console/shared/Icons";
import { StatusDot } from "@/console/shared/StatusDot";
import { navItems } from "./nav";
import { useEngine } from "@/console/live/useEngine";
import { useHealthPolling } from "@/console/live/useHealthPolling";
import { useEntitlementPolling } from "@/console/live/useEntitlementPolling";
import { claimBeta } from "@/lib/desktopBridge";
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
/**
 * Upgrade CTA (GTM-1 #1915 · ADR-GTM-1b) — the top-left sidebar slot for Junior
 * (BASIC) desktops. Clicking claims the free-beta Senior unlock via the Electron
 * IPC bridge (claimBeta → the shell copies the bundled OFFLINE license into
 * license.json; no cloud, no login). On a successful claim the entitlement
 * re-polls (Senior now → canUpgrade flips false → this CTA unmounts). Anything
 * else — an absent bridge (browser), a claim error, or the free-beta cap —
 * surfaces a note instead of silently doing nothing. Styled like the Overview
 * kill-switch button — a solid pill in our green (#00c27a) with white, bold,
 * uppercase text (supersedes #1983's dark-on-green pill).
 */
export function UpgradeCta({ refetch }: { refetch: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const onClick = async () => {
    if (busy) return;
    setBusy(true);
    setNote(null);
    try {
      const res = await claimBeta();
      if (res.status === "claimed") {
        await refetch(); // Senior now → canUpgrade flips false → CTA unmounts.
      } else if (res.status === "cap-reached") {
        setNote("Beta full — Senior is now €0.99/month.");
      } else {
        // Never fail silently: an absent desktop bridge (browser) or a claim
        // error must be visible to the operator.
        setNote(
          res.error === "desktop-only"
            ? "Available in the desktop app."
            : "Upgrade failed — please try again.",
        );
      }
    } catch {
      setNote("Upgrade failed — please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        onClick={onClick}
        disabled={busy}
        className="w-full rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide text-white bg-[#00c27a] hover:bg-[#00d687] border border-transparent transition-all transform active:scale-[0.98] disabled:opacity-60"
        title="Claim the free beta Senior grant — enables live trading"
      >
        {busy ? "CLAIMING…" : "UPGRADE"}
      </button>
      {note && <p className="text-[11px] text-white/55 mt-1.5 px-1">{note}</p>}
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
  const { refetch: refetchEntitlement } = useEntitlementPolling();
  const canUpgrade = useStore((s) => s.canUpgrade);
  const tier = useStore((s) => s.tier);
  // Simulation/backtest nav is entitlement-gated (hidden unless strictly enabled).
  const simulationEnabled = useStore((s) => s.simulationEnabled);
  // The "Chat" launcher is now ENT-only. Hide it for the gated desktop tiers
  // (Junior=BASIC, Senior=PRO); show it while the tier is unknown/ENT (null →
  // cloud/browser build, or a resolved ENT-eligible tier).
  const showChat = tier == null || (tier !== "BASIC" && tier !== "PRO");
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
      {/* Top-left slot (GTM-1 #1915): the Upgrade CTA for Junior desktops, else the
          Chat launcher (ENT-only). The brand mark lives in the title bar. */}
      {(canUpgrade || showChat) && (
        <>
          <div className="px-3 pt-4 pb-3">
            {canUpgrade ? (
              <UpgradeCta refetch={refetchEntitlement} />
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

      {/* System-status footer */}
      <div className="p-4 space-y-2.5">
        <div className="flex items-center gap-2">
          <StatusDot
            tone={online ? "on" : engine.status === "error" ? "off" : "neutral"}
            className="!text-[10px] uppercase tracking-wider"
          >
            {online ? "Engine running" : engine.status === "error" ? "Engine error" : "Engine offline"}
          </StatusDot>
        </div>
        <div className="space-y-1 text-[10px] num text-white/30">
          {/* C-D: a tooltip turns the ambiguous "—" into "not started yet", not "broken". */}
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
