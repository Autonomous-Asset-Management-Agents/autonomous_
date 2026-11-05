/**
 * Shared state notice of the strategy workshop (UX-Konzept #4628, "Zustände"; built with
 * STW-4b #4864). Each state is a `surface-flat` notice in place of the content with at most
 * one action; only `low-memory` has a second one ("Start anyway / Cancel", deviation). STW-9c
 * (#4918) adds `no-keys`, `engine-off` and `edition-limit` for flow 3 and `no-data`, flow 4
 * reduced to its notice T21 (deviation); STW-2b (#4934) adds `duplicate-account` for flow 2,
 * STW-6c (#4952) `low-memory`. Default texts come from
 * `console.strategyWorkshop.state.<Kennzeichen>` (#4892); `empty` has one text per flow and
 * `no-data` is T21, so their callers pass the text.
 */
import { useTranslation } from "react-i18next";

export type WorkshopStateKind =
  | "empty" | "loading" | "error" | "no-keys" | "duplicate-account" | "engine-off" | "low-memory" | "edition-limit"
  | "no-data";

const DEFAULT_TEXT_KEY: Partial<Record<WorkshopStateKind, string>> = {
  loading: "console.strategyWorkshop.state.loading.text",
  error: "console.strategyWorkshop.state.error.text",
  "no-keys": "console.strategyWorkshop.state.no-keys.text",
  "duplicate-account": "console.strategyWorkshop.state.duplicate-account.text",
  "engine-off": "console.strategyWorkshop.state.engine-off.text",
  "low-memory": "console.strategyWorkshop.state.low-memory.text",
  "edition-limit": "console.strategyWorkshop.state.edition-limit.text",
};

export function WorkshopState({
  kind,
  text,
  action,
  onAction,
  secondaryAction,
  onSecondaryAction,
  actionDisabled = false,
}: {
  kind: WorkshopStateKind;
  text?: string;
  action?: string;
  onAction?: () => void;
  secondaryAction?: string;
  onSecondaryAction?: () => void;
  /** #5142: the action stays visible but locked (paper accounts: restart-required). */
  actionDisabled?: boolean;
}) {
  const { t } = useTranslation();
  const defaultKey = DEFAULT_TEXT_KEY[kind];
  const shown = text ?? (defaultKey ? t(defaultKey) : "");
  if (kind === "loading") {
    return (
      <div className="surface-flat px-4 py-3" role="status" data-state="loading">
        <span className="text-[13px] text-white/55">{shown}</span>
        <div className="mt-3 flex flex-col gap-2" aria-hidden="true">
          {[0, 1].map((i) => (
            <div key={i} className="h-4 rounded bg-white/5" />
          ))}
        </div>
      </div>
    );
  }
  const actionButton = action && onAction && (
    <button type="button" className="btn" disabled={actionDisabled} onClick={onAction}>
      {action}
    </button>
  );
  return (
    <div
      className="surface-flat px-4 py-3 flex items-center justify-between gap-4"
      role="status"
      data-state={kind}
    >
      <span className="text-[13px] text-white/70">{shown}</span>
      {secondaryAction && onSecondaryAction ? (
        <div className="flex gap-2">
          {actionButton}
          <button type="button" className="btn" onClick={onSecondaryAction}>
            {secondaryAction}
          </button>
        </div>
      ) : (
        actionButton
      )}
    </div>
  );
}
