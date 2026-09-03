import { useEffect, useMemo, useRef, useState } from "react";
import { isDesktop, getSetupState, saveSetupState } from "@/lib/desktopBridge";
import { OnOffSegment } from "@/console/shared/SegmentPicker";
import { getTradingSettings, postTradingSettings } from "@/lib/api";
import type { TradingSettingsApplyResult } from "@/lib/api";
import type { TradingSettings } from "@/lib/api";
import { usePendingSettings } from "@/console/desktop/usePendingSettings";
import {
  SettingsApplyModal,
  type SettingsApplyChange,
} from "@/console/desktop/SettingsApplyModal";
import {
  ADVANCED_GROUPS,
  ADVANCED_KEYS,
  type AdvancedField,
} from "@/console/desktop/advancedTradingFields";

/**
 * "Advanced" expert area (UXC-1 S5, #3158) — collapsed by default, rendered INSIDE
 * the Trading-controls card (owner decision 02.09.2026: no separate risk-limits
 * card). Twenty expert parameters in seven groups, data-driven from
 * advancedTradingFields.ts (the neutrality guard tests THE SAME table).
 *
 * Values shown are the ENGINE-effective ones from the S2 GET (never UI state);
 * every edit is a pending value; "Apply changes" hands the diff (exact engine keys)
 * to the S3 WORM-apply seam — nothing persists from here. Client-side bound and
 * cross-field mirrors (sizing chain, equal-weight slot, displacement headroom) only
 * PREVENT obviously invalid applies; the server clamps authoritatively
 * (core/trading_settings.py). The 8% hard stop renders as a read-only guardrail.
 */

const APPLY_ACK =
  "I confirm these trading-parameter changes. They apply to all future trading decisions and are recorded on a tamper-evident audit trail.";

interface AdvancedTradingSectionProps {
  /** Test/extension seam: when given, Apply hands the pending diff here INSTEAD of
   *  running the built-in WORM flow (record → persist → restart, #3156). */
  onApply?: (pending: Record<string, string>) => void;
}

const numEq = (a: string | undefined, b: string | undefined): boolean => {
  if (a === b) return true;
  const na = Number(a);
  const nb = Number(b);
  return Number.isFinite(na) && Number.isFinite(nb) && na === nb;
};

export function AdvancedTradingSection({ onApply }: AdvancedTradingSectionProps) {
  const [expanded, setExpanded] = useState(false);
  const [settings, setSettings] = useState<TradingSettings | null>(null);
  const [failed, setFailed] = useState(false);
  const [positions, setPositions] = useState(10);
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
    getSetupState()
      .then((state) => {
        const n = Number((state as Record<string, unknown>)?.FULL_UNIVERSE_MAX_POSITIONS);
        if (alive && Number.isFinite(n) && n > 0) setPositions(Math.round(n));
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

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

  // Client-side cross-field mirrors (server authoritative, core/trading_settings.py).
  const violations: string[] = [];
  if (settings) {
    const minP = Number(val("MIN_POSITION_PERCENT"));
    const maxP = Number(val("MAX_POSITION_PERCENT"));
    const cap = Number(val("MAX_POSITION_PERCENT_SIZING"));
    const slot = 1 / Math.max(1, positions);
    if (maxP > cap) {
      violations.push(
        "Maximum position size is above the sizing cap — lower it or raise the cap.",
      );
    }
    if (minP >= maxP) {
      violations.push("Minimum position size must stay below the maximum position size.");
    }
    if (minP > slot + 1e-9) {
      violations.push(
        `Minimum position size does not fit the equal-weight slot of ${positions} positions (1/${positions}).`,
      );
    }
    if (Number(val("ROUND_TABLE_TOP_K_EVAL")) <= positions) {
      violations.push(
        `Round-table candidates must stay above the configured position count (${positions}).`,
      );
    }
    if (Number(val("SIGNAL_SELL_THRESHOLD")) >= Number(val("SIGNAL_BUY_THRESHOLD"))) {
      violations.push("The sell threshold must stay below the buy threshold.");
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

  const renderField = (f: AdvancedField) => {
    const v = val(f.key);
    return (
      <div key={f.key} data-key={f.key} className="py-3 border-t border-white/6">
        <div className="flex items-start justify-between gap-4">
          <div className="max-w-sm">
            <div className="text-[13px] font-semibold text-white/90">
              {f.label}
              {dirty(f.key) && (
                <span className="ml-2 rounded-full bg-white/8 px-2 py-0.5 text-[10px] font-medium text-white/60 align-middle">
                  Changed
                </span>
              )}
            </div>
            <div
              data-copy="description"
              className="text-[11.5px] text-white/45 leading-relaxed mt-0.5"
            >
              {f.desc}
            </div>
          </div>
          <div className="flex items-center gap-4">
            {f.kind === "toggle" && (
              <OnOffSegment
                ariaLabel={f.label}
                value={v === "true"}
                onChange={(next) => pending.set(f.key, next ? "true" : "false")}
              />
            )}
            {f.kind === "select" && (
              <select
                aria-label={f.label}
                value={v}
                onChange={(e) => pending.set(f.key, e.target.value)}
                className="rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90"
              >
                {(f.choices ?? []).map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            )}
            {f.kind === "slider" && (
              <span className="num text-[12px] text-white/70 w-12 text-right">{v}</span>
            )}
          </div>
        </div>
        {f.kind === "slider" && (
          <div className="flex items-center gap-3 mt-3">
            <span className="num text-[10.5px] text-white/30">{f.min}</span>
            <input
              type="range"
              className="flex-1 accent-[#00c27a]"
              min={f.min}
              max={f.max}
              step={f.step}
              value={Number(v) || f.min}
              onChange={(e) => pending.set(f.key, e.target.value)}
              aria-label={f.label}
            />
            <span className="num text-[10.5px] text-white/30">{f.max}</span>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="pt-4 border-t border-white/8 mt-4">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between text-left"
      >
        <span className="sublabel">Advanced</span>
        <span className="text-[11px] text-white/40">
          {expanded ? "Hide" : "Show"} expert parameters
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
              {g.fields.map(renderField)}
              {g.id === "stops" && (
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
              )}
            </div>
          ))}

          {violations.length > 0 && (
            <div className="surface-flat mt-3 px-4 py-3">
              {violations.map((v) => (
                <div key={v} className="text-[11.5px] text-[#FF453A] leading-relaxed" role="alert">
                  {v}
                </div>
              ))}
            </div>
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
                    {current?.[key]} {"→"} {to}
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
              disabled={violations.length > 0}
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
                    from: current?.[key] ?? "",
                    to,
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
            record={async (nonce) => {
              applyResultRef.current = await postTradingSettings(
                applyDiffRef.current,
                APPLY_ACK,
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
                .then((s) => setSettings(s))
                .catch(() => setFailed(true));
            }}
          />
        </>
      )}
    </div>
  );
}
