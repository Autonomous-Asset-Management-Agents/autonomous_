import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { useNavigate } from "react-router-dom";

/* ── Scroll-fade wrapper ── */
const FI = ({
  children,
  delay = 0,
  className = "",
  style = {},
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
  style?: React.CSSProperties;
}) => (
  <motion.div
    initial={{ opacity: 0, y: 20 }}
    whileInView={{ opacity: 1, y: 0 }}
    viewport={{ once: true, margin: "-60px" }}
    transition={{ duration: 0.7, ease: [0.4, 0, 0.2, 1], delay }}
    className={className}
    style={style}
  >
    {children}
  </motion.div>
);

const Div = () => (
  <div style={{ height: 1, background: "rgba(255,255,255,0.05)", maxWidth: 980, margin: "0 auto" }} />
);

const Section = ({ children, sm = false }: { children: React.ReactNode; sm?: boolean }) => (
  <section style={{ padding: sm ? "48px 0" : "80px 0" }}>{children}</section>
);

const WrapW = ({ children }: { children: React.ReactNode }) => (
  <div style={{ maxWidth: 980, margin: "0 auto", padding: "0 24px" }}>{children}</div>
);

const Wrap = ({ children }: { children: React.ReactNode }) => (
  <div style={{ maxWidth: 680, margin: "0 auto", padding: "0 24px" }}>{children}</div>
);

const Eye = ({ children }: { children: React.ReactNode }) => (
  <div style={{
    fontSize: 12, fontWeight: 600, marginBottom: 6, textAlign: "center",
    textTransform: "uppercase", letterSpacing: "0.06em",
    color: "rgba(255,255,255,0.3)",
  }}>
    {children}
  </div>
);

const H2 = ({ children }: { children: React.ReactNode }) => (
  <h2 style={{
    fontSize: "clamp(24px,3.5vw,36px)", fontWeight: 700, lineHeight: 1.15,
    letterSpacing: "-0.025em", textAlign: "center", marginBottom: 10,
    color: "rgba(255,255,255,0.85)",
  }}>
    {children}
  </h2>
);

const Sub = ({ children }: { children: React.ReactNode }) => (
  <p style={{
    fontSize: 14, color: "rgba(255,255,255,0.4)", textAlign: "center",
    maxWidth: 440, margin: "0 auto 36px", lineHeight: 1.6,
  }}>
    {children}
  </p>
);

/* ── Accordion ── */
const accordionItems = [
  {
    n: "01",
    t: "Reinforcement Learning Agent",
    body: "PPO agent trained on multi-year S&P 500 data. Builds a composite observation vector per symbol — price action, position state, volatility regime, and model signals. Adapts thresholds dynamically based on market conditions.",
    pills: ["PPO", "SB3", "VIX Regime", "Adaptive Thresholds"],
  },
  {
    n: "02",
    t: "LSTM + Attention",
    body: "Bidirectional LSTM with attention over multi-week sequences, trained on a broad set of technical features per bar (momentum, volatility, volume, trend indicators). Multiple independent models averaged for stability.",
    pills: ["Bi-LSTM", "Attention", "Technical Features", "Ensemble"],
  },
  {
    n: "03",
    t: "LLM Sentiment",
    body: "Gemini Flash analyzes news from Polygon for held symbols. Runs asynchronously — a slow response or timeout does not block the trading cycle. Output feeds into the agent consensus as one weighted vote.",
    pills: ["Gemini Flash", "Polygon News", "Async", "Sentiment"],
  },
  {
    n: "04",
    t: "Market Regime Detection",
    body: "Classifies the current market as calm, normal, elevated, or crisis based on volatility indices and price structure. Shifts all agents toward conservative signals during elevated market stress.",
    pills: ["VIX", "4 Regimes", "Threshold Shift", "Risk-Aware"],
  },
  {
    n: "05",
    t: "Stock Specialist — Alpha Research",
    body: "Per-symbol research pipeline that synthesizes fundamentals, analyst ratings, and recent news into a structured report. Results are cached and rate-limited per symbol to stay within free data API limits.",
    pills: ["Research Pipeline", "Cached", "Fundamentals", "News Synthesis"],
  },
];

const AccordionItem = ({ item, open, onToggle }: {
  item: typeof accordionItems[0];
  open: boolean;
  onToggle: () => void;
}) => (
  <div onClick={onToggle} style={{ borderBottom: "1px solid rgba(255,255,255,0.05)", cursor: "pointer" }}>
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "16px 0",
      color: open ? "rgba(255,255,255,0.85)" : "rgba(255,255,255,0.55)",
      transition: "color 0.2s",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "rgba(255,255,255,0.16)", fontWeight: 500 }}>{item.n}</span>
        <span style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.01em" }}>{item.t}</span>
      </div>
      <span style={{
        fontSize: 16, color: open ? "rgba(255,255,255,0.55)" : "rgba(255,255,255,0.2)",
        transform: open ? "rotate(45deg)" : "rotate(0deg)",
        transition: "transform 0.3s, color 0.3s", fontWeight: 300, lineHeight: 1, flexShrink: 0,
      }}>+</span>
    </div>
    <motion.div
      initial={false}
      animate={{ height: open ? "auto" : 0, opacity: open ? 1 : 0 }}
      transition={{ duration: 0.35, ease: [0.4, 0, 0.2, 1] }}
      style={{ overflow: "hidden" }}
    >
      <div style={{ padding: "0 0 18px 34px" }}>
        <p style={{ fontSize: 13, color: "rgba(255,255,255,0.45)", maxWidth: 480, marginBottom: 10, lineHeight: 1.6 }}>{item.body}</p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {item.pills.map((p) => (
            <span key={p} style={{
              fontSize: 11, fontWeight: 500, color: "rgba(255,255,255,0.4)",
              padding: "3px 9px", background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.06)", borderRadius: 980,
            }}>{p}</span>
          ))}
        </div>
      </div>
    </motion.div>
  </div>
);

/* ── City clock hook ── */
function useCityClocks() {
  const fmt = (tz: string) =>
    new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: tz });
  const [times, setTimes] = useState({
    frankfurt: fmt("Europe/Berlin"),
    london: fmt("Europe/London"),
    newYork: fmt("America/New_York"),
    tokyo: fmt("Asia/Tokyo"),
  });
  useEffect(() => {
    const iv = setInterval(() => setTimes({
      frankfurt: fmt("Europe/Berlin"),
      london: fmt("Europe/London"),
      newYork: fmt("America/New_York"),
      tokyo: fmt("Asia/Tokyo"),
    }), 1000);
    return () => clearInterval(iv);
  }, []);
  return times;
}

/* ═══════════════════════════════════════════════════════════════════
   LandingView
═══════════════════════════════════════════════════════════════════ */
export const LandingView = () => {
  const navigate = useNavigate();
  const [openAccordion, setOpenAccordion] = useState<number | null>(null);
  const [emailValue, setEmailValue] = useState("");
  const clocks = useCityClocks();
  const heroRef = useRef<HTMLDivElement>(null);

  const handleJoin = (e: React.FormEvent) => {
    e.preventDefault();
    navigate("/login");
  };

  return (
    <div style={{ position: "relative", zIndex: 2 }}>

      {/* ── HERO ── */}
      <section ref={heroRef} style={{
        minHeight: "100vh", display: "flex",
        alignItems: "center", justifyContent: "center",
        padding: "0 48px", overflow: "visible",
      }}>
        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.8, delay: 0.3, ease: [0.4, 0, 0.2, 1] }}
          style={{
            fontSize: "clamp(40px,10vw,110px)",
            fontWeight: 700, letterSpacing: "-0.04em", lineHeight: 1.1,
            textAlign: "center", color: "rgba(255,255,255,0.85)",
            userSelect: "none",
          }}
        >
          AAAgents
        </motion.div>
      </section>

      <Div />

      {/* ── HOW IT WORKS ── */}
      <Section>
        <WrapW>
          <FI>
            <Eye>System</Eye>
            <H2>Four subsystems.</H2>
            <Sub>Each trade passes through data ingestion, model inference, agent consensus, and compliance enforcement — in that order.</Sub>
          </FI>
          <FI delay={0.1}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10 }}>
              {[
                {
                  label: "Data Pipeline",
                  title: "Market data ingestion",
                  desc: "Alpaca (OHLCV), Polygon (news), VIX and SPY indices. Multiple feeds run in parallel and trigger the trading loop on each cycle.",
                },
                {
                  label: "Round Table",
                  title: "9-agent consensus",
                  desc: "Nine independent agents evaluate each symbol in parallel. A consensus engine aggregates their weighted votes into a single signal. P99 per cycle: ~28ms at 50 symbols.",
                },
                {
                  label: "Execution",
                  title: "Order + portfolio management",
                  desc: "Position sizes are determined by a per-symbol conviction score. Every order passes through a compliance layer before reaching the Alpaca broker API.",
                },
                {
                  label: "Compliance",
                  title: "Iron Dome — deterministic checks",
                  desc: "A series of hard blocks run before every order: symbol restrictions, MiFID II field validation, wash-trade detection, order size limits, and daily trade caps. No AI agent can override this layer.",
                },
              ].map((fc) => (
                <div
                  key={fc.title}
                  className="surface-card"
                  style={{ padding: 28, transition: "border-color 0.25s" }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = "rgba(255,255,255,0.12)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = ""; }}
                >
                  <div style={{
                    fontSize: 11, fontWeight: 600, marginBottom: 8,
                    textTransform: "uppercase", letterSpacing: "0.05em",
                    color: "rgba(255,255,255,0.25)",
                  }}>{fc.label}</div>
                  <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.01em", marginBottom: 6, color: "rgba(255,255,255,0.85)" }}>{fc.title}</div>
                  <div style={{ fontSize: 13, color: "rgba(255,255,255,0.45)", lineHeight: 1.55 }}>{fc.desc}</div>
                </div>
              ))}
            </div>
          </FI>
        </WrapW>
      </Section>

      <Div />

      {/* ── AGENTS ── */}
      <Section>
        <WrapW>
          <FI>
            <Eye>Round Table</Eye>
            <H2>Nine agents, one decision.</H2>
            <Sub>Each agent has an independent signal source and a fixed vote weight. A ComplianceGatekeeper runs after consensus — its veto is final.</Sub>
          </FI>
          <FI delay={0.1}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>
              {[
                { name: "DrawdownGuard",      role: "Portfolio drawdown & kill-switch" },
                { name: "SpecialistAlpha",    role: "Stock research synthesis" },
                { name: "RegimeDetection",    role: "Market regime classification" },
                { name: "Momentum",           role: "Price momentum signal" },
                { name: "VIXAwareRisk",       role: "Volatility-adjusted thresholds" },
                { name: "LSTMSignal",         role: "LSTM direction prediction" },
                { name: "RLConfidence",       role: "RL agent confidence signal" },
                { name: "NewsSentiment",      role: "LLM news analysis" },
                { name: "PatternRecognition", role: "Candlestick pattern detection" },
              ].map((ag) => (
                <div
                  key={ag.name}
                  style={{
                    background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.05)",
                    borderRadius: 10, padding: "12px 14px", transition: "border-color 0.2s",
                  }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = "rgba(255,255,255,0.1)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = "rgba(255,255,255,0.05)"; }}
                >
                  <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, fontWeight: 600, color: "rgba(255,255,255,0.7)", marginBottom: 4 }}>{ag.name}</div>
                  <div style={{ fontSize: 11, color: "rgba(255,255,255,0.35)", lineHeight: 1.4 }}>{ag.role}</div>
                </div>
              ))}
            </div>
          </FI>
        </WrapW>
      </Section>

      <Div />

      {/* ── MODELS (Accordion) ── */}
      <Section>
        <Wrap>
          <FI>
            <Eye>Models</Eye>
            <H2>The AI layer.</H2>
            <Sub>Three learning paradigms feed into the Round Table. Each is independently tested and versioned.</Sub>
          </FI>
          <FI delay={0.1}>
            <div style={{ borderTop: "1px solid rgba(255,255,255,0.05)", marginTop: 8 }}>
              {accordionItems.map((item, i) => (
                <AccordionItem
                  key={item.n}
                  item={item}
                  open={openAccordion === i}
                  onToggle={() => setOpenAccordion(openAccordion === i ? null : i)}
                />
              ))}
            </div>
          </FI>
        </Wrap>
      </Section>

      <Div />

      {/* ── ARCHITECTURE LAYERS ── */}
      <Section>
        <Wrap>
          <FI>
            <Eye>Architecture</Eye>
            <H2>Five layers deep.</H2>
            <Sub>From raw market data to executed order — every layer is independently deployable and observable via OpenTelemetry.</Sub>
          </FI>
          <FI delay={0.1}>
            <div style={{ maxWidth: 500, margin: "8px auto 0" }}>
              {[
                { color: "#0a84ff", name: "Data",    items: "Alpaca · Polygon · VIX/SPY" },
                { color: "#30d158", name: "Models",  items: "LSTM+Attention · RL Agent · LLM Sentiment" },
                { color: "#ff9f0a", name: "Engine",  items: "Trading Loop · Orchestration · Round Table" },
                { color: "#bf5af2", name: "API",     items: "FastAPI · WebSocket · REST Endpoints" },
                { color: "#ff375f", name: "UI",      items: "React · PyQt6 · Cloud Run" },
              ].map((lr) => (
                <div
                  key={lr.name}
                  style={{
                    display: "flex", alignItems: "center", gap: 14,
                    padding: "10px 12px", borderRadius: 8, marginBottom: 1,
                    transition: "background 0.2s",
                  }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "rgba(255,255,255,0.03)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = ""; }}
                >
                  <div style={{ width: 6, height: 6, borderRadius: "50%", background: lr.color, flexShrink: 0 }} />
                  <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, fontWeight: 600, color: lr.color, minWidth: 56 }}>{lr.name}</span>
                  <span style={{ fontSize: 13, color: "rgba(255,255,255,0.45)" }}>{lr.items}</span>
                </div>
              ))}
            </div>
          </FI>
        </Wrap>
      </Section>

      <Div />

      {/* ── METRICS ── */}
      <Section sm>
        <WrapW>
          <FI>
            <div style={{ display: "flex", gap: 10, justifyContent: "center", flexWrap: "wrap" }}>
              {[
                { v: "9",     l: "Voting agents" },
                { v: "28ms",  l: "P99 per cycle" },
                { v: "~50×",  l: "vs. sequential" },
                { v: "5",     l: "Compliance checks" },
              ].map((sp) => (
                <div
                  key={sp.l}
                  className="surface-card"
                  style={{ padding: "14px 24px", textAlign: "center", flex: 1, maxWidth: 160, transition: "border-color 0.25s" }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = "rgba(255,255,255,0.12)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = ""; }}
                >
                  <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 22, fontWeight: 600, letterSpacing: "-0.02em", color: "rgba(255,255,255,0.85)" }}>{sp.v}</div>
                  <div style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", marginTop: 3, fontWeight: 500 }}>{sp.l}</div>
                </div>
              ))}
            </div>
          </FI>
        </WrapW>
      </Section>

      <Div />

      {/* ── COMPLIANCE ── */}
      <Section>
        <WrapW>
          <FI>
            <Eye>Regulatory</Eye>
            <H2>Compliance as infrastructure.</H2>
            <Sub>Not a checklist added at the end — compliance rules are enforced in the execution path and logged to an immutable audit trail.</Sub>
          </FI>
          <FI delay={0.1}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, marginTop: 8 }}>
              {[
                {
                  t: "MiFID II",
                  s: "EU Markets Directive",
                  items: [
                    "Required order fields validated pre-submission",
                    "Wash-trade detection on every order",
                    "Strategy ID on every order (audit trail)",
                    "Best-execution routing via regulated broker",
                  ],
                },
                {
                  t: "Iron Dome",
                  s: "Deterministic rule engine",
                  items: [
                    "Kill-switch with high unit test coverage",
                    "Hard blocks that no AI agent can override",
                    "Trade limits enforced per account",
                    "Restricted symbol list enforced pre-order",
                  ],
                },
                {
                  t: "DORA",
                  s: "Operational resilience",
                  items: [
                    "Immutable audit logs on GCP Cloud SQL",
                    "End-to-end tracing via OpenTelemetry",
                    "Persistent fallback on state store failure",
                    "Graceful degradation on component failure",
                  ],
                },
              ].map((rc) => (
                <div
                  key={rc.t}
                  className="surface-card"
                  style={{ padding: 24, transition: "border-color 0.25s" }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = "rgba(255,255,255,0.12)"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.borderColor = ""; }}
                >
                  <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 2, letterSpacing: "-0.01em", color: "rgba(255,255,255,0.85)" }}>{rc.t}</div>
                  <div style={{ fontSize: 11, color: "rgba(255,255,255,0.2)", marginBottom: 14, fontWeight: 500 }}>{rc.s}</div>
                  {rc.items.map((item) => (
                    <div key={item} style={{ fontSize: 12, color: "rgba(255,255,255,0.45)", padding: "3px 0", display: "flex", alignItems: "flex-start", gap: 7, lineHeight: 1.45 }}>
                      <span style={{ color: "#30d158", fontSize: 10, marginTop: 2, flexShrink: 0 }}>✓</span> {item}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </FI>
        </WrapW>
      </Section>

      <Div />

      {/* ── ROADMAP STATUS ── */}
      <Section>
        <Wrap>
          <FI>
            <Eye>Roadmap</Eye>
            <H2>Where we are.</H2>
            <Sub>Infrastructure and live trading are operational. The current phase focuses on data quality, model monitoring, and platform hardening.</Sub>
          </FI>
          <FI delay={0.1}>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {[
                { phase: "Phase 1", label: "Cloud infrastructure, operative dashboard", done: true },
                { phase: "Phase 2", label: "Live capital, multi-tenant, kill-switch, MiFID II compliance", done: true },
                { phase: "Phase 3", label: "Performance hardening, model monitoring, data pipeline upgrade", done: false },
                { phase: "Phase 4", label: "Platform scaling, KYC integration, security hardening", done: false },
                { phase: "Phase 5", label: "Container orchestration migration, self-optimisation", done: false },
              ].map((row) => (
                <div
                  key={row.phase}
                  style={{
                    display: "flex", alignItems: "center", gap: 14,
                    padding: "11px 14px", borderRadius: 8,
                    background: row.done ? "rgba(48,209,88,0.04)" : "rgba(255,255,255,0.02)",
                    border: `1px solid ${row.done ? "rgba(48,209,88,0.1)" : "rgba(255,255,255,0.05)"}`,
                  }}
                >
                  <div style={{ width: 6, height: 6, borderRadius: "50%", flexShrink: 0, background: row.done ? "#30d158" : "rgba(255,255,255,0.15)" }} />
                  <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, fontWeight: 600, color: row.done ? "rgba(48,209,88,0.8)" : "rgba(255,255,255,0.25)", minWidth: 68 }}>{row.phase}</span>
                  <span style={{ fontSize: 13, color: row.done ? "rgba(255,255,255,0.55)" : "rgba(255,255,255,0.3)", lineHeight: 1.4 }}>{row.label}</span>
                </div>
              ))}
            </div>
          </FI>
        </Wrap>
      </Section>

      <Div />

      {/* ── ACCESS / CTA ── */}
      <Section>
        <Wrap>
          <FI>
            <Eye>Access</Eye>
            <H2>Private beta.</H2>
            <p style={{
              fontSize: 14, color: "rgba(255,255,255,0.4)", textAlign: "center",
              maxWidth: 400, margin: "0 auto 40px", lineHeight: 1.6,
            }}>
              Currently onboarding qualified investors and trading firms in Germany, Austria, and Switzerland.
            </p>
            <form onSubmit={handleJoin} style={{
              display: "flex", maxWidth: 340, margin: "0 auto",
              borderRadius: 10, overflow: "hidden",
              border: "1px solid rgba(255,255,255,0.08)",
              background: "rgba(28,28,30,0.72)", backdropFilter: "saturate(180%) blur(20px)",
            }}>
              <input
                type="email"
                value={emailValue}
                onChange={(e) => setEmailValue(e.target.value)}
                placeholder="Email address"
                style={{
                  flex: 1, padding: "12px 14px", background: "transparent",
                  border: "none", color: "rgba(255,255,255,0.85)", fontFamily: "inherit",
                  fontSize: 14, outline: "none",
                }}
              />
              <button
                type="submit"
                style={{
                  padding: "12px 18px", background: "#d4a853", color: "#000",
                  fontWeight: 600, fontSize: 13, border: "none", cursor: "pointer",
                  transition: "opacity 0.2s",
                }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "0.88"; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "1"; }}
              >
                Join
              </button>
            </form>
          </FI>
        </Wrap>
      </Section>

      {/* ── FOOTER ── */}
      <footer style={{ padding: "20px 0", borderTop: "1px solid rgba(255,255,255,0.05)" }}>
        <WrapW>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
            <div style={{ fontSize: 13, color: "rgba(255,255,255,0.25)", fontWeight: 500 }}>AAAgents</div>
            <div style={{ display: "flex", gap: 20 }}>
              {[
                { city: "Frankfurt", time: clocks.frankfurt },
                { city: "London",    time: clocks.london },
                { city: "New York",  time: clocks.newYork },
                { city: "Tokyo",     time: clocks.tokyo },
              ].map((c) => (
                <div key={c.city}>
                  <div style={{ fontSize: 10, color: "rgba(255,255,255,0.16)", fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.06em" }}>{c.city}</div>
                  <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "rgba(255,255,255,0.4)", fontVariantNumeric: "tabular-nums" }}>{c.time}</div>
                </div>
              ))}
            </div>
            <div style={{ fontSize: 11, color: "rgba(255,255,255,0.16)" }}>© 2024–2026</div>
          </div>
        </WrapW>
      </footer>

    </div>
  );
};
