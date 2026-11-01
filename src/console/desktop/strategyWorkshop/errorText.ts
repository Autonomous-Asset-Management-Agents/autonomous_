import { HttpError } from "@/lib/api";

export const KNOWN_ERROR_CODES = [
  "strategy_name_length", "guardrail_in_strategy", "strategy_limit_reached", "unknown_setting",
  "setting_value_not_allowed", "setting_value_type", "setting_value_out_of_range", "setting_text_too_long",
  "ranking_prompt_missing", "ranking_list_missing", "request_invalid", "strategy_not_found",
  "nonce_reused", "hitl_required", "strategy_assigned", "sizing_change_unconfirmed", "audit_write_failed",
  "strategy_store_unreadable", "audit_chain_unreadable",
] as const;

export type Translate = (key: string) => string;
const KNOWN = new Set<string>(KNOWN_ERROR_CODES);

export function workshopErrorText(t: Translate, err: unknown, fallbackKey = "console.strategyWorkshop.state.error.text"): string {
  if (err instanceof HttpError) {
    if (err.code && KNOWN.has(err.code)) return t(`console.strategyWorkshop.errors.${err.code}`);
    if (typeof err.detail === "string" && err.detail) return err.detail;
  }
  return t(fallbackKey);
}
