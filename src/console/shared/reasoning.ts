// #2133: agent vote reasoning is stored "AgentName: …" (kept verbatim in the audit chain), but the
// Decision-page vote row already shows the agent name as its label — so rendering the raw string read
// "DrawdownGuard: DrawdownGuard: VETO=…". Strip a single leading "<AgentName…>: " prefix for DISPLAY
// only. Lives in its own module so the component file exports only components (react-refresh clean).

/** Drop a single leading "AgentName: " (or "AgentName (variant): ") prefix. Prefix-less strings and
 *  strings that don't start with a letter are returned unchanged.
 *  #2408: agent names are single words ("DrawdownGuard", "LSTMSignal"), optionally followed by a
 *  parenthetical variant. The new plain-English producer strings open with multi-word sentence
 *  lead-ins ("Market regime today:", "AI news read:") — those are content, not a prefix, and must
 *  survive intact, so the name part must not contain spaces. */
export function stripAgentPrefix(reasoning: string): string {
  return reasoning.replace(/^\s*[A-Za-z][\w.-]*(?:\s*\([\w .-]*\))?:\s+/, "");
}

// #2134: the raw reasoning is precise but cryptic ("action=buy conf=0.80 → score=0.750"). For the
// Decision page, turn the common per-agent patterns into a plain-English sentence. The RAW string is
// untouched in the audit chain — this is display-only. Returns null when nothing matches, so the
// caller falls back to the de-prefixed raw string (already English after #2132).
function firstMatch(re: RegExp, s: string): string | null {
  const m = re.exec(s);
  return m ? m[1] : null;
}

export function humanizeReasoning(name: string, reasoning: string): string | null {
  const n = (name || "").toLowerCase();

  // RLConfidence: "RLConfidence: action=buy conf=0.80 → score=0.750"
  if (n.includes("rl")) {
    const action = firstMatch(/action=([A-Za-z]+)/i, reasoning);
    const conf = firstMatch(/conf=([\d.]+)/i, reasoning);
    if (action && conf) {
      return `RL policy: ${action.toUpperCase()} (confidence ${Math.round(parseFloat(conf) * 100)}%).`;
    }
  }

  // LSTMSignal: "LSTMSignal: pred=+0.120 action=BUY → continuous score=0.630 (…)"
  if (n.includes("lstm")) {
    const action = firstMatch(/action=([A-Za-z]+)/i, reasoning);
    const score = firstMatch(/score=([\d.]+)/i, reasoning);
    if (action) {
      return `Price model: ${action.toUpperCase()}${score ? ` (score ${parseFloat(score).toFixed(2)})` : ""}.`;
    }
  }

  // MomentumAgent: main "Momentum: 12-1M ret=+3.20% (…)"; fallback "… pct=+3.20% → …"
  if (n.includes("momentum")) {
    const pct = firstMatch(/(?:ret|pct)=([+\-\d.]+)\s*%/i, reasoning);
    if (pct !== null) {
      const v = parseFloat(pct);
      return `Momentum ${v >= 0 ? "+" : ""}${v.toFixed(1)}% (${v >= 0 ? "bullish" : "bearish"}).`;
    }
  }

  // VIXAwareRiskAgent: "VIXAware: VIX=18.50 → score=0.55 (continuous risk scaling)"
  if (n.includes("vix")) {
    const vix = firstMatch(/vix=([\d.]+)/i, reasoning);
    if (vix) {
      const v = parseFloat(vix);
      const regime = v < 20 ? "calm" : v < 30 ? "elevated" : "high";
      return `Volatility ${regime} (VIX ${v.toFixed(1)}).`;
    }
  }

  // NewsSentiment: "NewsSentiment: LLM → '…' → score=0.72"
  if (n.includes("news")) {
    const score = firstMatch(/score=([\d.]+)/i, reasoning);
    if (score) {
      const s = parseFloat(score);
      const tone = s > 0.55 ? "bullish" : s < 0.45 ? "bearish" : "neutral";
      return `News sentiment ${tone} (${s.toFixed(2)}).`;
    }
  }

  // DrawdownGuard non-veto: "DrawdownGuard: VETO=False - … drawdown=3.20% (…) → score=…"
  // (the hard-VETO case is rendered separately by the caller with its own explanatory sentence).
  if (n.includes("drawdown")) {
    const dd = firstMatch(/drawdown=([\d.]+)\s*%/i, reasoning);
    if (dd) {
      return `${Math.round(parseFloat(dd))}% below its 30-day high — within limits.`;
    }
  }

  return null;
}
