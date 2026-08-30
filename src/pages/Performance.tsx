/**
 * /performance — public read-only page showing the AAAgents paper portfolio
 * equity curve vs. S&P 500 over the same window.
 *
 * Reuses BenchmarkEquityChart (dashboard component) verbatim. The chart calls
 * fetchBenchmarkEquity() which today hits /benchmark-equity via the relative
 * path. On aaagents.de that currently falls through to the SPA catch-all
 * because the Cloud Run proxy rewrite was removed in PR #766 while the deploy
 * SA is missing roles/run.viewer; the chart handles the empty payload and
 * shows its "No equity data available yet" state until papa restores the
 * IAM grant + a follow-up PR re-adds the rewrites.
 */
import { useNavigate } from "react-router-dom";
import { BenchmarkEquityChart } from "@/components/BenchmarkEquityChart";
import "@/styles/landing-b.css";

export default function Performance() {
    const navigate = useNavigate();

    return (
        <div className="landing-b-root" style={{ minHeight: "100vh", background: "#fff", color: "#000" }}>
            <div className="lb-risk-banner">
                <a
                    href="#"
                    onClick={(e) => { e.preventDefault(); navigate("/"); }}
                >
                    ‹ Back to aaagents.de
                </a>
            </div>

            <nav className="lb-nav">
                <div className="lb-nav-logo">aaagents<span style={{ color: "#00c27a" }}>_</span></div>
                <div className="lb-nav-right">
                    <a
                        className="lb-nav-link lb-nav-github"
                        href="https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases"
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label="GitHub"
                        title="GitHub"
                    >
                        <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
                        </svg>
                    </a>
                    <a className="lb-cta-primary" href="/#waitlist" onClick={(e) => { e.preventDefault(); navigate("/#waitlist"); }}>Join waitlist</a>
                </div>
            </nav>

            <main style={{ maxWidth: 960, margin: "0 auto", padding: "48px var(--lb-gutter, 24px)" }}>
                <header style={{ marginBottom: 32 }}>
                    <div className="lb-eyebrow" style={{ color: "rgba(0,0,0,0.5)" }}>Paper portfolio · live since launch</div>
                    <h1 style={{ fontSize: 44, lineHeight: 1.1, margin: "8px 0 12px" }}>Performance</h1>
                    <p style={{ color: "rgba(0,0,0,0.6)", maxWidth: 640, lineHeight: 1.55 }}>
                        Daily mark-to-market of the AAAgents paper portfolio, alongside the S&P 500 over the same window.
                        Both lines are cumulative percentage return from start; the chart updates automatically.
                    </p>
                </header>

                <BenchmarkEquityChart />

                <section style={{ marginTop: 40, fontSize: 13, color: "rgba(0,0,0,0.55)", lineHeight: 1.6 }}>
                    <p>
                        Paper trading means no real capital is at risk; the bot trades against broker market data with
                        simulated fills. Results here are a live record and are <strong>not a solicitation or offer</strong> &mdash;
                        see the <a href="/legal/risk-disclosure" onClick={(e) => { e.preventDefault(); navigate("/legal/risk-disclosure"); }} style={{ color: "inherit", textDecoration: "underline" }}>full risk disclosure</a>.
                    </p>
                </section>
            </main>
        </div>
    );
}
