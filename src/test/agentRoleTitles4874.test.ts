// #4874: one display name per Round-Table agent. The role titles live in ONE place —
// `role_title` in ai_trading_bot/core/trading_settings.py (served to the console) — and every
// list a person reads (console glossary, landing and dashboard lists, the agent catalog, the
// variant-B expert chips) must use exactly those titles. Engine ids (class names) stay the keys.
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { agentInfo } from "@/console/live/agentGlossary";

const ROOT = process.cwd();
const read = (p: string): string => readFileSync(resolve(ROOT, p), "utf8");

// Owner decision 28.10.2026 (#4874): English titles, "Analyst" for every rating agent.
const EXPECTED: Record<string, string> = {
  FundamentalsAgent: "Fundamental Analyst",
  ValuationAgent: "Valuation Analyst",
  QualityAgent: "Quality Analyst",
  SpecialistAlphaAgent: "Equity Research Analyst",
  NewsSentimentAgent: "News Sentiment Analyst",
  MomentumAgent: "Momentum Analyst",
  TrendAgent: "Trend Analyst",
  VolumeConfirmationAgent: "Volume Analyst",
  VIXAwareRiskAgent: "Volatility Analyst",
  RegimeDetectionAgent: "Market Regime Analyst",
  UpsideSkewAgent: "Options Skew Analyst",
  LSTMSignalAgent: "Forecast Model Analyst",
  RLConfidenceAgent: "RL Signal Analyst",
  DrawdownGuardAgent: "Drawdown Analyst",
};
const TITLES = Object.values(EXPECTED);

/** name → role_title pairs from the _AGENTS tuple in trading_settings.py (the single source). */
const sourceTitles = (): Record<string, string> => {
  const text = read("ai_trading_bot/core/trading_settings.py");
  const out: Record<string, string> = {};
  const re = /"name":\s*"(\w+)",\s*"role_title":\s*"([^"]+)"/g;
  for (const m of text.matchAll(re)) out[m[1]] = m[2];
  return out;
};

describe("one role title per agent (#4874)", () => {
  it("trading_settings.py carries the agreed titles for all 14 voting agents", () => {
    const src = sourceTitles();
    for (const [id, title] of Object.entries(EXPECTED)) expect(src[id], id).toBe(title);
  });

  it("the console glossary labels each agent with its role title", () => {
    for (const [id, title] of Object.entries(EXPECTED)) expect(agentInfo(id).label, id).toBe(title);
  });

  it("the agent catalog lists the same titles", () => {
    const catalog = read("docs/2_agentic_operations/AGENT_CATALOG.md");
    for (const [id, title] of Object.entries(EXPECTED)) {
      const row = catalog.split("\n").find((l) => l.includes(`[${id}](`));
      expect(row, id).toBeDefined();
      expect(row!.split("|").map((c) => c.trim())).toContain(title);
    }
  });

  it("the landing and dashboard lists name all 14 agents by title and no removed agent", () => {
    for (const p of ["src/components/views/LandingView.tsx", "src/components/views/DashboardView.tsx"]) {
      const text = read(p);
      expect(text, p).not.toContain("PatternRecognition");
      for (const title of TITLES) expect(text, `${p}: ${title}`).toContain(`"${title}"`);
    }
  });

  it("the weights demo on the landing uses role titles", () => {
    const text = read("src/components/views/LandingViewE.tsx");
    const block = text.slice(text.indexOf("const EXAMPLE_WEIGHTS"), text.indexOf("];", text.indexOf("const EXAMPLE_WEIGHTS")));
    const names = [...block.matchAll(/\["([^"]+)",\s*[\d.]+\]/g)].map((m) => m[1]);
    expect(names.length).toBeGreaterThan(0);
    for (const n of names) expect(TITLES, n).toContain(n);
  });

  it("the variant-B expert chips, once present, use the role titles", () => {
    for (const lang of ["en", "de"]) {
      const p = `src/locales/${lang}/translation.json`;
      if (!existsSync(resolve(ROOT, p))) continue;
      const experts = JSON.parse(read(p))?.landing?.quant?.graphic?.experts;
      if (!Array.isArray(experts)) continue; // #4872 not merged yet
      expect([...experts].sort(), p).toEqual([...TITLES].sort());
    }
  });

  it("each agent documentation page has the matching Rollenbezeichnung on line 5", () => {
    const slugMap: Record<string, string> = {
      FundamentalsAgent: "fundamentals_agent.md",
      ValuationAgent: "valuation_agent.md",
      QualityAgent: "quality_agent.md",
      SpecialistAlphaAgent: "specialist_alpha_agent.md",
      NewsSentimentAgent: "news_sentiment_agent.md",
      MomentumAgent: "momentum_agent.md",
      TrendAgent: "trend_agent.md",
      VolumeConfirmationAgent: "volume_confirmation_agent.md",
      VIXAwareRiskAgent: "vix_aware_risk_agent.md",
      RegimeDetectionAgent: "regime_detection_agent.md",
      UpsideSkewAgent: "upside_skew_agent.md",
      LSTMSignalAgent: "lstm_signal_agent.md",
      RLConfidenceAgent: "rl_confidence_agent.md",
      DrawdownGuardAgent: "drawdown_guard_agent.md",
    };
    for (const [id, title] of Object.entries(EXPECTED)) {
      const p = `docs/2_agentic_operations/agents/${slugMap[id]}`;
      const text = read(p);
      const line5 = text.split("\n")[4] ?? "";
      expect(line5, p).toContain(`**Rollenbezeichnung:** ${title}`);
    }
  });
});
