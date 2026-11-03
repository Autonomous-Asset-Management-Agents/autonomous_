import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { isDesktop, saveSetupState } from "@/lib/desktopBridge";
import { getTradingSettings, postTradingSettings } from "@/lib/api";
import type { TradingSettingsApplyResult } from "@/lib/api";
import type { TradingSettings } from "@/lib/api";
import { usePendingSettings } from "@/console/desktop/usePendingSettings";
import {
  SettingsApplyModal,
  type SettingsApplyChange,
} from "@/console/desktop/SettingsApplyModal";
import {
  addViolation,
  ADVANCED_GROUPS,
  ADVANCED_KEYS,
  effectivePositions,
  inertHint,
  isFieldVisible,
  minimumFitsSlotCheckApplies,
  numEq,
  type AdvancedField,
  type FieldViolations,
} from "@/console/desktop/advancedTradingFields";
import { RegimePreviewBlock } from "@/console/desktop/RegimePreviewBlock";
import { SizingPreviewBlock } from "@/console/desktop/SizingPreviewBlock";
import { SizingReferences } from "@/console/desktop/SizingReferences";
import { AdvancedFieldRow } from "@/console/desktop/AdvancedFieldRow";
import { formatValue } from "@/console/desktop/fieldUnits";

/**
 * "Advanced" expert area (UXC-1 S5, #3158) — collapsed by default, rendered INSIDE
 * the Trading-controls card (owner decision 02.09.2026: no separate risk-limits
 * card). Expert parameters in the groups of the decision flow (#5003), data-driven from
 * advancedTradingFields.ts (the neutrality guard tests THE SAME table); each row is
 * an AdvancedFieldRow.tsx, shared with the strategy editor (#4873).
 *
 * Values shown are the ENGINE-effective ones from the S2 GET (never UI state);
 * every edit is a pending value; "Apply changes" hands the diff (exact engine keys)
 * to the S3 WORM-apply seam — nothing persists from here. Client-side bound and
 * cross-field mirrors (sizing chain, equal-weight slot, displacement headroom) only
 * PREVENT obviously invalid applies; the server clamps authoritatively
 * (core/trading_settings.py). The 8% hard stop renders as a read-only guardrail.
 */

/** #5003: the fixed limits belong to the stops, so they render ahead of the trailing
 *  rules that follow them in the Exits group. */
const FIRST_FIELD_AFTER_STOPS = "EXIT_TRAIL_PROFILE";

/** #5046: restartEngine resolves when the engine is spawned, not when it answers, so the first
 *  read after an apply can fail. 30 × 3 s spans the 90 s of SWITCH_TIMEOUT_MS. */
const REFERENCE_RETRIES = 30;
const REFERENCE_RETRY_MS = 3000;

const APPLY_ACK =
  "I confirm these trading-parameter changes. They apply to all future trading decisions and are recorded on a tamper-evident audit trail.";

interface AdvancedTradingSectionProps {
  /** Test/extension seam: when given, Apply hands the pending diff here INSTEAD of
   *  running the built-in WORM flow (record → persist → restart, #3156). */
  onApply?: (pending: Record<string, string>) => void;
  /** #5046: raised after an apply elsewhere on the card; SizingReferences reads the engine again. */
  refreshToken?: number;
}

export function AdvancedTradingSection({ onApply, refreshToken }: AdvancedTradingSectionProps) {
  const [expanded, setExpanded] = useState(false);
  const [settings, setSettings] = useState<TradingSettings | null>(null);
  // #5046: a second state for the block only. A new `settings` is a new baseline for
  // usePendingSettings, which would drop the open changes in this section.
  const [referenceSettings, setReferenceSettings] = useState<Partial<TradingSettings> | null>(null);
  const [failed, setFailed] = useState(false);
  // #3156: the built-in WORM apply flow — pending diff staged for the shared modal.
  const [applyChanges, setApplyChanges] = useState<SettingsApplyChange[] | null>(null);
  const applyDiffRef = useRef<Record<string, string>>({});
  const applyResultRef = useRef<TradingSettingsApplyResult | null>(null);

  useEffect(() => {
    if (!isDesktop()) return;
    let alive = true;
    // Defensive: in partially-mocked test environments (and any runtime oddity)
    // a missing/failing GET degrades to the neutral unavailable state, never a crash.
    // The Promise.resolve() hop also routes a SYNCHRONOUS throw into .catch, so no
    // setState ever runs synchronously inside the effect body (react-hooks lint).
    Promise.resolve()
      .then(() => getTradingSettings())
      .then((s) => {
        if (alive) setSettings(s);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!refreshToken || !isDesktop()) return;
    let alive = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const read = (retriesLeft: number) => {
      Promise.resolve()
        .then(() => getTradingSettings())
        .then((s) => {
          if (alive) setReferenceSettings(s);
        })
        .catch(() => {
          if (!alive) return;
          setReferenceSettings({});
          if (retriesLeft > 0) {
            retry = setTimeout(() => read(retriesLeft - 1), REFERENCE_RETRY_MS);
          }
        });
    };
    read(REFERENCE_RETRIES);
    return () => {
      alive = false;
      clearTimeout(retry);
    };
  }, [refreshToken]);

  const current = useMemo(() => {
    if (!settings) return null;
    const out: Record<string, string> = {};
    for (const key of ADVANCED_KEYS) {
      const v = settings.settings?.[key];
      if (v !== undefined) out[key] = v;
    }
    return out;
  }, [settings]);

  const defaults = useMemo(() => {
    if (!settings) return null;
    const out: Record<string, string> = {};
    for (const m of settings.meta ?? []) {
      if (ADVANCED_KEYS.includes(m.key)) out[m.key] = m.default;
    }
    return out;
  }, [settings]);

  const pending = usePendingSettings(current);

  if (!isDesktop()) return null;

  const val = (key: string): string => pending.values?.[key] ?? current?.[key] ?? "";
  const dirty = (key: string): boolean =>
    defaults !== null && !numEq(val(key), defaults[key]);
  // A dependent field is HIDDEN while its parent toggle is off — a knob shown as
  // active for a disabled feature reads as operable when it does nothing. Keys off
  // the PENDING value, so toggling the parent hides/reveals its children at once.
  // #4660: the same rule covers value-dependent fields (visibleWhen).
  const isVisible = (f: AdvancedField): boolean => isFieldVisible(f, val);

  // Client-side cross-field mirrors (server authoritative, core/trading_settings.py).
  const positions = effectivePositions(settings);
  const referenced = referenceSettings ?? settings ?? {};
  // #5001: each message hangs at a field that is never hidden, so a blocked Apply stays explained.
  const violations: FieldViolations = {};
  if (settings) {
    const minP = Number(val("MIN_POSITION_PERCENT"));
    const maxP = Number(val("MAX_POSITION_PERCENT"));
    const cap = Number(val("MAX_POSITION_PERCENT_SIZING"));
    if (maxP > cap) {
      // #4987: no "raise the cap" — in the equal-weight methods the cap is locked.
      addViolation(violations, "MAX_POSITION_PERCENT", "Maximum position size must stay at or below the sizing cap.");
    }
    if (minP >= maxP) {
      addViolation(violations, "MIN_POSITION_PERCENT", "Minimum position size must stay below the maximum position size.");
    }
    if (positions !== null && minP > 1 / positions + 1e-9 && minimumFitsSlotCheckApplies(val)) {
      addViolation(
        violations,
        "MIN_POSITION_PERCENT",
        `Minimum position size does not fit the equal-weight slot of ${positions} positions (1/${positions}).`,
      );
    }
    if (positions !== null && Number(val("ROUND_TABLE_TOP_K_EVAL")) <= positions) {
      addViolation(
        violations,
        "ROUND_TABLE_TOP_K_EVAL",
        `Round-table candidates must stay above the configured position count (${positions}).`,
      );
    }
    if (Number(val("SIGNAL_SELL_THRESHOLD")) >= Number(val("SIGNAL_BUY_THRESHOLD"))) {
      addViolation(violations, "SIGNAL_SELL_THRESHOLD", "The sell threshold must stay below the buy threshold.");
    }
  }

  const diff = (): Record<string, string> => {
    if (!current || !pending.values) return {};
    const out: Record<string, string> = {};
    for (const key of ADVANCED_KEYS) {
      const to = pending.values[key];
      if (to !== undefined && !numEq(to, current[key])) out[key] = to;
    }
    return out;
  };

  const pendingRows = Object.entries(diff());

  const fieldLabel = (key: string): string => {
    for (const g of ADVANCED_GROUPS) {
      const f = g.fields.find((x) => x.key === key);
      if (f) return f.label;
    }
    return key;
  };

  // #4999: what the confirm shows reads like the row; what is sent stays the stored value.
  const shown = (key: string, raw: string): string => {
    const f = ADVANCED_GROUPS.flatMap((g) => g.fields).find((x) => x.key === key);
    return formatValue(f?.unit, raw);
  };

  const renderField = (f: AdvancedField) => (
    <AdvancedFieldRow
      key={f.key}
      field={f}
      value={val(f.key)}
      dirty={dirty(f.key)}
      lockHint={inertHint(f, val)}
      decimals={settings?.meta?.find((m) => m.key === f.key)?.decimals}
      defaultValue={defaults?.[f.key]}
      errors={violations[f.key]}
      onChange={(next) => pending.set(f.key, next)}
    />
  );

  const fixedLimits = settings && (
    <>
      <div
        data-testid="guardrail-hard-stop"
        className="flex items-baseline justify-between gap-4 py-3 border-t border-white/6"
      >
        <div className="max-w-sm">
          <div className="text-[13px] font-semibold text-white/90">
            Hard stop
            <span className="ml-2 rounded-full bg-white/8 px-2 py-0.5 text-[10px] font-medium text-white/50 align-middle">
              Fixed
            </span>
          </div>
          <div
            data-copy="description"
            className="text-[11.5px] text-white/45 leading-relaxed mt-0.5"
          >
            The engine-fixed backstop. Not adjustable here or anywhere in the app.
          </div>
        </div>
        <span className="num text-[12px] text-white/60">
          {settings.guardrails?.HARD_STOP_LOSS_PCT ?? "-8.0"}
        </span>
      </div>
      <div
        data-testid="guardrail-loss-time-tiers"
        className="flex items-baseline justify-between gap-4 py-3 border-t border-white/6"
      >
        <div className="max-w-sm">
          <div className="text-[13px] font-semibold text-white/90">
            Loss time tiers
            <span className="ml-2 rounded-full bg-white/8 px-2 py-0.5 text-[10px] font-medium text-white/50 align-middle">
              Fixed
            </span>
          </div>
          <div
            data-copy="description"
            className="text-[11.5px] text-white/45 leading-relaxed mt-0.5"
          >
            How long a loss may persist before the exit pressure rises. Engine-fixed.
          </div>
        </div>
        <span className="num text-[12px] text-white/60">4 h / 24 h / 72 h</span>
      </div>
    </>
  );

  return (
    <div className="pt-4 border-t border-white/8 mt-4">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-3 text-left rounded-lg px-3 py-2.5 -mx-3 bg-white/[0.03] hover:bg-white/[0.06] transition-colors"
      >
        <span className="flex items-baseline gap-2 min-w-0">
          <span className="sublabel">Advanced</span>
          <span className="num text-[10px] text-white/45 rounded-full bg-white/8 px-2 py-0.5 whitespace-nowrap">
            {ADVANCED_KEYS.length} parameters
          </span>
          {!expanded && (
            <span className="text-[11px] text-white/35 truncate">
              · thresholds, sizing, exits &amp; pacing
            </span>
          )}
        </span>
        <span className="flex items-center gap-1.5 text-[11px] text-white/45 whitespace-nowrap">
          {expanded ? "Hide" : "Show"}
          <span aria-hidden className={expanded ? "rotate-180" : ""}>▾</span>
        </span>
      </button>

      {expanded && failed && (
        <div className="text-[12px] text-white/45 py-3">
          Engine not available — the current settings cannot be shown.
        </div>
      )}

      {expanded && !failed && settings && (
        <>
          <p
            data-copy="description"
            className="text-[11px] text-white/30 mt-2 mb-1 max-w-md leading-relaxed"
          >
            Expert parameters. Each row states only what it technically does. Changes
            stay pending until you apply and confirm them; the engine records the
            change and restarts with the new values. Bounds are enforced by the engine.
          </p>

          {ADVANCED_GROUPS.map((g) => (
            <div
              key={g.id}
              className="surface-flat mt-3 px-4 pt-3 pb-1"
            >
              <div className="sublabel">{g.title}</div>
              {g.fields.filter(isVisible).map((f) => (
                <Fragment key={f.key}>
                  {f.key === FIRST_FIELD_AFTER_STOPS && fixedLimits}
                  {renderField(f)}
                </Fragment>
              ))}
              {g.id === "sizing" && (
                <>
                  <SizingPreviewBlock />
                  <RegimePreviewBlock />
                  <SizingReferences
                    settings={referenced}
                    positions={effectivePositions(referenced)}
                  />
                </>
              )}
            </div>
          ))}

          {positions === null && (
            <p
              data-testid="slot-check-skipped"
              className="text-[11.5px] text-white/45 mt-3 leading-relaxed"
            >
              The engine did not report its position count, so the checks against it are
              skipped here. The engine still enforces them.
            </p>
          )}

          {pendingRows.length > 0 && (
            <div
              data-testid="pending-changes-advanced"
              className="surface-flat mt-3 px-4 py-3"
            >
              <div className="eyebrow mb-1">Pending changes</div>
              <p
                data-copy="description"
                className="text-[11px] text-white/30 mb-2 max-w-md leading-relaxed"
              >
                Nothing takes effect yet. Applying records each change and restarts the
                engine with the new values.
              </p>
              {pendingRows.map(([key, to]) => (
                <div
                  key={key}
                  className="flex items-baseline justify-between gap-4 border-t border-white/6 py-1.5"
                >
                  <span className="text-[12px] text-white/70">{fieldLabel(key)}</span>
                  <span className="num text-[12px] text-white/60">
                    {shown(key, current?.[key] ?? "")} {"→"} {shown(key, to)}
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="flex items-center justify-between gap-4 pt-4 border-t border-white/8 mt-3">
            <button
              type="button"
              onClick={() => defaults && pending.replaceAll(defaults)}
              className="btn"
            >
              Default settings
            </button>
            <button
              type="button"
              disabled={Object.keys(violations).length > 0}
              onClick={() => {
                const changes = diff();
                if (Object.keys(changes).length === 0) return;
                if (onApply) {
                  onApply(changes);
                  return;
                }
                applyDiffRef.current = changes;
                setApplyChanges(
                  Object.entries(changes).map(([key, to]) => ({
                    key,
                    label: fieldLabel(key),
                    from: shown(key, current?.[key] ?? ""),
                    to: shown(key, to),
                  })),
                );
              }}
              className="btn btn-primary"
            >
              Apply changes
            </button>
          </div>

          <SettingsApplyModal
            open={applyChanges !== null}
            changes={applyChanges ?? []}
            record={async (nonce, sizingAck) => {
              applyResultRef.current = await postTradingSettings(
                applyDiffRef.current,
                sizingAck ?? APPLY_ACK,
                nonce,
              );
            }}
            persist={async () => {
              const res = applyResultRef.current;
              const toPersist = Object.fromEntries(
                (res?.changes ?? []).map((c) => [c.key, c.to]),
              );
              if (Object.keys(toPersist).length > 0) await saveSetupState(toPersist);
            }}
            onCancel={() => setApplyChanges(null)}
            onApplied={() => {
              setApplyChanges(null);
              applyResultRef.current = null;
              getTradingSettings()
                .then((s) => {
                  setSettings(s);
                  setReferenceSettings(null);
                })
                .catch(() => setFailed(true));
            }}
          />
        </>
      )}
    </div>
  );
}
