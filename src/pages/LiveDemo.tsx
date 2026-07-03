import { useMemo, useState } from "react";
import "@/console/console.css";
import {
  useSnapshotPolling,
  type DemoEquityPoint,
  type DemoSnapshot,
} from "@/console/live/useSnapshotPolling";
import { SiteHeader } from "@/components/SiteHeader";
import { SiteMarquee } from "@/components/SiteMarquee";
import { EquityChart } from "@/console/shared/EquityChart";
import { Sparkline } from "@/console/shared/Sparkline";
import { ConvictionMeter } from "@/console/shared/RoundTableView";
import { fmtEUR, fmtPct } from "@/console/lib/format";
import {
  filterCurveByRange,
  EQUITY_RANGES,
  type EquityRange,
} from "@/console/live/equityRange";

// Snapshot curve ({date,equity,benchmark}) -> EquityChart / filterCurveByRange shape ({t:Date,eur}).
type CurvePt = { t: Date; eur: number };
const toEquitySeries = (curve: DemoEquityPoint[]): CurvePt[] =>
  curve.map((p) => ({ t: new Date(p.date), eur: p.equity }));
// benchmark is nullable per point — drop nulls (never fabricate); <2 points => EquityChart omits the overlay.
const toBenchSeries = (curve: DemoEquityPoint[]): CurvePt[] =>
  curve.filter((p) => p.benchmark != null).map((p) => ({ t: new Date(p.date), eur: p.benchmark as number }));

const RANGE_ACTIVE = "border-transparent text-[#00c27a] bg-[#00c27a]/12";
const RANGE_IDLE = "border-white/5 text-white/55 hover:text-white/92 hover:border-white/15";

// The Console Overview dashboard, re-skinned for the public demo — same shared components,
// same .aaa-console theme, fed by the snapshot instead of the live engine.
function DashboardBody({
  snapshot: s,
  paused,
  stamp,
  range,
  setRange,
  percent,
  setPercent,
  view,
  benchView,
}: {
  snapshot: DemoSnapshot;
  paused: boolean;
  stamp: string;
  range: EquityRange;
  setRange: (r: EquityRange) => void;
  percent: boolean;
  setPercent: (p: boolean) => void;
  view: CurvePt[];
  benchView: CurvePt[];
}) {
  const startEquity = s.equity_curve[0]?.equity ?? null;
  const totalReturnPct =
    startEquity != null && startEquity !== 0 ? ((s.equity - startEquity) / startEquity) * 100 : null;
  // intraday P/L in EUR reverse-derived from day_pl_pct + current equity (exact, not fabricated).
  const prevEquity = s.equity / (1 + s.day_pl_pct / 100);
  const dayPL = s.equity - prevEquity;
  const benchPts = s.equity_curve.filter((p) => p.benchmark != null);
  const benchToday =
    benchPts.length >= 2
      ? (() => {
          const prev = benchPts[benchPts.length - 2].benchmark as number;
          const cur = benchPts[benchPts.length - 1].benchmark as number;
          return prev !== 0 ? ((cur - prev) / prev) * 100 : null;
        })()
      : null;
  const exposurePct = s.equity
    ? (s.positions.reduce((a, p) => a + p.market_value, 0) / s.equity) * 100
    : null;

  return (
    <main className="aaa-console">
      <div className="mx-auto max-w-5xl px-6 sm:px-8 py-7 space-y-7">
        {/* Disclaimer */}
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.06] px-3 py-2 text-xs text-amber-300/90">
          ⚠ {s.disclaimer}
        </div>

        {/* Hero */}
        <div className="flex items-end justify-between">
          <div>
            <div className="eyebrow mb-2">Portfolio · Paper Trading</div>
            <h1 className="text-[40px] sm:text-[54px] font-bold tracking-tight3 leading-[1.02] text-white/92">
              Live-Demo
            </h1>
            <p className="text-white/55 mt-2 text-[13px]">
              Der Round Table hat {s.decisions.length}{" "}
              {s.decisions.length === 1 ? "Entscheidung" : "Entscheidungen"} getroffen.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="pill">Paper-Trading</span>
            <span className={`pill ${paused ? "pill-warn" : "pill-bull"}`}>{paused ? "pausiert" : "live"}</span>
          </div>
        </div>

        {/* Total equity surface */}
        <div className="surface halo-soft p-7">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="eyebrow mb-2">Gesamt-Equity</div>
              <div className="num text-[48px] sm:text-[56px] font-bold tracking-tight3 leading-none text-white/92">
                {fmtEUR(s.equity)}
              </div>
              <div className="mt-2 flex items-center gap-3 text-[13px]">
                <span className={`num ${dayPL >= 0 ? "text-bull" : "text-bear"}`}>
                  {fmtEUR(dayPL, { sign: true })} <span className="text-white/40">({fmtPct(s.day_pl_pct)} heute)</span>
                </span>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 text-[12px] text-white/55">
                <span>
                  Seit Start{" "}
                  <span className={`num ${totalReturnPct != null && totalReturnPct >= 0 ? "text-bull" : "text-bear"}`}>
                    {totalReturnPct != null ? fmtPct(totalReturnPct) : "—"}
                  </span>
                </span>
                <span className="hairline-v" />
                <span>
                  S&amp;P 500 heute{" "}
                  <span className="num text-white/80">{benchToday != null ? fmtPct(benchToday) : "—"}</span>
                </span>
                <span className="hairline-v" />
                <span>
                  Start <span className="num text-white/80">{startEquity != null ? fmtEUR(startEquity, { compact: true }) : "—"}</span>
                </span>
              </div>
            </div>
            <div className="flex flex-col items-end gap-2">
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setPercent(false)}
                  className={`rounded-md border px-2.5 py-1 text-[11px] transition-colors ${!percent ? RANGE_ACTIVE : RANGE_IDLE}`}
                >
                  €
                </button>
                <button
                  onClick={() => setPercent(true)}
                  className={`rounded-md border px-2.5 py-1 text-[11px] transition-colors ${percent ? RANGE_ACTIVE : RANGE_IDLE}`}
                >
                  %
                </button>
              </div>
              <div className="flex items-center gap-1">
                {EQUITY_RANGES.map((r) => (
                  <button
                    key={r}
                    onClick={() => setRange(r)}
                    className={`rounded-md border px-2 py-1 text-[11px] transition-colors ${r === range ? RANGE_ACTIVE : RANGE_IDLE}`}
                  >
                    {r}
                  </button>
                ))}
              </div>
              <div className="num text-[10px] text-white/30">Stand {stamp}</div>
            </div>
          </div>

          <div className="mt-5 equity-grid rounded-xl">
            <EquityChart data={view} benchmark={benchView} percent={percent} height={240} glowColor="#00c27a" />
          </div>

          <div className="mt-3 flex items-center gap-5 text-[11px] text-white/45">
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-4 rounded" style={{ background: "#00c27a" }} /> autonomous_
            </span>
            {benchView.length >= 2 && (
              <span className="inline-flex items-center gap-1.5">
                <span className="inline-block h-0.5 w-4 rounded border-t border-dashed border-white/40" /> S&amp;P 500
              </span>
            )}
          </div>
        </div>

        {/* KPI cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="surface-flat p-4">
            <div className="eyebrow mb-1.5">Tages-P/L</div>
            <div className={`num text-[22px] font-semibold tracking-tight2 ${dayPL >= 0 ? "text-bull" : "text-bear"}`}>
              {fmtEUR(dayPL, { sign: true })}
            </div>
            <div className="text-[11px] text-white/40 mt-0.5">{fmtPct(s.day_pl_pct)} heute</div>
          </div>
          <div className="surface-flat p-4">
            <div className="eyebrow mb-1.5">Rendite seit Start</div>
            <div
              className={`num text-[22px] font-semibold tracking-tight2 ${totalReturnPct != null && totalReturnPct >= 0 ? "text-bull" : "text-bear"}`}
            >
              {totalReturnPct != null ? fmtPct(totalReturnPct) : "—"}
            </div>
            <div className="text-[11px] text-white/40 mt-0.5">seit erstem Kurspunkt</div>
          </div>
          <div className="surface-flat p-4">
            <div className="eyebrow mb-1.5">Cash</div>
            <div className="num text-[22px] font-semibold tracking-tight2 text-white/92">
              {fmtEUR(s.cash, { compact: true })}
            </div>
            <div className="text-[11px] text-white/40 mt-0.5">
              {exposurePct != null ? `${exposurePct.toFixed(0)}% investiert` : "settled cash"}
            </div>
          </div>
          <div className="surface-flat p-4">
            <div className="eyebrow mb-1.5">Positionen</div>
            <div className="num text-[22px] font-semibold tracking-tight2 text-white/92">{s.positions.length}</div>
            <div className="text-[11px] text-white/40 mt-0.5">offen</div>
          </div>
        </div>

        {/* Decisions + Positions */}
        <div className="grid grid-cols-1 lg:grid-cols-[1.15fr_1fr] gap-5">
          {/* Decision queue */}
          <div className="surface p-5">
            <div className="eyebrow mb-3">Round Table · Entscheidungen</div>
            <div className="space-y-2.5">
              {s.decisions.map((d) => {
                const act = d.action.toUpperCase();
                const pillCls = act === "BUY" ? "pill-bull" : act === "SELL" ? "pill-bear" : "pill-warn";
                return (
                  <div key={d.symbol} className="p-3 rounded-lg bg-white/[0.025] border border-white/5">
                    <div className="flex items-center gap-3 mb-2">
                      <span className="font-bold text-[13px] tracking-tight2 text-white/92">{d.symbol}</span>
                      <span className={`pill ${pillCls}`}>{act}</span>
                      <span className="ml-auto pill pill-strong">Konsens {(d.consensus * 100).toFixed(0)}%</span>
                    </div>
                    {d.conviction != null && (
                      <div className="w-full my-2">
                        <ConvictionMeter score={d.conviction * 2 - 1} />
                      </div>
                    )}
                    <p className="mt-1 text-[12px] text-white/55">„{d.summary}“</p>
                  </div>
                );
              })}
              {s.decisions.length === 0 && (
                <div className="text-[12px] text-white/30 p-4 text-center">Keine Entscheidungen im aktuellen Snapshot.</div>
              )}
            </div>
          </div>

          {/* Top positions */}
          <div className="surface p-5">
            <div className="flex items-center justify-between mb-3">
              <div className="eyebrow">Positionen</div>
              <span className="num text-[11px] text-white/40">{s.positions.length} offen</span>
            </div>
            <div>
              {s.positions.slice(0, 6).map((p) => {
                const base = p.market_value / (1 + p.unrealized_pl_pct / 100); // cost basis -> current
                return (
                  <div key={p.symbol} className="flex items-center gap-3 py-2.5 border-b border-white/5 last:border-0">
                    <div className="w-12 font-bold text-[13px] tracking-tight2 text-white/92">{p.symbol}</div>
                    <Sparkline data={[base, p.market_value]} width={56} height={20} />
                    <div className="num text-[12px] text-white/92 ml-auto">{fmtEUR(p.market_value, { compact: true })}</div>
                    <div className={`num text-[11px] w-16 text-right ${p.unrealized_pl_pct >= 0 ? "text-bull" : "text-bear"}`}>
                      {fmtPct(p.unrealized_pl_pct)}
                    </div>
                  </div>
                );
              })}
              {s.positions.length === 0 && (
                <div className="text-[12px] text-white/30 py-4 text-center">Keine offenen Positionen.</div>
              )}
            </div>
          </div>
        </div>

        {/* Specialist */}
        {s.report && (
          <div className="surface p-5">
            <div className="flex items-center justify-between mb-3">
              <div className="eyebrow">Specialist · {s.report.symbol}</div>
              <span className="num text-[11px] text-white/40">
                {s.report.sentiment}
                {s.report.as_of ? ` · Stand ${s.report.as_of}` : ""}
              </span>
            </div>
            <p className="text-[13px] text-white/70">„{s.report.summary}“</p>
          </div>
        )}

        <p className="pt-2 text-center text-[11px] text-white/30">
          Demo-Depot, fiktiv · Paper-Trading · keine echten Aufträge · autonomous_ trading solution
        </p>
      </div>
    </main>
  );
}

export function LiveDemo() {
  const { snapshot, paused } = useSnapshotPolling();
  const [range, setRange] = useState<EquityRange>("1W");
  const [percent, setPercent] = useState(false);

  // Hooks must run unconditionally — feed them [] until a snapshot arrives.
  const curve = useMemo(() => snapshot?.equity_curve ?? [], [snapshot]);
  const series = useMemo(() => toEquitySeries(curve), [curve]);
  const bench = useMemo(() => toBenchSeries(curve), [curve]);
  const view = useMemo(() => filterCurveByRange(series, range), [series, range]);
  const benchView = useMemo(() => filterCurveByRange(bench, range), [bench, range]);

  const stamp = snapshot
    ? new Date(snapshot.generated_at).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })
    : "—";

  return (
    <div className="min-h-screen bg-black text-slate-200">
      {/* OSS promo ticker — shared 1:1 with the marketing landing, above the header */}
      <SiteMarquee dark />
      {/* Public marketing header (shared) — dark variant, same black (#000) as the console body */}
      <SiteHeader dark />

      {!snapshot ? (
        <div className="flex h-64 flex-col items-center justify-center gap-1 text-slate-500">
          {paused ? (
            <>
              <span className="text-slate-400">Demo pausiert</span>
              <span className="text-xs text-slate-600">Der Agent ist gerade offline — gleich wieder da.</span>
            </>
          ) : (
            <span>Demo lädt …</span>
          )}
        </div>
      ) : (
        <DashboardBody
          snapshot={snapshot}
          paused={paused}
          stamp={stamp}
          range={range}
          setRange={setRange}
          percent={percent}
          setPercent={setPercent}
          view={view}
          benchView={benchView}
        />
      )}
    </div>
  );
}
