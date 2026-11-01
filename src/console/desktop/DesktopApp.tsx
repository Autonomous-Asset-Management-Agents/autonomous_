import { useEffect, useRef } from "react";
import { useStore, type ConsolePage } from "@/console/store/useStore";
import { useResetScrollOnSwitchReveal } from "./useResetScrollOnSwitchReveal";
import { Sidebar } from "./Sidebar";
import { TitleBar } from "./TitleBar";
import { Chat } from "./pages/Chat";
import { Positions } from "./pages/Positions";
import { Activities } from "./pages/Activities";
import { Reports } from "./pages/Reports";
import { Overview } from "./pages/Overview";
import { AuditChain } from "./pages/AuditChain";
import { Settings } from "./pages/Settings";
import { UpdateBanner } from "./UpdateBanner";
import { Decisions } from "./pages/Decisions";
import { Simulation } from "./pages/Simulation";
import { ErrorBoundary } from "@/console/shared/ErrorBoundary";
import { LiveConsentModal } from "./LiveConsentModal";
import { validPages } from "./nav";

/**
 * The operator console shell (G3, #1050): title bar + sidebar + the active
 * page. Chat is live; the data pages render an honest placeholder until their
 * own slices land. Decisions lists the live Round-Table verdicts. "One frontend for all
 * editions" — engine calls are edition-switched at the API layer, so the shell
 * needs no per-edition branching.
 */
export function DesktopApp() {
  const page = useStore((s) => s.desktopPage);
  const setDesktopPage = useStore((s) => s.setDesktopPage);
  // Simulation/backtest page is entitlement-gated (hidden unless strictly enabled).
  const simulationEnabled = useStore((s) => s.simulationEnabled);

  // After a Paper↔Live switch reveals the app, snap the shared scroll container back to the
  // top so the revealed page's header (account name/equity) is immediately visible instead of
  // whatever scroll position carried over from before the switch.
  const mainRef = useRef<HTMLElement>(null);
  useResetScrollOnSwitchReveal(mainRef);

  // Direct-URL navigation: read ?page= or #hash on mount and hashchange.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const syncPage = () => {
      try {
        const searchPage = new URLSearchParams(window.location.search).get("page");
        const rawHash = window.location.hash.replace("#", "").split("/")[0];
        const target = searchPage || rawHash;
        if (target && validPages(simulationEnabled === true).has(target as ConsolePage)) {
          setDesktopPage(target as ConsolePage);
        }
      } catch {
        /* URL parsing failure is non-critical */
      }
    };
    syncPage();
    window.addEventListener("hashchange", syncPage);
    return () => window.removeEventListener("hashchange", syncPage);
  }, [setDesktopPage, simulationEnabled]);

  return (
    <div className="window-shell w-full h-full flex flex-col overflow-hidden">
      <TitleBar />
      <div className="flex-1 flex min-h-0">
        <Sidebar />
        <main className="flex-1 overflow-y-auto bg-black">
          <UpdateBanner />
          <ErrorBoundary resetKey={page} label={page}>
            {page === "chat" && <Chat />}
            {page === "overview" && <Overview />}
            {page === "positions" && <Positions />}
            {page === "activities" && <Activities />}
            {page === "reports" && <Reports />}
            {page === "audit" && <AuditChain />}
            {page === "settings" && <Settings />}
            {page === "decisions" && <Decisions />}
            {page === "simulation" && simulationEnabled === true && <Simulation />}
          </ErrorBoundary>
        </main>
      </div>
      {/* #2465: the LiveReconfirmGate ("Continue live trading?") is removed. There is no verified-live
          idle boot anymore — a live boot happens ONLY via the go-live consent (AAA_GO_LIVE) and
          auto-arms; a fresh launch/reboot always boots Paper. The consent IS the Art-14 oversight.
          The go-live consent itself is now a GLOBAL modal that opens OVER the active page (no Settings
          detour) — driven by the requestLiveConsent / requestSwitchToPaper store signals. */}
      <LiveConsentModal />
    </div>
  );
}
