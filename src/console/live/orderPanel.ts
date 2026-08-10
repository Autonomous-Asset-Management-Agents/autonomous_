// src/console/live/orderPanel.ts — #2190: the Overview order-book panel.
// No React, no side effects.

/**
 * Show the OPEN-ORDERS book on the Overview ONLY in live trading. In paper,
 * market/marketable-limit orders fill instantly so the book is always empty —
 * hide it entirely. Live trading is a Senior/Plus feature, so this also naturally
 * scopes the book to Senior operators. Before the first `/health` poll
 * (`paperTrading === null`) it stays hidden (paper is the default).
 *
 * @param paperTrading `/health.paper_trading`: `false` = live, `true` = paper,
 *   `null` = not polled yet.
 */
export function showOrderBookOnOverview(paperTrading: boolean | null): boolean {
  return paperTrading === false;
}
