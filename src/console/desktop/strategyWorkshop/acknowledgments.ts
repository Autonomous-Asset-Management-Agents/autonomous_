/**
 * Acknowledgment texts of the strategy workshop (STW-4e #4892).
 *
 * Sealed on the WORM chain — English, byte-identical, never translated. The engine records
 * the exact bytes in the `StrategyDefinitionEvent` / `StrategyAssignmentEvent`, so these are
 * part of the record, not UI copy: they stay out of the i18n catalogue (`console.strategyWorkshop`)
 * and this file imports nothing. Pinned by text and SHA-256 in
 * `src/test/strategyWorkshopAcknowledgments.test.tsx`.
 */

/** Live assignment (STW-4 #4630). */
export const ASSIGN_ACK =
  "I confirm assigning this strategy to the live account. Its settings apply to all future trading decisions and the assignment is recorded on a tamper-evident audit trail." as const;

/** Live sizing-method change (#4989). The engine builds the same bytes itself
 *  (`core/trading_settings.py::verfahrenswechsel_text`) and answers 409 on any other text. */
export const liveSizingAck = (from: string, to: string): string =>
  `I confirm changing the live sizing method from "${from}" to "${to}". The change applies to real-money trading and is recorded on a tamper-evident audit trail.`;

/** Paper slot assignment (STW-2c #4953). */
export const PAPER_ASSIGN_ACK =
  "I confirm assigning this strategy to this paper account. Its settings apply to future simulated trading on this account and the assignment is recorded on a tamper-evident audit trail." as const;

/** Create / edit (STW-4c #4873). */
export const SAVE_ACK =
  "I confirm saving this strategy. The change is recorded on a tamper-evident audit trail." as const;

/** Delete (STW-4b #4864). */
export const DELETE_ACK =
  "I confirm deleting this strategy. The deletion is recorded on a tamper-evident audit trail." as const;
