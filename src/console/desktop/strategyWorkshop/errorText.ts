import { HttpError } from "@/lib/api";

export const KNOWN_ERROR_CODES = [
  "strategy_name_length", "strategy_name_reserved", "guardrail_in_strategy", "strategy_limit_reached", "unknown_setting",
  "setting_value_not_allowed", "setting_value_type", "setting_value_out_of_range", "setting_text_too_long",
  "ranking_prompt_missing", "ranking_list_missing", "request_invalid", "strategy_not_found",
  "nonce_reused", "hitl_required", "strategy_assigned", "sizing_change_unconfirmed", "audit_write_failed",
  "strategy_store_unreadable", "audit_chain_unreadable",
] as const;

export type Translate = (key: string) => string;
const KNOWN = new Set<string>(KNOWN_ERROR_CODES);

/**
 * #5166: how a failed read of the workshop shows. 404 — this engine runs without the strategy
 * routes; no HTTP status — no engine answered at all (stopped, starting, port closed); anything
 * else is a real error. Duck-typed like `StrategySimulationCompare::httpStatus`.
 */
export type LoadFailure = "hidden" | "engine-offline" | "error";

export function loadFailure(err: unknown): LoadFailure {
  const status =
    typeof err === "object" && err !== null && typeof (err as { status?: unknown }).status === "number"
      ? (err as { status: number }).status
      : null;
  if (status === null) return "engine-offline";
  return status === 404 ? "hidden" : "error";
}

export function workshopErrorText(t: Translate, err: unknown, fallbackKey = "console.strategyWorkshop.state.error.text"): string {
  if (err instanceof HttpError) {
    if (err.code && KNOWN.has(err.code)) return t(`console.strategyWorkshop.errors.${err.code}`);
    if (typeof err.detail === "string" && err.detail) return err.detail;
  }
  return t(fallbackKey);
}
