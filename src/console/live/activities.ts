import type { RecentTrade } from "@/lib/api";

/**
 * Activities adapter (DESK-1): map the engine's /recent-trades response (the
 * last N *filled* Alpaca orders) to the console's Activity shape. The engine
 * returns snake_case + a nullable `filled_at`; the console table shows
 * side/qty/price/time, so:
 *   - side is normalised to "buy" | "sell" (an out-of-contract value → "buy",
 *     never crashes the table)
 *   - qty/price use `?? 0` (a real 0 must survive, not be masked as missing)
 *   - `filled_at` parses to a Date, and an unparseable/absent stamp collapses
 *     to null rather than an "Invalid Date".
 */
export interface ConsoleActivity {
  id: string;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price: number;
  filledAt: Date | null;
}

// Accepts both /recent-trades and /activities responses — both carry `trades`.
export function adaptActivities(
  resp: { trades?: RecentTrade[] } | null | undefined,
): ConsoleActivity[] {
  const raw = Array.isArray(resp?.trades) ? resp!.trades! : [];
  return raw.map((t) => {
    const d = t.filled_at ? new Date(t.filled_at) : null;
    return {
      id: String(t.id ?? ""),
      symbol: String(t.symbol ?? ""),
      side: t.side === "sell" ? "sell" : "buy",
      qty: t.qty ?? 0,
      price: t.price ?? 0,
      filledAt: d && !Number.isNaN(d.getTime()) ? d : null,
    };
  });
}
