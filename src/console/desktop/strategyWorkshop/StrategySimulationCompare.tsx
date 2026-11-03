import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getStrategySimulation,
  listStrategies,
  startStrategySimulation,
  type NamedStrategy,
  type StrategySimNotModelled,
  type StrategySimRow,
  type StrategySimRun,
} from "@/lib/api";
import { hasPaperSlots, startEngine } from "@/lib/desktopBridge";
import { WorkshopState } from "./WorkshopStates";

/**
 * "Simulate" (STW-9c #4918; UX-Konzept #4628, Abläufe 3 und 5, Zustände).
 *
 * Up to three named strategies over one period within the last three years, run by the engine
 * (`POST /api/strategy-sim/runs`, then polled, #4917). The result is a neutral table — TWR, max
 * drawdown, SPY over the same period — in send order, sorted only on a header click (ADR-027
 * Regel 2). The mandatory notes stand above the figures and do not collapse (Regel 4). No
 * control adopts or assigns a strategy (Regel 3). Flag off (`GET /api/strategies` → 404):
 * nothing renders. Texts come from `console.strategyWorkshop` (#4892).
 */

const MAX_SELECTED = 3, YEARS = 3, EDITION_MONTHS = 12, POLL_MS = 1_500;

type SortKey = "twr" | "dd" | "spy";
type Sort = { key: SortKey; dir: "ascending" | "descending" } | null;
const COLUMNS: { key: SortKey; label: string; value: (r: StrategySimRow) => number | null }[] = [
  { key: "twr", label: "console.strategyWorkshop.extra.twr", value: (r) => r.twr_pct },
  // Shown as a loss, like the live/paper comparison (STW-7).
  { key: "dd", label: "console.strategyWorkshop.extra.maxDrawdown", value: (r) => -r.max_drawdown_pct },
  { key: "spy", label: "console.strategyWorkshop.extra.spySamePeriod", value: (r) => r.spy_twr_pct },
];

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const s = Math.abs(v).toFixed(2);
  return `${s === "0.00" ? "" : v > 0 ? "+" : "−"}${s} %`;
}

const ymd = (d: Date) => d.toISOString().slice(0, 10);
function minusMonths(iso: string, months: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const total = y * 12 + (m - 1) - months;
  const year = Math.floor(total / 12), month = total % 12;
  const last = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return ymd(new Date(Date.UTC(year, month, Math.min(d, last))));
}

type Translate = (key: string) => string;
function notModelledText(t: Translate, n: StrategySimNotModelled): string | null {
  switch (n.art) {
    case "survivorship":
      return t("console.strategyWorkshop.deviation.notModelled.survivorship");
    case "quelle":
      return t("console.strategyWorkshop.deviation.notModelled.quelle");
    case "fehlende_titel": {
      // `vergleich.py` writes "<German lead>: <names>" or "…: keine".
      const names = (n.text ?? "").split(": ")[1];
      return names && names !== "keine" ? `${t("console.strategyWorkshop.deviation.notModelled.missingPrices")} ${names}` : null;
    }
    case "einstellungen":
      return `${n.strategie}: ${t("console.strategyWorkshop.deviation.notModelled.settings")} — ${(n.keys ?? []).join(", ")}`;
    case "agenten":
      return `${n.strategie}: ${t("console.strategyWorkshop.deviation.notModelled.agents")} — ${(n.agenten ?? []).join(", ")}`;
    case "ohne_lstm":
      return t("console.strategyWorkshop.deviation.notModelled.noLstm");
    case "ohne_fundamentaldaten":
      return `${t("console.strategyWorkshop.deviation.notModelled.missingFundamentals")} ${(n.titel ?? []).join(", ")}`;
    default:
      return n.text ?? n.art;
  }
}

type Failure = "error" | "no-keys" | "no-data" | "engine-off" | "edition-limit";
type Phase = { kind: "setup" } | { kind: "loading" } | { kind: "result"; run: StrategySimRun } | { kind: Failure };

// Duck-typed like AccountComparison (STW-7): an HttpError carries `status`, a failed fetch does not.
const httpStatus = (e: unknown): number | null =>
  typeof e === "object" && e !== null && typeof (e as { status?: unknown }).status === "number"
    ? (e as { status: number }).status
    : null;

/** Engine answer → state (#4917/#4890: 403 `simulation_edition_limit`, 409 `params.reason`). */
function failureOf(e: unknown): Failure {
  const status = httpStatus(e);
  if (status === null) return "engine-off";
  if (status === 403) return "edition-limit";
  if (status === 409) {
    const reason = (e as { params?: { reason?: unknown } }).params?.reason;
    if (reason === "keine_keys") return "no-keys";
    if (reason === "kein_korpus") return "no-data";
  }
  return "error";
}

type Gate = "loading" | "hidden" | "error" | "ready";

export function StrategySimulationCompare() {
  const { t } = useTranslation();
  const [gate, setGate] = useState<Gate>("loading");
  const [strategies, setStrategies] = useState<NamedStrategy[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.resolve().then(() => listStrategies()).then(
      (list) => { if (alive) { setStrategies(list.strategien); setGate("ready"); } },
      (e: unknown) => { if (alive) setGate(httpStatus(e) === 404 ? "hidden" : "error"); },
    );
    return () => { alive = false; };
  }, [attempt]);

  if (gate === "hidden" || gate === "loading") return null;

  return (
    <div className="surface p-6">
      {gate === "error" ? (
        <>
          <div className="eyebrow mb-1">{t("console.strategyWorkshop.T16")}</div>
          <WorkshopState
            kind="error"
            action={t("console.strategyWorkshop.state.error.action")}
            onAction={() => { setGate("loading"); setAttempt((n) => n + 1); }}
          />
        </>
      ) : (
        <>
          <button type="button" className="btn-link eyebrow" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {t("console.strategyWorkshop.T16")}
          </button>
          {open && <SimulationBody strategies={strategies} />}
        </>
      )}
    </div>
  );
}

function SimulationBody({ strategies }: { strategies: NamedStrategy[] }) {
  const { t } = useTranslation();
  const today = ymd(new Date());
  const earliest = minusMonths(today, YEARS * 12);
  const [end, setEnd] = useState(today);
  const [start, setStart] = useState(minusMonths(today, EDITION_MONTHS));
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [capital, setCapital] = useState("100000");
  const [phase, setPhase] = useState<Phase>({ kind: "setup" });
  const alive = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; clearTimeout(timer.current); };
  }, []);

  const capitalValue = Number(capital);
  const periodValid = !!start && !!end && start >= earliest && end <= today && start < end;
  const canRun = selected.size > 0 && periodValid && capitalValue > 0;

  const toggle = (name: string) => setSelected((s) => {
    const next = new Set(s);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
  });

  const poll = async (runId: string) => {
    try {
      const run = await getStrategySimulation(runId);
      if (!alive.current) return;
      if (run.status === "fertig") setPhase({ kind: "result", run });
      else if (run.status === "fehler") setPhase({ kind: "error" });
      else timer.current = setTimeout(() => void poll(runId), POLL_MS);
    } catch (e) {
      if (alive.current) setPhase({ kind: failureOf(e) });
    }
  };

  const submit = async () => {
    setPhase({ kind: "loading" });
    try {
      const { run_id } = await startStrategySimulation({
        beginn: start,
        ende: end,
        // Creation order (`GET /api/strategies`, #4863), whatever order the boxes were ticked in (Regel 2).
        strategien: strategies.map((s) => s.name).filter((n) => selected.has(n)),
        startkapital: capitalValue,
      });
      if (alive.current) await poll(run_id);
    } catch (e) {
      if (alive.current) setPhase({ kind: failureOf(e) });
    }
  };

  if (phase.kind === "result") {
    return <SimulationResult run={phase.run} onChangeSetup={() => setPhase({ kind: "setup" })} />;
  }
  if (strategies.length === 0) {
    return <div className="mt-3"><WorkshopState kind="empty" text={t("console.strategyWorkshop.state.empty.text.1")} /></div>;
  }

  return (
    <div className="mt-3 space-y-3">
      <div className="surface-flat px-4 py-3 text-[13px] text-white/70 space-y-3">
        <fieldset>
          <legend className="sublabel mb-1">{t("console.strategyWorkshop.T17")}</legend>
          <div className="flex gap-3">
            <label className="flex flex-col gap-1">
              {t("console.strategyWorkshop.deviation.start")}
              <input type="date" className="num" value={start} min={earliest} max={today} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1">
              {t("console.strategyWorkshop.deviation.end")}
              <input type="date" className="num" value={end} min={earliest} max={today} onChange={(e) => setEnd(e.target.value)} />
            </label>
          </div>
        </fieldset>
        <fieldset>
          <legend className="sublabel mb-1">{t("console.strategyWorkshop.deviation.strategies")}</legend>
          {strategies.map((s) => (
            <label key={s.name} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={selected.has(s.name)}
                disabled={!selected.has(s.name) && selected.size >= MAX_SELECTED}
                onChange={() => toggle(s.name)}
              />
              {s.name}
            </label>
          ))}
        </fieldset>
        <label className="flex flex-col gap-1">
          {t("console.strategyWorkshop.T18")}
          <input type="number" className="num" min={1} value={capital} onChange={(e) => setCapital(e.target.value)} />
        </label>
        <button type="button" className="btn btn-primary" disabled={!canRun || phase.kind === "loading"} onClick={() => void submit()}>
          {t("console.strategyWorkshop.T19")}
        </button>
      </div>

      {phase.kind === "loading" && <WorkshopState kind="loading" />}
      {phase.kind === "error" && (
        <WorkshopState kind="error" action={t("console.strategyWorkshop.state.error.action")} onAction={() => void submit()} />
      )}
      {phase.kind === "no-keys" && (
        // #4636 (A3-P5): like the empty state of AccountComparison — the action leads to "Paper
        // accounts", which renders only when the shell offers the slots.
        <WorkshopState
          kind="no-keys"
          action={hasPaperSlots() ? t("console.strategyWorkshop.state.no-keys.action") : undefined}
          onAction={() => document.getElementById("paper-accounts")?.scrollIntoView({ behavior: "smooth", block: "start" })}
        />
      )}
      {phase.kind === "no-data" && <WorkshopState kind="no-data" text={t("console.strategyWorkshop.T21")} />}
      {phase.kind === "engine-off" && (
        <WorkshopState
          kind="engine-off"
          action={t("console.strategyWorkshop.state.engine-off.action")}
          onAction={() => { void startEngine().catch(() => {}); setPhase({ kind: "setup" }); }}
        />
      )}
      {phase.kind === "edition-limit" && (
        <WorkshopState
          kind="edition-limit"
          action={t("console.strategyWorkshop.state.edition-limit.action")}
          onAction={() => { setStart(minusMonths(end, EDITION_MONTHS)); setPhase({ kind: "setup" }); }}
        />
      )}
    </div>
  );
}

function SimulationResult({ run, onChangeSetup }: { run: StrategySimRun; onChangeSetup: () => void }) {
  const { t } = useTranslation();
  const e = run.ergebnis!;
  const [sort, setSort] = useState<Sort>(null);

  const rows = useMemo(() => {
    if (!sort) return e.strategien;
    const col = COLUMNS.find((c) => c.key === sort.key)!;
    const sign = sort.dir === "ascending" ? 1 : -1;
    return [...e.strategien].sort((a, b) => {
      const va = col.value(a), vb = col.value(b);
      if (va == null || vb == null) return (va == null ? 1 : 0) - (vb == null ? 1 : 0);
      return (va - vb) * sign;
    });
  }, [e.strategien, sort]);

  const clickSort = (key: SortKey) =>
    setSort((s) => s?.key === key && s.dir === "ascending" ? { key, dir: "descending" } : { key, dir: "ascending" });
  const notModelled = e.nicht_abgebildet.map((n) => notModelledText(t, n)).filter((x): x is string => !!x);

  return (
    <div className="mt-3">
      <div className="sublabel mb-1">{t("console.strategyWorkshop.T26")}</div>
      <div className="surface-flat px-4 py-3 text-[13px] text-white/70 space-y-1">
        <p className="font-medium text-white/90">{t("console.strategyWorkshop.T27")}</p>
        <p className="num">{`${t("console.strategyWorkshop.extra.period")} ${e.zeitraum.beginn} – ${e.zeitraum.ende}`}</p>
        <p>{t("console.strategyWorkshop.T28")}</p>
        <p>{t("console.strategyWorkshop.T29")}</p>
        {notModelled.length > 0 && (
          <ul className="list-disc pl-5">
            {notModelled.map((x) => <li key={x}>{x}</li>)}
          </ul>
        )}
      </div>

      <table className="w-full mt-3 text-[13px] text-white/70">
        <thead>
          <tr className="text-left text-white/55">
            <th scope="col" className="py-2 font-normal">{t("console.strategyWorkshop.extra.strategy")}</th>
            {COLUMNS.map((c) => (
              <th key={c.key} scope="col" className="py-2 font-normal text-right" aria-sort={sort?.key === c.key ? sort.dir : "none"}>
                <button type="button" className="btn-link" onClick={() => clickSort(c.key)}>{t(c.label)}</button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} className="border-t border-white/8">
              <td className="py-2">{r.name}</td>
              {COLUMNS.map((c) => <td key={c.key} className="py-2 text-right num">{fmtPct(c.value(r))}</td>)}
            </tr>
          ))}
        </tbody>
      </table>

      <div className="mt-2 flex gap-4">
        <button type="button" className="btn-link" onClick={() => setSort(null)}>{t("console.strategyWorkshop.T30")}</button>
        <button type="button" className="btn-link" onClick={onChangeSetup}>{t("console.strategyWorkshop.T33")}</button>
      </div>
      <p className="mt-2 text-[13px] text-white/55">{t("console.strategyWorkshop.T32")}</p>
    </div>
  );
}
