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

// #3442 FINDING-03: a capture of `[\d.]+` also matches a bare ".", and parseFloat(".")
// is NaN — which then rides through Math.round into the UI as "NaN% (NaNth pct)".
// This helper is the single place where a captured number becomes a number: the pattern
// demands at least one digit, and a non-finite result reads as "no match" so the caller
// falls back to the raw agent text (the intended display-layer fail-open, #2134).
const NUMBER = String.raw`(\d+(?:\.\d+)?|\.\d+)`;

function firstNumber(pattern: string, flags: string, s: string): number | null {
  const raw = firstMatch(new RegExp(pattern, flags), s);
  if (raw === null) return null;
  const value = parseFloat(raw);
  return Number.isFinite(value) ? value : null;
}

// #3442 FINDING-02: "63th pct" was hardcoded. English ordinals are not "th" throughout —
// and the 11/12/13 exception is the part that hand-rolled versions get wrong.
function ordinal(n: number): string {
  const teens = Math.abs(n) % 100;
  if (teens >= 11 && teens <= 13) return `${n}th`;
  switch (Math.abs(n) % 10) {
    case 1:
      return `${n}st`;
    case 2:
      return `${n}nd`;
    case 3:
      return `${n}rd`;
    default:
      return `${n}th`;
  }
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

  // VIXAwareRiskAgent. #3440: since IMPLIED_VOL_FORECAST_ENABLED default-ON (#3094),
  // this agent reports the name's OWN 30-day implied volatility + its cross-section
  // rank, not the market VIX — e.g. "Expected swing for this stock (30-day implied
  // volatility) at 0.2533 — rank 0.070 against the 35 symbols…". The old vix= parser
  // no longer matched, so the row fell back to raw text. Humanize the IV form first;
  // keep the legacy VIX= form as a fallback (flag OFF → the agent emits VIX again).
  if (n.includes("vix")) {
    const iv = firstNumber(String.raw`implied volatility\)?\s*at\s*${NUMBER}`, "i", reasoning);
    if (iv !== null) {
      const ivPct = Math.round(iv * 100);
      // rank is a 0..1 cross-section percentile (low = calmer than peers).
      const r = firstNumber(String.raw`rank\s*${NUMBER}`, "i", reasoning);
      if (r !== null) {
        const band = r < 0.34 ? "calm" : r < 0.67 ? "moderate" : "elevated";
        return `Expected swing ${ivPct}% — ${band} (${ordinal(Math.round(r * 100))} pct vs. peers).`;
      }
      return `Expected swing ${ivPct}% (30-day implied volatility).`;
    }
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
