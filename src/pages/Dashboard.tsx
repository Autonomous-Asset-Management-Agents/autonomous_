/**
 * Dashboard view — Primary interface for the AAAgents Container. Renders the real paper portfolio
 * (DashboardView + PortfolioView + SimulationView) with full controls.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, Link } from "react-router-dom";
import { DashboardView } from "@/components/views/DashboardView";
import { PortfolioView } from "@/components/views/PortfolioView";
import { SimulationView } from "@/components/views/SimulationView";
import { fetchPortfolioSummary, fetchBenchmarkEquity } from "@/lib/api";
import PricingPreview from "./PricingPreview";
import "@/styles/landing-b.css";

type Tab = "dashboard" | "portfolio" | "simulation" | "about" | "pricing";

export default function Dashboard() {
    const navigate = useNavigate();
    const [tab, setTab] = useState<Tab>("dashboard");



    const { data: portfolioData } = useQuery({
        queryKey: ["public-portfolio-summary"],
        queryFn: fetchPortfolioSummary,
        refetchInterval: 15000,
        retry: 1,
    });

    const { data: benchmarkData } = useQuery({
        queryKey: ["benchmark-equity"],
        queryFn: fetchBenchmarkEquity,
        refetchInterval: 60000,
        retry: 1,
    });

    const positions = portfolioData?.status === "success" && portfolioData.positions
        ? portfolioData.positions.map((p) => ({
            symbol: p.symbol,
            qty: p.qty,
            market_value: p.market_value,
            unrealized_pnl: p.unrealized_pnl ?? 0,
            unrealized_pnl_pct: p.unrealized_pnl_pct ?? 0,
        }))
        : [];

    return (
        <div className="landing-b-root" style={{ minHeight: "100vh", background: "#000", color: "#fff" }}>
            {/* Top bar — read-only notice */}
            <div className="lb-risk-banner" style={{ background: "#0b0b0b", color: "#9a9a9a", borderBottomColor: "#1a1a1a" }}>
                <span style={{ fontFamily: "var(--lb-mono)", fontSize: 12, letterSpacing: 2, textTransform: "uppercase" }}>
                    AAAgents Console · Engine Online
                </span>
            </div>

            <nav className="lb-nav" style={{ borderBottom: "1px solid #1a1a1a" }}>
                <div className="lb-nav-logo" style={{ color: "#fff" }}>
                    autonomous<span style={{ color: "#00c27a" }}>_</span>
                </div>
                <div className="lb-nav-right">
                    <button className="lb-nav-link" style={{ color: tab === "dashboard" ? "#00c27a" : "#9a9a9a" }} onClick={() => setTab("dashboard")}>Overview</button>
                    <button className="lb-nav-link" style={{ color: tab === "portfolio" ? "#00c27a" : "#9a9a9a" }} onClick={() => setTab("portfolio")}>Portfolio</button>
                    <button className="lb-nav-link" style={{ color: tab === "simulation" ? "#00c27a" : "#9a9a9a" }} onClick={() => setTab("simulation")}>Simulation</button>
                    <button className="lb-nav-link" style={{ color: tab === "about" ? "#00c27a" : "#9a9a9a" }} onClick={() => setTab("about")}>About</button>
                    <button className="lb-nav-link" style={{ color: tab === "pricing" ? "#3b82f6" : "#9a9a9a", marginLeft: 16, fontWeight: "bold" }} onClick={() => setTab("pricing")}>💳 PRICING PREVIEW</button>
                </div>
            </nav>

            <main style={{ minHeight: "calc(100vh - 140px)", padding: "24px var(--lb-gutter)" }}>
                {tab === "dashboard" && (
                    <DashboardView
                        equity={portfolioData?.status === "success" ? portfolioData.equity : undefined}
                        lastEquity={portfolioData?.status === "success" ? portfolioData.last_equity : undefined}
                        positions={positions}
                        isConnected={portfolioData?.status === "success"}
                        agentStatuses={portfolioData?.agent_statuses}
                    />
                )}
                {tab === "portfolio" && <PortfolioView positions={positions} equity={portfolioData?.status === "success" ? portfolioData.equity : undefined} startingCapital={benchmarkData?.initial_capital} />}
                {tab === "simulation" && <SimulationView />}
                {tab === "about" && (
                    <div style={{ maxWidth: 800, margin: "0 auto", padding: "40px 0" }}>
                        <h2 style={{ fontSize: 32, fontWeight: 800, marginBottom: 24 }}>Notice Infos & Legal</h2>
                        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                            <Link id="legal-link-notice" to="/legal/notice" style={{ textAlign: "left", fontSize: 18, color: "#00c27a", textDecoration: "underline" }}>Legal Notice & Attributions (NOTICE)</Link>
                            <Link id="legal-link-imprint" to="/legal/imprint" style={{ textAlign: "left", fontSize: 18, color: "#00c27a", textDecoration: "underline" }}>Imprint (§ 5 TMG)</Link>
                            <Link id="legal-link-privacy" to="/legal/privacy" style={{ textAlign: "left", fontSize: 18, color: "#00c27a", textDecoration: "underline" }}>Privacy Policy (GDPR)</Link>
                            <Link id="legal-link-risk" to="/legal/risk-disclosure" style={{ textAlign: "left", fontSize: 18, color: "#00c27a", textDecoration: "underline" }}>Risk Disclosure</Link>
                        </div>
                    </div>
                )}
                {tab === "pricing" && <PricingPreview />}
            </main>
        </div>
    );
}
