// src/console/live/orderPanel.ts — #2190: the Overview order-book panel.
// No React, no side effects.

/**
 * Show the OPEN-ORDERS book on the Overview ONLY in live trading. In paper,
 * market/marketable-limit orders fill instantly so the book is always empty —
 * hide it entirely. Live trading is a Senior/Plus feature, so this also naturally
 * scopes the book to Senior operators. Before the first `/health` poll
 * (`paperTrading === null`) it stays hidden (paper is the default).
 *
 * #overview-ia: additionally hidden under FULL-AUTONOMOUS live (Mode C): there nothing waits for a
 * human, so a "working orders awaiting you" book is meaningless. The book is for the manual /
 * semi-autonomous modes where orders actually queue for approval. Shown only when live AND not
 * full-autonomous.
 *
 * @param paperTrading `/health.paper_trading`: `false` = live, `true` = paper,
 *   `null` = not polled yet.
 * @param autonomousLive live AND Mode C (HITL_AUTONOMOUS_UNLIMITED) — see `useModeCLive`.
 */
export function showOrderBookOnOverview(
  paperTrading: boolean | null,
  autonomousLive: boolean = false,
): boolean {
  return paperTrading === false && !autonomousLive;
}
