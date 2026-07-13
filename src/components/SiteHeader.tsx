import type { CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
import "./SiteHeader.css";

/**
 * Canonical site header — ONE shared nav (logo + LinkedIn + GitHub + Demo) used by the
 * marketing landing AND the public LiveDemo, so the header is 1:1 across pages instead of copied
 * per page. The "lb-nav" markup was inlined in LandingViewB/D/E and again (differently) in
 * LiveDemo; this component is the single source. Markup + classes mirror the landing lb-nav;
 * styling lives in SiteHeader.css (re-scoped to `.site-header`).
 */
const MENU_BTN: CSSProperties = {
  width: "auto",
  padding: "0 12px",
  background: "transparent",
  border: "none",
  textDecoration: "none",
  color: "inherit",
  fontWeight: 600,
  fontSize: "11px",
  letterSpacing: "1px",
  textTransform: "uppercase",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

export function SiteHeader({ dark = false }: { dark?: boolean } = {}) {
  const navigate = useNavigate();

  return (
    <header className={`site-header${dark ? " site-header--dark" : ""}`}>
      <nav className="lb-nav">
        <button
          type="button"
          onClick={() => navigate("/preview")}
          aria-label="To homepage"
          title="To homepage"
          style={{ display: "flex", alignItems: "center", background: "none", border: "none", cursor: "pointer", padding: 0 }}
        >
          <img src="/aaagents_logo_linkedin.png" alt="AAAgents Logo" style={{ height: "32px", width: "auto" }} />
        </button>
        <div className="lb-nav-social">
          <a
            href="https://www.linkedin.com/company/aaa-autonomous-asset-management-agents/posts/?feedView=all"
            target="_blank"
            rel="noopener"
            className="lb-nav-social-link"
            aria-label="Follow us on LinkedIn"
            title="Follow us on LinkedIn"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
            </svg>
          </a>
          <a
            href="https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases"
            target="_blank"
            rel="noopener"
            className="lb-nav-social-link"
            aria-label="View on GitHub"
            title="View on GitHub"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.1.79-.25.79-.56 0-.28-.01-1.02-.02-2-3.2.7-3.87-1.54-3.87-1.54-.52-1.33-1.28-1.69-1.28-1.69-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18.92-.26 1.91-.39 2.9-.39.99 0 1.98.13 2.9.39 2.21-1.49 3.18-1.18 3.18-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.83 1.19 3.09 0 4.42-2.69 5.39-5.25 5.68.41.36.78 1.06.78 2.13 0 1.54-.01 2.78-.01 3.16 0 .31.21.67.8.56C20.22 21.39 23.5 17.08 23.5 12 23.5 5.65 18.35.5 12 .5z" />
            </svg>
          </a>
          <button
            className="site-header-demo-btn"
            onClick={(e) => {
              e.stopPropagation();
              navigate("/live-demo");
            }}
            aria-label="View live demo"
            title="View live demo"
          >
            LIVE DEMO
          </button>
        </div>
      </nav>
    </header>
  );
}
