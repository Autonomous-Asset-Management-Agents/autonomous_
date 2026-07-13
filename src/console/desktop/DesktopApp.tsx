import { useEffect } from "react";
import { useStore, type ConsolePage } from "@/console/store/useStore";
import { Sidebar } from "./Sidebar";
import { TitleBar } from "./TitleBar";
import { Chat } from "./pages/Chat";
import { Positions } from "./pages/Positions";
import { Activities } from "./pages/Activities";
import { Reports } from "./pages/Reports";
import { Overview } from "./pages/Overview";
import { AuditChain } from "./pages/AuditChain";
import { Settings } from "./pages/Settings";
import { Decisions } from "./pages/Decisions";
import { Simulation } from "./pages/Simulation";
import { ErrorBoundary } from "@/console/shared/ErrorBoundary";
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

  // Direct-URL navigation: read ?page= once on mount.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const requested = new URLSearchParams(window.location.search).get("page");
      if (requested && validPages(simulationEnabled === true).has(requested as ConsolePage)) {
        setDesktopPage(requested as ConsolePage);
      }
    } catch {
      /* URL parsing failure is non-critical */
    }
    // setDesktopPage is a stable zustand action, so this effect runs once.
  }, [setDesktopPage, simulationEnabled]);

  return (
    <div className="window-shell w-full h-full flex flex-col overflow-hidden">
      <TitleBar />
      <div className="flex-1 flex min-h-0">
        <Sidebar />
        <main className="flex-1 overflow-y-auto bg-black">
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
    </div>
  );
}
