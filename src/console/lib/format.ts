/** Console number/currency formatters (G3, #1050). Ported from the bundle. */

export const fmtEUR = (n: number, opts: { compact?: boolean; sign?: boolean } = {}) => {
  const v = Math.abs(n);
  const prefix = opts.sign ? (n >= 0 ? "+" : "−") : n < 0 ? "−" : "";
  if (opts.compact && v >= 1000) {
    const k = v / 1000;
    if (k >= 1000) return `${prefix}€${(k / 1000).toFixed(2)}M`;
    return `${prefix}€${k.toFixed(2)}k`;
  }
  return `${prefix}€${v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

// USD variant ($ + en-US grouping) for the public live demo, whose Alpaca paper
// account is denominated in USD. (fmtEUR above stays €-labelled for the console.)
export const fmtUSD = (n: number, opts: { compact?: boolean; sign?: boolean } = {}) => {
  const v = Math.abs(n);
  const prefix = opts.sign ? (n >= 0 ? "+" : "−") : n < 0 ? "−" : "";
  if (opts.compact && v >= 1000) {
    const k = v / 1000;
    if (k >= 1000) return `${prefix}$${(k / 1000).toFixed(2)}M`;
    return `${prefix}$${k.toFixed(2)}k`;
  }
  return `${prefix}$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

// PR-B: the console must show the REAL account currency (Alpaca account.currency),
// not a hardcoded €. `currencySymbol` maps an ISO-4217 code to a display symbol
// (fail-soft: $ for empty/undefined, the code itself for currencies we don't
// symbolise); `fmtMoney` is the currency-agnostic formatter used at call sites.
export const currencySymbol = (iso?: string): string => {
  if (!iso) return "$";
  const map: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", JPY: "¥" };
  return map[iso.toUpperCase()] ?? iso.toUpperCase();
};

export const fmtMoney = (
  n: number,
  symbol: string,
  opts: { compact?: boolean; sign?: boolean } = {},
) => {
  const v = Math.abs(n);
  const prefix = opts.sign ? (n >= 0 ? "+" : "−") : n < 0 ? "−" : "";
  if (opts.compact && v >= 1000) {
    const k = v / 1000;
    if (k >= 1000) return `${prefix}${symbol}${(k / 1000).toFixed(2)}M`;
    return `${prefix}${symbol}${k.toFixed(2)}k`;
  }
  return `${prefix}${symbol}${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

export const fmtPct = (n: number, digits = 2) =>
  `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(digits)}%`;

export const fmtNum = (n: number, digits = 2) =>
  n.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });

// Share quantity — fractional-aware. Whole shares render clean ("10", "1,234"); fractional /
// notional fills (Alpaca partial or dollar-based orders, e.g. 0.2057 sh) keep 2–4 decimals
// instead of being rounded to "0". #2131: the trades/positions tables used fmtNum(qty, 0) —
// 0 decimals — which truncated a real fractional fill to "0" while the value column was
// non-zero. #2587: the de-DE locale rendered fractional counts with a decimal COMMA inside
// the en-US UI ("15,951" read as fifteen thousand shares) → en-US, decimal POINT. Sub-0.005
// fills widen to 6 decimals so a real fractional fill still never collapses to "0.00".
export const fmtQty = (n: number) => {
  if (Number.isInteger(n)) return n.toLocaleString("en-US");
  const max = Math.abs(n) < 0.005 ? 6 : 4;
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: max });
};

// Format a TFT scenario return (a percent, e.g. 1.5 → "+1.50%"). Magnitude-aware
// so a real-but-small projected move never collapses to a dead-looking "0.0%":
//   ≥0.1%  → two decimals ("+1.50%")
//   <0.1%  → basis points ("+5 bps")    (1% = 100 bps; v is a percent → ×100)
//   exact 0 → "0.00%"   ·   null → "—"
export const fmtMlReturn = (v: number | null): string => {
  if (v == null) return "—";
  if (v === 0) return "0.00%";
  if (Math.abs(v) < 0.1) {
    const bps = Math.round(v * 100);
    return bps >= 0 ? `+${bps} bps` : `${bps} bps`;
  }
  return v > 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`;
};

export const fmtTime = (d: Date) =>
  d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });

/** Relative age ("3m ago"). `now` is injectable so tests stay deterministic. */
export const ago = (d: Date, now: number = Date.now()) => {
  const s = Math.max(0, Math.floor((now - d.getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};
