export type ExecutionMode = "auto" | "hitl";

/**
 * Resolve the persisted execution-mode preference into the UI default (#1653).
 *
 * Default = **autonomous** ("auto"), so the UI label matches the already-implemented behaviour:
 * paper auto-starts the strategy and trades autonomously (#1442). The operator only sees "hitl"
 * if they explicitly chose it.
 *
 * Live safety is unaffected: a verified live boot is ALWAYS started with HITL_ENABLED=true
 * (native-engine-manager.cjs → config.py _enforce_hitl_boot_gate), regardless of this UI value.
 */
export function resolveExecutionMode(stored: string | null): ExecutionMode {
  return stored === "hitl" ? "hitl" : "auto";
}
