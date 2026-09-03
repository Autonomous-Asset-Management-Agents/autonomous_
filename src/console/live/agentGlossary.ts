/**
 * Per-agent explainer for the Round-Table. A deterministic, factual profile —
 * the agent's ROLE, its TASKS, and its DECISION CRITERIA — revealed behind the
 * agent's name (a link). Static, not generated, so it is honest and always
 * available, unlike the LLM glass-box (which degrades to generic chat on the
 * OSS/Ollama desktop). Unknown agents fall back to a de-camelCased label.
 */
export interface AgentInfo {
  label: string;
  /** What the agent IS / its purpose at the table. */
  role: string;
  /** What it actually computes / does. */
  tasks: string;
  /** How its read maps to a vote. */
  criteria: string;
}

const GLOSSARY: Record<string, AgentInfo> = {
  DrawdownGuardAgent: {
    label: "Drawdown Guard",
    role: "Risk overlay — the pre-trade veto. Never part of the directional mean.",
    tasks: "Computes the current drawdown from the recent high/low.",
    criteria: "A sharp sell-off can veto a new buy outright (vetoed decision = no trade).",
  },
  SpecialistAlphaAgent: {
    label: "Specialist · Sentiment",
    role: "The fundamental/sentiment voice — an LLM analyst per symbol.",
    tasks: "Scores the symbol's news + sentiment (0–100) and derives its vote from it.",
    criteria: "High sentiment → buy, mid → hold, low → sell; strong reads can escalate.",
  },
  RegimeDetectionAgent: {
    label: "Regime Detection",
    role: "Regime overlay — sets the macro context; excluded from the directional mean.",
    tasks: "Classifies the market regime (up / down / neutral).",
    criteria: "Risk-off regimes damp the directional voters' weights; it casts no directional vote of its own.",
  },
  MomentumAgent: {
    label: "Momentum",
    role: "The trend-follower.",
    tasks: "Measures the short-term price move in %.",
    criteria: "A strong upward push → bullish; flat or negative → neutral / bearish.",
  },
  VIXAwareRiskAgent: {
    label: "VIX Risk",
    role: "Volatility input to position sizing — excluded from the directional mean while implied-vol forecasting is on.",
    tasks: "Reads expected volatility (VIX / implied vol) for the name.",
    criteria: "Higher expected volatility → smaller position size; it votes in the directional consensus only when the IV forecast is off.",
  },
  LSTMSignalAgent: {
    label: "LSTM Model",
    role: "The deep-learning forecaster.",
    tasks: "Runs an LSTM time-series model to predict the next direction.",
    criteria: "Votes the model's predicted action (buy / hold / sell).",
  },
  RLConfidenceAgent: {
    label: "RL Agent",
    role: "The learned policy — a reinforcement-learning trader.",
    tasks: "Outputs an action + a confidence from a policy trained on past trading.",
    criteria: "Acts only when confidence is high enough; otherwise holds.",
  },
  NewsSentimentAgent: {
    label: "News Sentiment",
    role: "The newsflow watcher.",
    tasks: "Scores the latest headlines for the symbol via LLM.",
    criteria: "Positive news → bullish; negative → bearish.",
  },
  UpsideSkewAgent: {
    label: "Upside Skew",
    role: "Weighs upside potential against downside risk (return skew). Ships dark — flag off by default.",
    tasks: "Compares the size of recent upside moves against recent downside moves.",
    criteria: "Favourable skew raises the score, unfavourable skew lowers it.",
  },
  FundamentalsAgent: {
    label: "Fundamentals",
    role: "Reads the business: growth and profitability from point-in-time filings. Ships at weight 0 (dormant).",
    tasks: "Reads revenue growth and net margin from the point-in-time fundamentals cache.",
    criteria: "A growing, profitable business scores above neutral, a shrinking one below; without data it abstains.",
  },
  ValuationAgent: {
    label: "Valuation",
    role: "Reads the price: trailing PEG and leverage from the same filings. Ships at weight 0 (dormant).",
    tasks: "Computes the trailing PEG (P/E over earnings growth) and the debt-to-equity ratio.",
    criteria: "Growth that is not priced in scores above neutral, stretched multiples below; it abstains when the PEG cannot be formed.",
  },
};

function humanize(name: string): string {
  return name.replace(/Agent$/, "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").trim() || name;
}

export function agentInfo(name: string): AgentInfo {
  return (
    GLOSSARY[name] ?? {
      label: humanize(name),
      role: "One of the Round-Table agents.",
      tasks: "Contributes a vote to the consensus.",
      criteria: "See its raw read for this decision.",
    }
  );
}

export function agentLabel(name: string): string {
  return agentInfo(name).label;
}
