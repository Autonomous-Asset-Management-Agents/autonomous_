// src/console/live/hitlAutonomy.ts — #2454: human-readable summary of the live
// autonomy posture for the go-live consent disclosure. No React, no side effects.

import type { HitlPolicy } from "@/lib/api";

export interface AutonomyDisclosure {
  /** Short state label. */
  label: string;
  /** One-line plain-language consequence. */
  detail: string;
  /** true when orders execute without any per-trade human approval (Mode C). */
  fullAutonomous: boolean;
}

const eur = (n: number) =>
  new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(n);

/**
 * Describe how live trading will execute, given the current HITL policy.
 * #2454 (revised): the live autonomy DEFAULT is a 10%-of-equity per-trade limit
 * (semi-autonomous), set/confirmed by the operator at go-live. Only an EXPLICIT
 * ``HITL_AUTONOMOUS_UNLIMITED`` is full autonomous; a positive limit is
 * semi-autonomous; the fail-closed floor (no limit, not unlimited) is manual —
 * every order needs approval.
 */
export function autonomyDisclosure(policy: HitlPolicy | null): AutonomyDisclosure {
  const perTrade = policy?.HITL_MAX_VALUE_PER_TRADE ?? 0;
  const perDay = policy?.HITL_MAX_VALUE_PER_DAY ?? 0;
  const unlimited = policy?.HITL_AUTONOMOUS_UNLIMITED === true;

  if (unlimited) {
    return {
      label: "Full autonomous",
      detail: "Every order executes automatically — no per-trade approval.",
      fullAutonomous: true,
    };
  }

  if (perTrade > 0 || perDay > 0) {
    const parts: string[] = [];
    if (perTrade > 0) parts.push(`${eur(perTrade)} per trade`);
    if (perDay > 0) parts.push(`${eur(perDay)} per day`);
    return {
      label: "Semi-autonomous",
      detail: `Orders up to ${parts.join(" / ")} execute automatically; larger ones wait for your approval.`,
      fullAutonomous: false,
    };
  }

  return {
    label: "Manual approval",
    detail: "Every order waits for your approval before it executes.",
    fullAutonomous: false,
  };
}

/**
 * The default per-trade autonomy limit offered at go-live: 10% of the live
 * account equity (#2454). Rounded to whole euros; non-finite/≤0 equity → 0
 * (the flow then asks the operator to set a value explicitly).
 */
export function defaultPerTradeLimit(equity: number | null | undefined): number {
  if (equity == null || !Number.isFinite(equity) || equity <= 0) return 0;
  return Math.round(equity * 0.1);
}
