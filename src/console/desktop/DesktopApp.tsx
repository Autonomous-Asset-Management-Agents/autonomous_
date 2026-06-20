import { useEffect } from "react";
import { useStore, type ConsolePage } from "@/console/store/useStore";
import { Sidebar } from "./Sidebar";
import { TitleBar } from "./TitleBar";
import { Chat } from "./pages/Chat";
import { Positions } from "./pages/Positions";
import { Reports } from "./pages/Reports";
import { Overview } from "./pages/Overview";
import { AuditChain } from "./pages/AuditChain";
import { Settings } from "./pages/Settings";
import { Placeholder } from "./pages/Placeholder";
import { ErrorBoundary } from "@/console/shared/ErrorBoundary";

// Sidebar-reachable page keys also accepted as ?page= direct-nav targets.
const VALID_PAGES = new Set<ConsolePage>([
  "overview",
  "decisions",
  "positions",
  "reports",
  "audit",
  "settings",
  "chat",
]);

/**
 * The operator console shell (G3, #1050): title bar + sidebar + the active
 * page. Chat is live; the data pages render an honest placeholder until their
 * own slices land. Decisions is the HITL/GAP2 stub. "One frontend for all
 * editions" — engine calls are edition-switched at the API layer, so the shell
 * needs no per-edition branching.
 */
export function DesktopApp() {
  const page = useStore((s) => s.desktopPage);
  const setDesktopPage = useStore((s) => s.setDesktopPage);

  // Direct-URL navigation: read ?page= once on mount.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const requested = new URLSearchParams(window.location.search).get("page");
      if (requested && VALID_PAGES.has(requested as ConsolePage)) {
        setDesktopPage(requested as ConsolePage);
      }
    } catch {
      /* URL parsing failure is non-critical */
    }
    // setDesktopPage is a stable zustand action, so this effect runs once.
  }, [setDesktopPage]);

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
            {page === "reports" && <Reports />}
            {page === "audit" && <AuditChain />}
            {page === "settings" && <Settings />}
            {page === "decisions" && (
              <Placeholder
                title="Decisions"
                note="Human-in-the-loop approval needs engine endpoints that don't exist on main yet (GAP2). It ships as its own compliance-track plan, not part of the UI port."
              />
            )}
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
