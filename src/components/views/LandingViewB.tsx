/**
 * LandingViewB — approach B landing page ported from aaagents-landing/approach-b.
 *
 * All styles are scoped under `.landing-b-root` (see src/styles/landing-b.css)
 * to avoid collision with the console's shadcn/Tailwind styles. The legacy
 * LandingView.tsx remains untouched at the / route via pages/Index.tsx until
 * we flip the switch in INTEGRATION_NOTES.md step 3.
 */
import { useEffect, useRef, useState, FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { submitWaitlist, isValidEmail } from "@/lib/waitlist";
import { sendChatMessage, type ChatMessage } from "@/lib/chatClient";
import { MobileCollapse } from "@/components/variants/landing-b/MobileCollapse";
import "@/styles/landing-b.css";

type WaitlistState = "idle" | "submitting" | "thanks" | "error";

const SEED_LOG: ChatMessage[] = [
    { role: "user", content: "what did the agents buy this week?" },
    { role: "agent", agentName: "analyst", content: "screening 2,847 tickers…" },
    { role: "agent", agentName: "analyst", content: "42 candidates pass Q1 filter" },
    { role: "agent", agentName: "coord", content: "ranking by Sharpe × conviction" },
    { role: "agent", agentName: "coord", content: "top pick: NVDA (+4 % target)" },
    { role: "agent", agentName: "board", content: "vote opened · 12 members" },
    { role: "agent", agentName: "board", content: "✓ 9 yes · ✗ 3 no" },
    { role: "agent", agentName: "board", content: "quorum reached" },
];

export default function LandingViewB() {
    const navigate = useNavigate();
    const scrubRef = useRef<HTMLDivElement | null>(null);
    const rootRef = useRef<HTMLDivElement | null>(null);

    const [email, setEmail] = useState("");
    const [wlState, setWlState] = useState<WaitlistState>("idle");
    const [wlError, setWlError] = useState<string | null>(null);

    const [chatInput, setChatInput] = useState("");
    const [chatLog, setChatLog] = useState<ChatMessage[]>(SEED_LOG);
    const [chatBusy, setChatBusy] = useState(false);

    const [wlExpanded, setWlExpanded] = useState(false);

    const scrollToId = (id: string) => {
        const el = document.getElementById(id);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    };

    // Hero scroll-scrub — drives --lb-hp custom property 0→1
    useEffect(() => {
        const scrub = scrubRef.current;
        const root = rootRef.current;
        if (!scrub || !root) return;
        let ticking = false;
        const update = () => {
            const rect = scrub.getBoundingClientRect();
            const total = scrub.offsetHeight - window.innerHeight;
            let raw = 0;
            if (total > 0) raw = Math.min(Math.max(-rect.top / total, 0), 1);
            const p = Math.min(raw / 0.7, 1);
            root.style.setProperty("--lb-hp", p.toFixed(4));
            ticking = false;
        };
        const onScroll = () => {
            if (!ticking) { requestAnimationFrame(update); ticking = true; }
        };
        window.addEventListener("scroll", onScroll, { passive: true });
        window.addEventListener("resize", update);
        update();
        return () => {
            window.removeEventListener("scroll", onScroll);
            window.removeEventListener("resize", update);
        };
    }, []);

    // Reveal-on-scroll
    useEffect(() => {
        const root = rootRef.current;
        if (!root) return;
        const els = root.querySelectorAll<HTMLElement>(".lb-reveal");
        if (!("IntersectionObserver" in window) || !els.length) {
            els.forEach((el) => el.classList.add("lb-in-view"));
            return;
        }
        const io = new IntersectionObserver((entries) => {
            entries.forEach((e) => {
                if (e.isIntersecting) {
                    e.target.classList.add("lb-in-view");
                    io.unobserve(e.target);
                }
            });
        }, { rootMargin: "0px 0px -10% 0px", threshold: 0.08 });
        els.forEach((el) => io.observe(el));
        return () => io.disconnect();
    }, []);

    const handleWaitlist = async (e: FormEvent) => {
        e.preventDefault();
        if (wlState === "submitting") return;
        if (!isValidEmail(email)) {
            setWlState("error");
            setWlError("Please enter a valid email address.");
            return;
        }
        setWlState("submitting");
        setWlError(null);
        try {
            await submitWaitlist(email, "landing-b/hero-cta");
            setWlState("thanks");
            setEmail("");
        } catch (err) {
            console.warn("waitlist submit failed", err);
            setWlState("error");
            setWlError("Something went wrong. Please try again in a moment.");
        }
    };

    const handleChatSend = async (e: FormEvent) => {
        e.preventDefault();
        const text = chatInput.trim();
        if (!text || chatBusy) return;
        setChatBusy(true);
        setChatLog((l) => [...l, { role: "user", content: text }]);
        setChatInput("");
        try {
            const reply = await sendChatMessage(text);
            setChatLog((l) => [...l, reply]);
        } finally {
            setChatBusy(false);
        }
    };

    return (
        <div className="landing-b-root" ref={rootRef}>
            {/* Top risk banner */}
            <div className="lb-risk-banner">
                <a href="/legal/risk-disclosure" onClick={(e) => { e.preventDefault(); navigate("/legal/risk-disclosure"); }}>Invest with confidence. Read the full risk disclosure ›</a>
            </div>

            {/* Nav */}
            {/*
              Nav is intentionally minimal: GitHub repo link + Performance page
              + Join-waitlist CTA. Previous Agents / Compliance / Open Source /
              Login entries were removed for mobile readability (see dedicated
              MobileLandingB variant follow-up). Dev-console access is still
              available to the allowlist via direct /login navigation.

              GitHub URL points at the autonomous_ repo that will be public on
              the 2026-05-19 OSS launch. Until then the link 404s; no build
              change needed once the repo flips public.
            */}
            <nav className="lb-nav">
                <div className="lb-nav-logo">aaagents<span style={{ color: "#00c27a" }}>_</span></div>
                <div className="lb-nav-right">
                    <a
                        className="lb-nav-link lb-nav-github"
                        href="https://github.com/Autonomous-Asset-Management-Agents/autonomous_"
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label="GitHub"
                        title="GitHub"
                    >
                        <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
                        </svg>
                    </a>
                    <button className="lb-nav-link" onClick={() => navigate("/performance")}>Performance</button>
                    <a className="lb-cta-primary" href="#waitlist">Join waitlist</a>
                </div>
            </nav>

            {/* Hero */}
            <div className="lb-hero-scrub" ref={scrubRef}>
                <section className="lb-hero">
                    <div className="lb-hero-inner">
                        <div className="lb-hero-text">
                            <div className="lb-eyebrow">An AI-driven investment platform</div>
                            <h1>The Future of Autonomous Trading.<br />Safe. Auditable. Agentic.</h1>
                            <p className="lb-hero-sub">We provide the governance layer to transform AI from a decision-support tool into a controlled execution actor.</p>
                            <div className="lb-hero-ctas" id="waitlist">
                                {wlState === "thanks" ? (
                                    <div className="lb-thanks">Thanks — we'll be in touch.</div>
                                ) : (
                                    <form
                                        onSubmit={(e) => {
                                            if (!wlExpanded) {
                                                e.preventDefault();
                                                setWlExpanded(true);
                                                setTimeout(() => {
                                                    document.getElementById("lb-wl-email")?.focus();
                                                }, 50);
                                                return;
                                            }
                                            handleWaitlist(e);
                                        }}
                                        style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}
                                    >
                                        {wlExpanded && (
                                            <input
                                                id="lb-wl-email"
                                                type="email"
                                                required
                                                placeholder="you@email.com"
                                                value={email}
                                                onChange={(e) => setEmail(e.target.value)}
                                                style={{
                                                    padding: "14px 20px", borderRadius: 999,
                                                    border: "1px solid #d0d0d0", fontSize: 15,
                                                    fontFamily: "inherit", minWidth: 220,
                                                    outline: "none",
                                                }}
                                            />
                                        )}
                                        <button type="submit" className="lb-cta-primary" disabled={wlState === "submitting"}>
                                            {wlState === "submitting" ? "Sending…" : "Keep me updated"}
                                        </button>
                                        {!wlExpanded && (
                                            <button type="button" className="lb-cta-ghost" onClick={() => navigate("/live")}>
                                                View it in action ›
                                            </button>
                                        )}
                                    </form>
                                )}
                            </div>
                            {wlError && <div className="lb-error">{wlError}</div>}
                            <div className="lb-hero-oss-strip">
                                <span className="lb-hero-oss-pill" onClick={() => scrollToId("section-oss")}>
                                    <span className="lb-hero-oss-dot" /> Open-source community edition · launching 2026-05-19 ›
                                </span>
                            </div>
                        </div>
                        <div className="lb-hero-terminal" aria-hidden="false">
                            <div className="lb-bar"><span /><span /><span /></div>
                            <div className="lb-log">
                                {chatLog.slice(-11).map((m, i) => (
                                    <div className="lb-line" key={i}>
                                        <span className="lb-prompt">
                                            {m.role === "user" ? "you   >" : `${(m.agentName ?? "agent").padEnd(6).slice(0, 7)} >`}
                                        </span>{" "}
                                        {m.content}
                                    </div>
                                ))}
                                {chatBusy && (
                                    <div className="lb-line"><span className="lb-prompt">agent &gt;</span> <span className="lb-cursor" /></div>
                                )}
                            </div>
                            <form className="lb-chat-input" onSubmit={handleChatSend}>
                                <input
                                    placeholder="ask the agents anything…"
                                    value={chatInput}
                                    onChange={(e) => setChatInput(e.target.value)}
                                />
                                <button type="submit" className="lb-send">send ›</button>
                            </form>
                        </div>
                    </div>
                    <div className="lb-hero-inner-2" aria-hidden="true">
                        <div className="lb-hero-inner-2-track">
                            <h2 className="lb-hero-h2">Build a full hedgefund<br />at your disposal.</h2>
                            <p className="lb-hero-h2-sub">Rational investment decisions — transparent, compliant, and in line with your investment strategy.</p>
                        </div>
                    </div>
                </section>
            </div>

            {/* S2 — Research pipeline */}
            <section className="lb-section" id="section-research">
                <div className="lb-section-grid">
                    <div>
                        <div className="lb-eyebrow lb-reveal">01 · Analytics</div>
                        <h2 className="lb-reveal lb-r-delay-1">Agents do<br />the research.</h2>
                        <p className="lb-lede lb-reveal lb-r-delay-1">Multi-agent intelligence with deterministic safety rails.</p>
                        <MobileCollapse>
                            <ul className="lb-bullets lb-reveal lb-r-delay-2">
                                <li><b>Diverse intelligence streams</b><span className="lb-muted">specialized agents synthesize alternative data, sentiment, historical volatility regimes, and real-time liquidity depth.</span></li>
                                <li><b>Modular agent architecture</b><span className="lb-muted">includes sophisticated modules such as NeuralSequenceAgents (temporal dependencies) and RegimeDetectionAgents (market shifts).</span></li>
                                <li><b>The Investment Board votes</b><span className="lb-muted">before a single order is placed — a majority of the 12-member board or nothing ships.</span></li>
                            </ul>
                        </MobileCollapse>
                    </div>
                    <div className="lb-viz-terminal lb-reveal lb-r-delay-3">
                        <div className="lb-bar"><span /><span /><span /></div>
                        <div className="lb-line"><span className="lb-prompt">board &gt;</span> motion: long NVDA 4 %</div>
                        <div className="lb-line"><span className="lb-dim">────────────────────────────</span></div>
                        <div className="lb-line">buffett   <span className="lb-ok">✓ yes</span> <span className="lb-dim">· quality compounding</span></div>
                        <div className="lb-line">dalio     <span className="lb-ok">✓ yes</span> <span className="lb-dim">· low corr to book</span></div>
                        <div className="lb-line">burry     <span className="lb-err">✗ no</span>  <span className="lb-dim">· valuation stretched</span></div>
                        <div className="lb-line">lynch     <span className="lb-ok">✓ yes</span> <span className="lb-dim">· tam expansion</span></div>
                        <div className="lb-line">wood      <span className="lb-ok">✓ yes</span> <span className="lb-dim">· secular thesis</span></div>
                        <div className="lb-line">graham    <span className="lb-ok">✓ yes</span> <span className="lb-dim">· margin of safety ok</span></div>
                        <div className="lb-line">soros     <span className="lb-err">✗ no</span>  <span className="lb-dim">· reflexive top near</span></div>
                        <div className="lb-line">icahn     <span className="lb-ok">✓ yes</span> <span className="lb-dim">· cashflow intact</span></div>
                        <div className="lb-line">druck     <span className="lb-ok">✓ yes</span> <span className="lb-dim">· macro tailwind</span></div>
                        <div className="lb-line">catalyst  <span className="lb-ok">✓ yes</span> <span className="lb-dim">· 8-K surprise</span></div>
                        <div className="lb-line">setup     <span className="lb-err">✗ no</span>  <span className="lb-dim">· chart base incomplete</span></div>
                        <div className="lb-line">contra    <span className="lb-ok">✓ yes</span> <span className="lb-dim">· sentiment not euphoric</span></div>
                        <div className="lb-line"><span className="lb-dim">────────────────────────────</span></div>
                        <div className="lb-line"><span className="lb-ok">■ PASS</span> 9 / 12  <span className="lb-dim">→ order routed</span></div>
                    </div>
                </div>
            </section>

            {/* S3 — Agentic Orchestration (light zone) */}
            <section className="lb-section lb-reverse">
                <div className="lb-section-grid">
                    <div>
                        <div className="lb-eyebrow lb-reveal">04 · Agentic Orchestration</div>
                        <h2 className="lb-reveal lb-r-delay-1">The investment<br />board decides.</h2>
                        <p className="lb-lede lb-reveal lb-r-delay-1">We eliminate the "AI black box" by enforcing a traceable identity layer on every autonomous action.</p>
                        <MobileCollapse>
                            <ul className="lb-bullets lb-reveal lb-r-delay-2">
                                <li><b>Identifiable voting</b><span className="lb-muted">every agent carries a unique ID. All trade recommendations are logged as named votes — a transparent audit trail of the logic behind every decision.</span></li>
                                <li><b>Weighted consensus</b><span className="lb-muted">decisions are reached via asymmetrically weighted voting, mitigating single-model bias and correlation risk.</span></li>
                                <li><b>Strategic mandates</b><span className="lb-muted">the human role shifts from manual approval to Mandate Design — defining the boundaries and risk appetites within which agents operate.</span></li>
                            </ul>
                        </MobileCollapse>
                    </div>
                    <div className="lb-viz-donut lb-reveal lb-r-delay-3">
                        <svg viewBox="0 0 120 120" style={{ width: "100%", height: "auto" }}>
                            <circle cx="60" cy="60" r="46" fill="none" stroke="#e9e9e9" strokeWidth="14" />
                            <circle cx="60" cy="60" r="46" fill="none" stroke="#000" strokeWidth="14" strokeDasharray="98 289" transform="rotate(-90 60 60)" />
                            <circle cx="60" cy="60" r="46" fill="none" stroke="#00c27a" strokeWidth="14" strokeDasharray="72 289" strokeDashoffset="-98" transform="rotate(-90 60 60)" />
                            <circle cx="60" cy="60" r="46" fill="none" stroke="#666" strokeWidth="14" strokeDasharray="67 289" strokeDashoffset="-170" transform="rotate(-90 60 60)" />
                            <circle cx="60" cy="60" r="46" fill="none" stroke="#bbb" strokeWidth="14" strokeDasharray="52 289" strokeDashoffset="-237" transform="rotate(-90 60 60)" />
                            <text x="60" y="58" textAnchor="middle" fontFamily="Inter" fontWeight="800" fontSize="14">12 / 12</text>
                            <text x="60" y="72" textAnchor="middle" fontFamily="JetBrains Mono" fontSize="6" fill="#888">WEIGHTED</text>
                        </svg>
                        <div className="lb-legend">
                            <div className="lb-row"><span className="lb-dot" style={{ background: "#000" }} />Risk &amp; Compliance <span className="lb-pct">0.34</span></div>
                            <div className="lb-row"><span className="lb-dot" style={{ background: "#00c27a" }} />Fundamentals <span className="lb-pct">0.25</span></div>
                            <div className="lb-row"><span className="lb-dot" style={{ background: "#666" }} />Macro &amp; Technical <span className="lb-pct">0.23</span></div>
                            <div className="lb-row"><span className="lb-dot" style={{ background: "#bbb" }} />Pattern &amp; Catalyst <span className="lb-pct">0.18</span></div>
                        </div>
                    </div>
                </div>
            </section>

            {/* S4 — Compliance Guardian (light zone) */}
            <section className="lb-section" id="section-compliance">
                <div className="lb-section-grid">
                    <div>
                        <div className="lb-eyebrow lb-reveal">05 · Compliance Guardian</div>
                        <h2 className="lb-reveal lb-r-delay-1">The iron dome<br />protects.</h2>
                        <p className="lb-lede lb-reveal lb-r-delay-1">A three-tier execution environment that constrains agents within non-negotiable regulatory and risk boundaries.</p>
                        <MobileCollapse>
                            <ul className="lb-bullets lb-reveal lb-r-delay-2">
                                <li><b>Layer 1 · Compliance Guardian</b><span className="lb-muted">order throttling, anti-wash-trading, and MiFID II blocklisting.</span></li>
                                <li><b>Layer 2 · cuFOLIO optimizer</b><span className="lb-muted">mean-CVaR portfolio construction — concentration caps, sector limits, and drawdown-aware position sizing.</span></li>
                                <li><b>Layer 3 · Risk Manager</b><span className="lb-muted">VIX-indexed sizing, single-trade stop-loss, and daily drawdown kill-switch.</span></li>
                            </ul>
                        </MobileCollapse>
                    </div>
                    <div className="lb-viz-dome lb-reveal lb-r-delay-3">
                        <svg viewBox="0 0 360 300" style={{ width: "100%", height: "auto" }}>
                            <defs>
                                <linearGradient id="lbDomeGrad" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="0%" stopColor="#00c27a" stopOpacity="0.18" />
                                    <stop offset="100%" stopColor="#00c27a" stopOpacity="0.02" />
                                </linearGradient>
                            </defs>
                            <line x1="20" y1="260" x2="340" y2="260" stroke="#111" strokeWidth="1.5" />
                            <path d="M 30 260 A 150 150 0 0 1 330 260" fill="url(#lbDomeGrad)" stroke="#111" strokeWidth="1.5" />
                            <path d="M 70 260 A 110 110 0 0 1 290 260" fill="none" stroke="#111" strokeWidth="1.5" strokeDasharray="3 3" />
                            <path d="M 110 260 A 70 70 0 0 1 250 260" fill="none" stroke="#00c27a" strokeWidth="2" />
                            <circle cx="180" cy="260" r="26" fill="#000" />
                            <text x="180" y="258" textAnchor="middle" fontFamily="JetBrains Mono" fontSize="8" fill="#00c27a">AGENTS</text>
                            <text x="180" y="270" textAnchor="middle" fontFamily="JetBrains Mono" fontSize="7" fill="#888">execute</text>
                            <text x="180" y="100" textAnchor="middle" fontFamily="JetBrains Mono" fontSize="9" fill="#111" fontWeight="600">L1 · COMPLIANCE</text>
                            <text x="180" y="138" textAnchor="middle" fontFamily="JetBrains Mono" fontSize="9" fill="#111" fontWeight="600">L2 · cuFOLIO</text>
                            <text x="180" y="178" textAnchor="middle" fontFamily="JetBrains Mono" fontSize="9" fill="#00c27a" fontWeight="600">L3 · RISK MGR</text>
                            <g stroke="#bbb" strokeWidth="1" strokeDasharray="2 3" fill="none">
                                <line x1="10" y1="60" x2="80" y2="160" />
                                <line x1="350" y1="60" x2="280" y2="160" />
                                <line x1="180" y1="10" x2="180" y2="90" />
                            </g>
                            <g fill="#bbb" fontFamily="JetBrains Mono" fontSize="10">
                                <text x="72" y="170">×</text>
                                <text x="284" y="170">×</text>
                                <text x="175" y="98">×</text>
                            </g>
                        </svg>
                    </div>
                </div>
            </section>

            {/* Skyline flip */}
            <section className="lb-skyline-flip">
                <div className="lb-narrative">
                    <div className="lb-eyebrow lb-reveal">Next stop · the machine room</div>
                    <h2 className="lb-reveal lb-r-delay-1">Light side for you.<br />Dark side for Wall Street.</h2>
                    <p className="lb-reveal lb-r-delay-2">Everything above this line is your view: the returns, the explanations, the plain-English thesis. Everything below is what the machine actually does — the agents, the votes, the guardrails, the audit trail.</p>
                </div>
                <div className="lb-img-wrap">
                    <img className="lb-skyline-img" src="/assets/skyline-nyc.jpg?v=3748x1369-r3" alt="New York City skyline silhouette — boundary between the product view and the machine room" />
                </div>
            </section>

            {/* Open Source Community Edition (dark zone) */}
            <section className="lb-section lb-dark lb-oss" id="section-oss">
                <div className="lb-container">
                    <div className="lb-eyebrow lb-reveal" style={{ color: "var(--lb-accent)" }}>02 · Open Source</div>
                    <h2 className="lb-reveal lb-r-delay-1">Run the engine<br />on your laptop.</h2>
                    <p className="lb-lede lb-reveal lb-r-delay-1">
                        On <b>May 19, 2026</b> we ship the Community Edition. Same Investment Board, same Guardian, same Portfolio Optimizer we run ourselves — Apache 2.0 licensed, self-hosted, no account required.
                    </p>

                    <div className="lb-oss-grid lb-reveal lb-r-delay-2">
                        <div className="lb-oss-card">
                            <div className="lb-oss-num">01</div>
                            <h3>One command to install</h3>
                            <p>A single terminal command, your Alpaca paper key, your LLM of choice — dashboard live in under 5 minutes.</p>
                        </div>
                        <div className="lb-oss-card">
                            <div className="lb-oss-num">02</div>
                            <h3>Paper trading by default</h3>
                            <p>Every install starts in paper mode. Live trading is a single flag guarded by a typed waiver — no accidents.</p>
                        </div>
                        <div className="lb-oss-card">
                            <div className="lb-oss-num">03</div>
                            <h3>Swap the Investment Board</h3>
                            <p>Five personality presets ship in YAML — balanced, conservative, aggressive, contrarian, momentum. Write your own in fifteen lines.</p>
                        </div>
                    </div>

                    <div className="lb-oss-terminal lb-reveal lb-r-delay-3">
                        <div className="lb-bar"><span /><span /><span /></div>
                        <div className="lb-line"><span className="lb-prompt">you    &gt;</span> install, planned for launch day</div>
                        <div className="lb-line"><span className="lb-dim">────────────────────────────────────────────────</span></div>
                        <div className="lb-line"><span className="lb-dim">$</span> curl -sSL aaagents.dev/install | sh</div>
                        <div className="lb-line"><span className="lb-dim">$</span> aaagents init         <span className="lb-dim">· paste Alpaca + Gemini keys</span></div>
                        <div className="lb-line"><span className="lb-dim">$</span> aaagents board use balanced</div>
                        <div className="lb-line"><span className="lb-dim">$</span> aaagents start</div>
                        <div className="lb-line"><span className="lb-dim">────────────────────────────────────────────────</span></div>
                        <div className="lb-line"><span className="lb-ok">✓</span> fastapi up · :8001</div>
                        <div className="lb-line"><span className="lb-ok">✓</span> dashboard up · :8081</div>
                        <div className="lb-line"><span className="lb-ok">✓</span> board loaded · 12 members</div>
                        <div className="lb-line"><span className="lb-ok">✓</span> paper mode · live=false</div>
                        <div className="lb-line"><span className="lb-prompt">board &gt;</span> awaiting market open<span className="lb-cursor" /></div>
                    </div>

                    <div className="lb-oss-footer lb-reveal lb-r-delay-3">
                        <a href="#waitlist" className="lb-cta-primary lb-cta-green" onClick={(e) => { e.preventDefault(); scrollToId("waitlist"); }}>
                            Get the launch email ›
                        </a>
                        <span className="lb-oss-meta">Apache 2.0 · self-hosted · no telemetry · BYO LLM key</span>
                    </div>
                </div>
            </section>

            {/* Community vs Enterprise (dark zone) */}
            <section className="lb-section lb-dark" id="section-editions">
                <div className="lb-container">
                    <div className="lb-eyebrow lb-reveal">03 · Editions</div>
                    <h2 className="lb-reveal lb-r-delay-1">Community or Enterprise.</h2>
                    <p className="lb-lede lb-reveal lb-r-delay-1">
                        Everyone runs the same Investment Board, Guardian, and Optimizer. The difference is where it runs, who is accountable, and what the custody chain looks like.
                    </p>
                    <div className="lb-editions lb-reveal lb-r-delay-2">
                        <div className="lb-ed-col">
                            <div className="lb-ed-badge">Community</div>
                            <div className="lb-ed-price">free · Apache 2.0</div>
                            <ul>
                                <li>Runs on your PC — Docker or native</li>
                                <li>Paper trading by default</li>
                                <li>Live trading behind a typed waiver</li>
                                <li>Ollama local LLM — or your own Claude / GPT / Gemini key</li>
                                <li>cvxpy CPU portfolio optimizer</li>
                                <li>12-member Investment Board · 5 personality presets</li>
                                <li>Community Discord · launching with the repo</li>
                                <li>Self-accountable · no custody chain</li>
                            </ul>
                        </div>
                        <div className="lb-ed-col lb-ed-col-alt">
                            <div className="lb-ed-badge lb-ed-badge-alt">Enterprise · aaagents.de</div>
                            <div className="lb-ed-price">EU/EEA · closed beta</div>
                            <ul>
                                <li>Multi-tenant hosted execution</li>
                                <li>Regulated custodian · client assets segregated</li>
                                <li>Auditable decisions and structured logging (design goals)</li>
                                <li>Managed LLM infrastructure</li>
                                <li>cuOpt GPU optimizer option</li>
                                <li>WORM audit log · exportable</li>
                                <li>Mandate-level approvals + support SLA</li>
                                <li>Legal entity + compliance coverage</li>
                            </ul>
                        </div>
                    </div>
                </div>
            </section>

            {/* S5 — Governance & Auditability (honest claims — only what we actually have today) */}
            <section className="lb-section lb-dark">
                <div className="lb-container">
                    <div className="lb-eyebrow lb-reveal">06 · Governance &amp; Auditability</div>
                    <h2 className="lb-reveal lb-r-delay-1">Every decision,<br />on the record.</h2>
                    <p className="lb-lede lb-reveal lb-r-delay-1" style={{ color: "var(--lb-muted-dark)" }}>We log the full chain: which agent voted, how the Guardian sized, how the optimizer cleared, when the order filled — replayable end-to-end.</p>
                    <div className="lb-stats">
                        <div className="lb-stat lb-reveal lb-r-delay-1"><div className="lb-big">Decision logs</div><div className="lb-lbl">Cloud SQL records every named vote, Guardian check, and order latency — any historical trade can be replayed from first agent signal to fill.</div></div>
                        <div className="lb-stat lb-reveal lb-r-delay-2"><div className="lb-big">Separation</div><div className="lb-lbl">Agents propose in natural language. Deterministic code executes. The boundary between "AI suggests" and "machine acts" is a hard, compile-time wall.</div></div>
                        <div className="lb-stat lb-reveal lb-r-delay-3"><div className="lb-big">Watchdogs</div><div className="lb-lbl">ML-drift and latency monitors run continuously. Breaches trigger automatic halt + alert — the dome stays sealed until a human clears it.</div></div>
                    </div>
                </div>
            </section>

            {/* Pre-footer — Institutional Precision */}
            <section className="lb-section lb-dark" id="section-track-record" style={{ padding: "140px var(--lb-gutter)" }}>
                <div className="lb-section-grid">
                    <div>
                        <div className="lb-eyebrow lb-reveal" style={{ color: "var(--lb-muted-dark)" }}>07 · Ready</div>
                        <h2 className="lb-reveal lb-r-delay-1">Institutional precision.<br />Democratized access.</h2>
                        <p className="lb-lede lb-reveal lb-r-delay-2" style={{ color: "var(--lb-muted-dark)" }}>The future of investing is here — for everyone.</p>
                        <ul className="lb-bullets lb-reveal lb-r-delay-2">
                            <li><b>Decoupled cost</b><span className="lb-muted" style={{ color: "var(--lb-muted-dark)" }}>by separating human labour from complex analysis, we drastically reduce the cost of top-tier asset management.</span></li>
                            <li><b>Higher alpha</b><span className="lb-muted" style={{ color: "var(--lb-muted-dark)" }}>more stable Sharpe ratios — accessible to every investor.</span></li>
                        </ul>
                        <div style={{ marginTop: 32, display: "flex", gap: 16, flexWrap: "wrap" }}>
                            <a href="#waitlist" className="lb-reveal lb-r-delay-3" style={{ display: "inline-block", background: "#00c27a", color: "#000", padding: "18px 36px", borderRadius: 999, fontWeight: 800, textDecoration: "none", fontSize: 16 }}>Request demo access ›</a>
                            <a href="#" onClick={(e) => { e.preventDefault(); navigate("/legal/risk-disclosure"); }} className="lb-reveal lb-r-delay-3" style={{ display: "inline-block", color: "#fff", padding: "18px 32px", borderRadius: 999, fontWeight: 600, textDecoration: "none", fontSize: 15, border: "1px solid rgba(255,255,255,0.18)" }}>Download whitepaper ›</a>
                        </div>
                        <div className="lb-reveal lb-r-delay-3" style={{ marginTop: 20, color: "var(--lb-muted-dark)", fontSize: 13, fontFamily: "var(--lb-mono)" }}>no credit card · no fee to join</div>
                    </div>
                    <div className="lb-viz-chart lb-reveal lb-r-delay-3" style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)" }}>
                        <div className="lb-label" style={{ color: "var(--lb-muted-dark)" }}>Alpha vs S&amp;P 500 · since Feb 2026</div>
                        <div className="lb-value" style={{ color: "#fff" }}>+9.3&nbsp;pp</div>
                        <div className="lb-delta">live paper portfolio &nbsp;·&nbsp; percentage points over benchmark</div>
                        <svg viewBox="0 0 600 220" style={{ marginTop: 32, width: "100%", height: "auto" }} aria-label="AAAgents vs S&P 500 bar comparison">
                            {/* baseline */}
                            <line x1="40" y1="180" x2="560" y2="180" stroke="rgba(255,255,255,0.2)" strokeWidth="1" />
                            <text x="40" y="200" fontFamily="JetBrains Mono" fontSize="11" fill="rgba(255,255,255,0.55)">S&amp;P 500</text>
                            <text x="560" y="200" textAnchor="end" fontFamily="JetBrains Mono" fontSize="11" fill="rgba(255,255,255,0.55)">AAAgents</text>
                            {/* S&P bar (baseline) */}
                            <rect x="60" y="170" width="220" height="10" fill="rgba(255,255,255,0.35)" />
                            <text x="60" y="160" fontFamily="JetBrains Mono" fontSize="11" fill="rgba(255,255,255,0.8)">0.0 pp (benchmark)</text>
                            {/* AAAgents bar (+9.3 pp above) */}
                            <rect x="320" y="50" width="220" height="130" fill="#00c27a" />
                            <text x="320" y="40" fontFamily="JetBrains Mono" fontSize="11" fill="#00c27a">+9.3 pp</text>
                            {/* tick */}
                            <line x1="320" y1="50" x2="540" y2="50" stroke="#00c27a" strokeWidth="1" strokeDasharray="2 2" opacity="0.4" />
                        </svg>
                        <div style={{ marginTop: 20, fontFamily: "var(--lb-mono)", fontSize: 11, color: "var(--lb-muted-dark)" }}>
                            Paper-trading period · Feb 2026 → today · past performance does not guarantee future results.
                        </div>
                    </div>
                </div>
            </section>

            {/* Footer */}
            <footer className="lb-footer">
                <div className="lb-footer-grid">
                    <div>
                        <div style={{ fontWeight: 800, fontSize: 18, color: "#fff", letterSpacing: "0.5px", fontFamily: "var(--lb-mono)" }}>
                            aaagents<span style={{ color: "#00c27a" }}>_</span>
                        </div>
                        <p style={{ maxWidth: "42ch", marginTop: 16, fontSize: 13, lineHeight: 1.6 }}>
                            The governance layer for autonomous trading. Analyst agents research, the Investment Board votes, the Iron Dome protects — every decision auditable, structured for future regulatory work.
                        </p>
                    </div>
                    <div>
                        <h4>Platform</h4>
                        <a href="#" onClick={(e) => { e.preventDefault(); scrollToId("section-research"); }}>Agents</a>
                        <a href="#" onClick={(e) => { e.preventDefault(); scrollToId("section-compliance"); }}>Compliance</a>
                        <a href="#" onClick={(e) => { e.preventDefault(); scrollToId("section-track-record"); }}>Performance</a>
                        <a href="#waitlist">Join waitlist</a>
                    </div>
                    <div>
                        <h4>Open Source</h4>
                        <a href="#" onClick={(e) => { e.preventDefault(); scrollToId("section-oss"); }}>Community Edition</a>
                        <a href="#" onClick={(e) => { e.preventDefault(); scrollToId("section-editions"); }}>Community vs Enterprise</a>
                        <a href="#" onClick={(e) => { e.preventDefault(); scrollToId("waitlist"); }}>Launch email · May 19</a>
                        <span style={{ color: "rgba(255,255,255,0.35)", fontSize: 13 }}>GitHub · launching soon</span>
                    </div>
                    <div>
                        <h4>Legal</h4>
                        <a href="#" onClick={(e) => { e.preventDefault(); navigate("/legal/imprint"); }}>Imprint</a>
                        <a href="#" onClick={(e) => { e.preventDefault(); navigate("/legal/privacy"); }}>Privacy</a>
                        <a href="#" onClick={(e) => { e.preventDefault(); navigate("/legal/risk-disclosure"); }}>Risk disclosure</a>
                        <a href="#">Security</a>
                    </div>
                </div>
                <p className="lb-disclaimer">
                    Investing carries risk. The value of your investment may fall or rise and losses of the capital invested may occur. Past performance is no guarantee of future results. AAAgents is a demonstration platform; figures shown may be illustrative.
                </p>
                <div className="lb-bottom">
                    <span>© AAAgents · Built in Europe</span>
                    <span>aaagents.de</span>
                </div>
            </footer>
        </div>
    );
}
