import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  fetchLlmTrichter,
  getTradingSettings,
  listStrategyVersions,
  saveStrategy,
  type LlmTrichterResponse,
  type NamedStrategy,
  type StrategySaveResult,
  type StrategyVersion,
  type TradingSettings,
} from "@/lib/api";
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
import { AdvancedFieldRow } from "@/console/desktop/AdvancedFieldRow";
import { grundText } from "@/console/shared/llmTrichterGruende";
import { WorkshopState } from "@/console/desktop/strategyWorkshop/WorkshopStates";
import { SAVE_ACK } from "@/console/desktop/strategyWorkshop/acknowledgments";
import { workshopErrorText } from "@/console/desktop/strategyWorkshop/errorText";
import { effektiverAnbieter, systemAnbieterAus } from "@/console/desktop/strategyWorkshop/promptProvider";

/**
 * Strategy editor (STW-4c #4873; UX-Konzept #4628, Ablauf 1, part 2 — create, duplicate,
 * edit). Opened from the "Strategies" list (#4864).
 *
 * Texts come from `console.strategyWorkshop` (#4892); a failed save shows the catalogue text
 * of the engine's error code. The acknowledgment comes from `acknowledgments.ts`.
 */

const NAME_MAXLEN = 64;

// Catalogue keys of the four rows under T07.
const FIXED_LIMITS = [
  {
    key: "HARD_STOP_LOSS_PCT",
    label: "console.strategyWorkshop.deviation.fixedLimits.hardStopLoss.label",
    desc: "console.strategyWorkshop.deviation.fixedLimits.hardStopLoss.desc",
  },
  {
    key: "HITL_ENABLED",
    label: "console.strategyWorkshop.deviation.fixedLimits.humanApproval.label",
    desc: "console.strategyWorkshop.deviation.fixedLimits.humanApproval.desc",
  },
  {
    key: "RISK_FORBID_LEVERAGE",
    label: "console.strategyWorkshop.deviation.fixedLimits.noLeverage.label",
    desc: "console.strategyWorkshop.deviation.fixedLimits.noLeverage.desc",
  },
  {
    key: "GATEKEEPER_PDT_CROSSDAY_EXEMPTION",
    label: "console.strategyWorkshop.deviation.fixedLimits.patternDayTrading.label",
    desc: "console.strategyWorkshop.deviation.fixedLimits.patternDayTrading.desc",
  },
];

/** A catalogue text with `{name}` placeholders filled in — values the engine settings decide. */
const fill = (text: string, values: Record<string, string | number>): string =>
  text.replace(/\{(\w+)\}/g, (m, name: string) => (name in values ? String(values[name]) : m));

export interface StrategyEditorProps {
  mode: "new" | "edit";
  strategies: NamedStrategy[];
  initial?: NamedStrategy;
  source?: string;
  onSaved: (result: StrategySaveResult) => void;
  onCancel: () => void;
  children?: ReactNode;
}

type Load = "loading" | "error" | "ready";

// #4660: a new strategy starts these at the registry default, not at the engine's value —
// the prompt field is empty when a strategy is created.
const ANBIETER_KEY = "LLM_RANKING_ANBIETER";
const DEFAULT_ON_CREATE = ["LLM_RANKING_QUELLE", "LLM_RANKING_RHYTHMUS", "LLM_RANKING_PROMPT", "LLM_RANKING_LISTE", ANBIETER_KEY];

function valuesFrom(src: NamedStrategy | undefined, ts: TradingSettings): Record<string, string> {
  const out: Record<string, string> = {};
  if (!src) {
    for (const key of ADVANCED_KEYS) {
      const v = ts.settings?.[key];
      if (v !== undefined) out[key] = v;
    }
    for (const m of ts.meta ?? []) {
      if (DEFAULT_ON_CREATE.includes(m.key)) out[m.key] = m.default;
    }
    return out;
  }
  for (const m of ts.meta ?? []) {
    if (ADVANCED_KEYS.includes(m.key)) out[m.key] = m.default;
  }
  return { ...out, ...src.werte };
}

export function StrategyEditor({
  mode,
  strategies,
  initial,
  source,
  onSaved,
  onCancel,
  children,
}: StrategyEditorProps) {
  const { t } = useTranslation();
  const [load, setLoad] = useState<Load>("loading");
  const [settings, setSettings] = useState<TradingSettings | null>(null);
  const [sourceName, setSourceName] = useState(source ?? "");
  const [name, setName] = useState(mode === "edit" ? (initial?.name ?? "") : "");
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedName, setSavedName] = useState<string | null>(null);
  const [saveAck, setSaveAck] = useState<string | null>(null);
  const [versions, setVersions] = useState<StrategyVersion[] | null>(null);
  const [versionsFailed, setVersionsFailed] = useState(false);
  const [verworfen, setVerworfen] = useState<Record<string, string> | null>(null);
  const [letzterLauf, setLetzterLauf] = useState<LlmTrichterResponse | null>(null);
  const mountedRef = useRef(true);

  const editing = mode === "edit" || savedName !== null;

  useEffect(() => {
    mountedRef.current = true;
    let alive = true;
    Promise.resolve()
      .then(() => getTradingSettings())
      .then(
        (ts) => {
          if (!alive) return;
          const src = mode === "edit" ? initial : strategies.find((s) => s.name === (source ?? ""));
          setSettings(ts);
          setValues(valuesFrom(src, ts));
          setLoad("ready");
        },
        () => { if (alive) setLoad("error"); },
      );
    // #4940: only the "System (…)" caption and the local hint read this; without it they stay neutral.
    Promise.resolve()
      .then(() => fetchLlmTrichter())
      .then(
        (lauf) => { if (alive) setLetzterLauf(lauf); },
        () => undefined,
      );
    return () => { alive = false; mountedRef.current = false; };
    // The source is fixed when the editor opens; switching it later goes through chooseSource.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const defaults: Record<string, string> = {};
  const registryChoices: Record<string, string[] | undefined> = {};
  const registryDecimals: Record<string, number | undefined> = {};
  for (const m of settings?.meta ?? []) {
    defaults[m.key] = m.default;
    registryChoices[m.key] = m.choices;
    registryDecimals[m.key] = m.decimals;
  }

  const val = (key: string): string => values[key] ?? "";
  const isVisible = (f: AdvancedField): boolean => isFieldVisible(f, val);

  const systemAnbieter = systemAnbieterAus(letzterLauf, settings?.settings?.[ANBIETER_KEY]);
  const { istLokal, istCloud } = effektiverAnbieter(val(ANBIETER_KEY), systemAnbieter);
  const withSystemCaption = (f: AdvancedField): AdvancedField =>
    f.key === ANBIETER_KEY && f.choiceLabels && systemAnbieter
      ? { ...f, choiceLabels: { ...f.choiceLabels, system: `${f.choiceLabels.system} (${systemAnbieter})` } }
      : f;

  const chooseSource = (next: string) => {
    setSourceName(next);
    if (settings) setValues(valuesFrom(strategies.find((s) => s.name === next), settings));
  };

  const positions = effectivePositions(settings);
  // #5001: each message hangs at a field that is never hidden, so a blocked save stays explained.
  const violations: FieldViolations = {};
  if (load === "ready") {
    const minP = Number(val("MIN_POSITION_PERCENT")), maxP = Number(val("MAX_POSITION_PERCENT"));
    const cap = Number(val("MAX_POSITION_PERCENT_SIZING"));
    if (maxP > cap) addViolation(violations, "MAX_POSITION_PERCENT", t("console.strategyWorkshop.extra.violation.aboveSizingCap"));
    if (minP >= maxP) addViolation(violations, "MIN_POSITION_PERCENT", t("console.strategyWorkshop.extra.violation.minNotBelowMax"));
    if (positions !== null && minP > 1 / positions + 1e-9 && minimumFitsSlotCheckApplies(val)) addViolation(violations, "MIN_POSITION_PERCENT", fill(t("console.strategyWorkshop.extra.violation.minAboveSlot"), { positions }));
    if (positions !== null && Number(val("ROUND_TABLE_TOP_K_EVAL")) <= positions) addViolation(violations, "ROUND_TABLE_TOP_K_EVAL", fill(t("console.strategyWorkshop.extra.violation.candidatesNotAbovePositions"), { positions }));
    if (Number(val("SIGNAL_SELL_THRESHOLD")) >= Number(val("SIGNAL_BUY_THRESHOLD"))) addViolation(violations, "SIGNAL_SELL_THRESHOLD", t("console.strategyWorkshop.extra.violation.sellNotBelowBuy"));
  }

  const loadVersions = (strategy: string) => {
    setVersionsFailed(false);
    Promise.resolve()
      .then(() => listStrategyVersions(strategy))
      .then(
        (v) => { if (mountedRef.current) setVersions(v.versionen); },
        () => { if (mountedRef.current) setVersionsFailed(true); },
      );
  };

  const save = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setSaving(true);
    setSaveError(null);
    const nonce = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    try {
      const result = await saveStrategy(trimmed, values, SAVE_ACK, nonce);
      if (mountedRef.current) {
        setSavedName(result.name);
        setName(result.name);
        setVerworfen(result.liste_verworfen ?? null);
        // The engine's success sentence for an unchanged save (`routes/steuerung.py`); shown
        // from the catalogue, compared on the engine's bytes.
        setSaveAck(
          t(
            result.detail === "no change — nothing recorded"
              ? "console.strategyWorkshop.extra.noChange"
              : "console.strategyWorkshop.T09",
          ),
        );
      }
      onSaved(result);
      loadVersions(result.name);
    } catch (e: unknown) {
      if (mountedRef.current) {
        setSaveError(workshopErrorText(t, e, "console.strategyWorkshop.extra.saveFailed"));
      }
    } finally {
      if (mountedRef.current) setSaving(false);
    }
  };

  const canSave = load === "ready" && name.trim() !== "" && Object.keys(violations).length === 0 && !saving;

  return (
    <div className="surface-flat mt-4 px-4 py-4" data-testid="strategy-editor">
      <div className="flex items-end gap-3 flex-wrap">
        {!editing && (
          <label className="flex flex-col gap-1.5 text-[12px] text-white/70">
            {t("console.strategyWorkshop.deviation.source")}
            <select
              value={sourceName}
              onChange={(e) => chooseSource(e.target.value)}
              disabled={load !== "ready"}
              className="bg-transparent border border-white/12 rounded-lg px-3 py-2 text-[13px] text-white/90"
            >
              <option value="">{t("console.strategyWorkshop.T03")}</option>
              {strategies.length > 0 && (
                <optgroup label={t("console.strategyWorkshop.T04")}>
                  {strategies.map((s) => (
                    <option key={s.name} value={s.name}>{s.name}</option>
                  ))}
                </optgroup>
              )}
            </select>
          </label>
        )}
        <label className="flex flex-col gap-1.5 text-[12px] text-white/70">
          {t("console.strategyWorkshop.T05")}
          <input
            type="text"
            value={name}
            readOnly={editing}
            maxLength={NAME_MAXLEN}
            onChange={(e) => setName(e.target.value)}
            className="bg-transparent border border-white/12 rounded-lg px-3 py-2 text-[13px] text-white/90"
          />
        </label>
      </div>

      {load === "loading" && <div className="mt-3"><WorkshopState kind="loading" /></div>}
      {load === "error" && <div className="mt-3"><WorkshopState kind="error" /></div>}

      {load === "ready" && settings && (
        <>
          {ADVANCED_GROUPS.map((g) => (
            <div key={g.id} className="surface-flat mt-3 px-4 pt-3 pb-1">
              <div className="sublabel">{g.title}</div>
              {g.fields.filter(isVisible).map((f) => (
                <Fragment key={f.key}>
                  <AdvancedFieldRow
                    field={withSystemCaption(f)}
                    value={val(f.key)}
                    dirty={f.key in defaults && !numEq(val(f.key), defaults[f.key])}
                    lockHint={inertHint(f, val)}
                    choices={registryChoices[f.key]}
                    decimals={registryDecimals[f.key]}
                    defaultValue={defaults[f.key]}
                    errors={violations[f.key]}
                    onChange={(next) => setValues((prev) => ({ ...prev, [f.key]: next }))}
                  />
                  {f.key === ANBIETER_KEY && (istLokal || istCloud) && (
                    <p role="status" className="pb-3 text-[11.5px] text-white/70 leading-relaxed">
                      {t(istLokal ? "console.strategyWorkshop.prompt.localModelHint" : "console.strategyWorkshop.prompt.cloudCostHint")}
                    </p>
                  )}
                </Fragment>
              ))}
            </div>
          ))}

          <div data-testid="fixed-limits" className="surface-flat mt-3 px-4 pt-3 pb-1">
            <div className="sublabel">{t("console.strategyWorkshop.T07")}</div>
            {FIXED_LIMITS.map((l) => (
              <div key={l.key} data-fixed={l.key} className="flex items-baseline justify-between gap-4 py-3 border-t border-white/6">
                <div className="max-w-sm">
                  <div className="text-[13px] font-semibold text-white/90">{t(l.label)}</div>
                  <div data-copy="description" className="text-[11.5px] text-white/45 leading-relaxed mt-0.5">{t(l.desc)}</div>
                </div>
                {l.key === "HARD_STOP_LOSS_PCT" && (
                  <span className="num text-[12px] text-white/60">{settings.guardrails?.HARD_STOP_LOSS_PCT ?? "-8.0"}</span>
                )}
              </div>
            ))}
          </div>

          {children}

          {positions === null && (
            <p data-testid="slot-check-skipped" className="text-[11.5px] text-white/45 mt-3 leading-relaxed">
              {t("console.strategyWorkshop.extra.slotCheckSkipped")}
            </p>
          )}
        </>
      )}

      {saveError &&<p className="surface-flat mt-3 px-4 py-3 text-[13px] text-white/70" role="alert">{saveError}</p>}
      {savedName && saveAck && <p className="surface-flat mt-3 px-4 py-3 text-[13px] text-white/70" role="status">{saveAck}</p>}
      {savedName && verworfen && Object.keys(verworfen).length > 0 && (
        // #4660: per discarded symbol of the own list, the engine's check code as text.
        <ul data-testid="liste-verworfen" role="status" className="surface-flat mt-3 px-4 py-3">
          {Object.entries(verworfen).map(([symbol, code]) => (
            <li key={symbol} className="flex items-baseline justify-between gap-4 py-1 text-[12px] text-white/70">
              <span className="num">{symbol}</span>
              <span>{grundText(code)}</span>
            </li>
          ))}
        </ul>
      )}

      {savedName && (versions || versionsFailed) && (
        <div className="surface-flat mt-3 px-4 py-3">
          <div className="sublabel">{t("console.strategyWorkshop.deviation.versions")}</div>
          {versionsFailed && (
            <p className="text-[13px] text-white/70 mt-2" role="status">
              {t("console.strategyWorkshop.state.error.text")}
            </p>
          )}
          {versions && (
            <ul data-testid="strategy-versions" className="mt-2">
              {versions.map((v) => (
                <li key={`${v.hash}-${v.zeitpunkt}`} className="flex items-baseline justify-between gap-4 border-t border-white/6 py-1.5">
                  <span className="num text-[12px] text-white/70">{v.hash_kuerzel}</span>
                  <span className="num text-[12px] text-white/60">{v.zeitpunkt}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="flex items-center justify-end gap-3 pt-4 border-t border-white/8 mt-3">
        <button type="button" className="btn" onClick={onCancel}>
          {t(savedName ? "console.strategyWorkshop.deviation.close" : "console.strategyWorkshop.extra.cancel")}
        </button>
        <button type="button" className="btn btn-primary" disabled={!canSave} onClick={() => void save()}>
          {t("console.strategyWorkshop.T06")}
        </button>
      </div>
    </div>
  );
}
