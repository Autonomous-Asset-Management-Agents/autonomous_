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
    role: "Risk sentinel — guards against buying into a sharp sell-off.",
    tasks: "Computes the current drawdown from the recent high/low.",
    criteria: "Shallow drawdown → healthy, leans buy; deep drawdown → caution / risk-off.",
  },
  SpecialistAlphaAgent: {
    label: "Specialist · Sentiment",
    role: "The fundamental/sentiment voice — an LLM analyst per symbol.",
    tasks: "Scores the symbol's news + sentiment (0–100) and forms a recommendation.",
    criteria: "High sentiment → buy, mid → hold, low → sell; strong reads can escalate.",
  },
  RegimeDetectionAgent: {
    label: "Regime Detection",
    role: "Sets the macro context the other agents trade within.",
    tasks: "Classifies the market regime (up / down / neutral) from open vs close.",
    criteria: "Up regime → allow aggressive entries; down / neutral → trade cautiously.",
  },
  MomentumAgent: {
    label: "Momentum",
    role: "The trend-follower.",
    tasks: "Measures the short-term price move in %.",
    criteria: "A strong upward push → bullish; flat or negative → neutral / bearish.",
  },
  VIXAwareRiskAgent: {
    label: "VIX Risk",
    role: "Volatility-aware risk manager.",
    tasks: "Reads market volatility (VIX) and its ratio to recent norms.",
    criteria: "Calm / low-vol market → allow more risk; high volatility → step back.",
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
  PatternRecognitionAgent: {
    label: "Chart Pattern",
    role: "The chartist — reads technical patterns.",
    tasks: "Detects candlestick patterns (e.g. a doji) on the recent candles.",
    criteria: "Bullish patterns → buy signal; an indecision pattern (doji) → no clear edge.",
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
