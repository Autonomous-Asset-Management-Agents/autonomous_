import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { listStrategies, listStrategyAssignments } from "@/lib/api";
import { getEngineFleet, hasPaperSlots, type FleetEngine } from "@/lib/desktopBridge";
import { WorkshopState } from "./WorkshopStates";
import { loadFailure, type LoadFailure } from "./errorText";
import { accountMetrics, commonWindow, type AccountFigures } from "./compareAccounts";
import { useAccountComparison, type ComparedAccount } from "./useAccountComparison";

// Texts come from `console.strategyWorkshop` (#4892).

type SortKey = "twrPct" | "maxDrawdownPct" | "spyPct";
type Sort = { key: SortKey; dir: "ascending" | "descending" } | null;
const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "twrPct", label: "console.strategyWorkshop.extra.twr" },
  { key: "maxDrawdownPct", label: "console.strategyWorkshop.extra.maxDrawdown" },
  { key: "spyPct", label: "console.strategyWorkshop.extra.spySamePeriod" },
];

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const s = Math.abs(v).toFixed(2);
  return `${s === "0.00" ? "" : v > 0 ? "+" : "−"}${s} %`;
}
const ymd = (d: Date) => d.toISOString().slice(0, 10);
type Gate = "loading" | LoadFailure | "ready";

const slotKey = (a: Pick<ComparedAccount, "kind" | "id" | "port">) => `${a.kind}:${a.id}:${a.port}`;

export function AccountComparison({ onAdjust }: { onAdjust?: (account: ComparedAccount) => void } = {}) {
  const { t } = useTranslation();
  const [gate, setGate] = useState<Gate>("loading");
  const [attempt, setAttempt] = useState(0);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.resolve().then(() => listStrategies()).then(
      () => alive && setGate("ready"),
      (e: unknown) => { if (alive) setGate(loadFailure(e)); },
    );
    return () => { alive = false; };
  }, [attempt]);

  if (gate === "hidden" || gate === "loading") return null;

  // #5166: the subtitle says these are the accounts' real results, unlike "Simulate" above.
  const sentence = (
    <p className="text-[13px] text-white/70 leading-snug mt-1">{t("console.strategyWorkshop.strategies.compareSentence")}</p>
  );
  return (
    <div className="surface p-6">
      {gate === "error" || gate === "engine-offline" ? (
        <>
          <div className="eyebrow mb-1">{t("console.strategyWorkshop.T34")}</div>
          {sentence}
          <div className="mt-3">
            <WorkshopState
              kind={gate}
              action={t("console.strategyWorkshop.state.error.action")}
              onAction={() => { setGate("loading"); setAttempt((n) => n + 1); }}
            />
          </div>
        </>
      ) : (
        <>
          <button type="button" className="btn-link eyebrow" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {t("console.strategyWorkshop.T34")}
          </button>
          {sentence}
          {open && <ComparisonBody onAdjust={onAdjust} />}
        </>
      )}
    </div>
  );
}

function ComparisonBody({ onAdjust }: { onAdjust?: (account: ComparedAccount) => void }) {
  const { t } = useTranslation();
  const [fleet, setFleet] = useState<FleetEngine[] | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    void getEngineFleet().then((f) => alive && setFleet(f));
    return () => { alive = false; };
  }, []);

  // #4953: account key → assigned strategy. Fail-soft: unreadable leaves "—", the figures stay.
  const [assigned, setAssigned] = useState<Record<string, string>>({});
  useEffect(() => {
    let alive = true;
    Promise.resolve().then(() => listStrategyAssignments()).then(
      (list) => { if (alive) setAssigned(Object.fromEntries(list.map((a) => [a.konto, a.strategie]))); },
      () => {},
    );
    return () => { alive = false; };
  }, []);

  const hasPaper = !!fleet && fleet.some((e) => e.kind === "paper");
  const accounts = useAccountComparison(hasPaper ? fleet! : null);
  const [sort, setSort] = useState<Sort>(null);

  const { win, figures } = useMemo(() => {
    const ready = accounts.filter((a) => a.state === "ok" && a.view);
    const w = ready.length > 0 ? commonWindow(ready.map((a) => a.view!)) : null;
    const f = new Map<string, AccountFigures | null>();
    if (w) for (const a of ready) f.set(slotKey(a), accountMetrics(a.view!, w));
    return { win: w, figures: f };
  }, [accounts]);

  const rows = useMemo(() => {
    if (!sort) return accounts;
    const val = (a: ComparedAccount) => figures.get(slotKey(a))?.[sort.key];
    const sign = sort.dir === "ascending" ? 1 : -1;
    return [...accounts].sort((a, b) => {
      const va = val(a), vb = val(b);
      if (va == null || vb == null) return (va == null ? 1 : 0) - (vb == null ? 1 : 0);
      return (va - vb) * sign;
    });
  }, [accounts, figures, sort]);

  const loading = t("console.strategyWorkshop.state.loading.text");
  if (fleet === undefined) return <div className="mt-3"><WorkshopState kind="loading" /></div>;
  if (!hasPaper) {
    // #4934 (A6-P4): the action leads to "Paper accounts" (PaperAccountSlots), which renders
    // only when the shell offers the slots (STW-6b) — elsewhere it would have no target.
    return (
      <div className="mt-3">
        <WorkshopState
          kind="empty"
          text={t("console.strategyWorkshop.state.empty.text.6")}
          action={hasPaperSlots() ? t("console.strategyWorkshop.state.empty.action.6") : undefined}
          onAction={() => document.getElementById("paper-accounts")?.scrollIntoView({ behavior: "smooth", block: "start" })}
        />
      </div>
    );
  }

  const anyLoading = accounts.some((a) => a.state === "loading");
  const clickSort = (key: SortKey) =>
    setSort((s) => s?.key === key && s.dir === "ascending" ? { key, dir: "descending" } : { key, dir: "ascending" });

  return (
    <div className="mt-3">
      <div className="surface-flat px-4 py-3 text-[13px] text-white/70 space-y-1">
        <p className="font-medium text-white/90">{t("console.strategyWorkshop.T27")}</p>
        {win ? (
          <p className="num">{`${t("console.strategyWorkshop.extra.period")} ${ymd(win.from)} – ${ymd(win.to)}`}</p>
        ) : (
          <p role="status">{anyLoading ? loading : t("console.strategyWorkshop.deviation.noCommonPeriod")}</p>
        )}
        <p>{t("console.strategyWorkshop.T35")}</p>
      </div>

      <table className="w-full mt-3 text-[13px] text-white/70">
        <thead>
          <tr className="text-left text-white/55">
            <th scope="col" className="py-2 font-normal">{t("console.strategyWorkshop.extra.account")}</th>
            <th scope="col" className="py-2 font-normal">{t("console.strategyWorkshop.extra.strategy")}</th>
            {COLUMNS.map((c) => (
              <th key={c.key} scope="col" className="py-2 font-normal text-right" aria-sort={sort?.key === c.key ? sort.dir : "none"}>
                <button type="button" className="btn-link" onClick={() => clickSort(c.key)}>{t(c.label)}</button>
              </th>
            ))}
            <th scope="col" className="py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((a) => {
            const f = figures.get(slotKey(a));
            return (
              <tr key={slotKey(a)} className="border-t border-white/8">
                <td className="py-2">{a.label}</td>
                <td className="py-2">{assigned[a.kind === "live" ? "live" : a.id] ?? "—"}</td>
                {a.state === "loading" ? (
                  <td className="py-2" colSpan={3} role="status">{loading}</td>
                ) : a.state === "unavailable" ? (
                  <td className="py-2" colSpan={3}>{t("console.strategyWorkshop.T37")}</td>
                ) : (
                  COLUMNS.map((c) => <td key={c.key} className="py-2 text-right num">{win ? fmtPct(f?.[c.key]) : "—"}</td>)
                )}
                <td className="py-2 text-right">
                  {a.kind === "paper" && onAdjust && (
                    <button type="button" className="btn-link" onClick={() => onAdjust(a)}>{t("console.strategyWorkshop.T36")}</button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="mt-2">
        <button type="button" className="btn-link" onClick={() => setSort(null)}>{t("console.strategyWorkshop.T30")}</button>
      </div>
    </div>
  );
}
