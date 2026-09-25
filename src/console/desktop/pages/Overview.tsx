import { useMemo, useState, useEffect } from "react";
import {
  fmtPct,
  fmtNum,
  fmtTime,
  fmtQty,
  fmtMoney,
} from "@/console/lib/format";
import { useMoney } from "@/console/lib/useMoney";
import { greeting } from "@/console/lib/greeting";
import { getCompanyName } from "@/console/lib/companyName";
import { getSetupState } from "@/lib/desktopBridge";
import { EquityChart } from "@/console/shared/EquityChart";
import { BalancesBand } from "@/console/shared/BalancesBand";
import { KpiBand } from "@/console/shared/KpiBand";
import { PositionsTable } from "@/console/shared/PositionsTable";
import { RoundTableBar } from "@/console/shared/RoundTableView";
import { StatusDot } from "@/console/shared/StatusDot";
import { SegmentPicker } from "@/console/shared/SegmentPicker";
import { IconChevronRight, IconLightbulb } from "@/console/shared/Icons";
import { useStore } from "@/console/store/useStore";
import { SymbolLink } from "@/console/desktop/SymbolLink";
import { usePortfolioPolling } from "@/console/live/usePortfolioPolling";
import { useEquityPolling } from "@/console/live/useEquityPolling";
import { useRoundTablePolling } from "@/console/live/useRoundTablePolling";
import { marketPillLabel, latestVerdictPerSymbol } from "@/console/live/health";
import { computeMaxDrawdownCashflowAdjusted } from "@/console/live/drawdown";
import {
  computeSharpe,
  benchmarkReturnSinceInception,
  liveHistoryReady,
} from "@/console/live/metrics";
import {
  filterCurveByRange,
  rangeReturnCashflowAdjusted,
  changeTone,
  rangeWindowLabel,
  EQUITY_RANGES,
  type EquityRange,
} from "@/console/live/equityRange";
import { useIntradayEquity } from "@/console/live/useIntradayEquity";
import {
  selectEquityView,
  isIntradayRange,
} from "@/console/live/intradayEquity";
import { baselineHint } from "@/console/live/baselineLabel";
import { ago } from "@/console/live/lastSync";

import { useOpenOrdersPolling } from "@/console/live/useOpenOrdersPolling";
import { showOrderBookOnOverview } from "@/console/live/orderPanel";
import { useModeCLive } from "@/console/desktop/useModeCLive";
import { CancelButton, cancelOpenOrder } from "@/console/desktop/orderCancel";
import { ReconciliationBlockCard } from "@/console/desktop/ReconciliationBlockCard";
import { cancelOrder as realCancelOrder } from "@/lib/api";

// Order-book tile helpers (mirrors the Orders page look; kept local to avoid a shared-util refactor).
const obTh =
  "px-4 pb-2.5 font-semibold text-[10px] tracking-[0.12em] uppercase text-white/16";
const obWhen = (d: Date | null) =>
  d
    ? d.toLocaleString("de-DE", {
        day: "2-digit",
        month: "2-digit",
        year: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
const obLimit = (
  limitPrice: number | null,
  stopPrice: number | null,
  sym: string,
) =>
  limitPrice != null
    ? fmtMoney(limitPrice, sym)
    : stopPrice != null
      ? `stop ${fmtMoney(stopPrice, sym)}`
      : "—";

// #3145: the hero's window label moved to equityRange.rangeWindowLabel — it has to know the
// performance baseline, which can cut the selected range short ("since Sep 1, 2026").

/**
 * Console Overview page (G3b, #1050) — faithful UX port of the bundle dashboard:
 * greeting hero + market pill, the total-equity surface (intraday change,
 * since-inception / S&P-today / started row, range toggles, equity-grid chart
 * with S&P overlay), the four KPI cards, and the two-up Decision-queue +
 * Top-positions section. Wired to main's polling store; figures main's engine
 * doesn't expose yet (market state, uptime, win rate, last-sync) render an
 * honest "—". The Senate vote widget is main's renamed Round-Table bar.
 */
export function Overview({
  cancelOrder = realCancelOrder,
}: { cancelOrder?: typeof realCancelOrder } = {}) {
  usePortfolioPolling();
  useEquityPolling();
  useRoundTablePolling();
  // #2743/S3: /health/deep is now polled by the persistent Sidebar (useDeepHealthPolling) so the
  // run-status stays honest on every page — not re-mounted here (would double-poll).
  useOpenOrdersPolling();
  const positions = useStore((s) => s.positions);
  const openOrders = useStore((s) => s.openOrders);
  // #2190: the open-orders book is live-only (Senior/Plus) — hidden in paper,
  // where market orders fill instantly so it's always empty.
  const paperTrading = useStore((s) => s.paperTrading);
  // #overview-ia: the working-orders book shows only in LIVE and only when NOT full-autonomous
  // (Mode C). Under full autonomy nothing queues for a human, so the book would always be empty.
  const autonomousLive = useModeCLive();
  const showOrderBook = showOrderBookOnOverview(paperTrading, autonomousLive);
  const currentEquity = useStore((s) => s.currentEquity);
  const cashEUR = useStore((s) => s.cashEUR);
  const { money, sym, iso } = useMoney();
  const equityCurve = useStore((s) => s.equityCurve);
  const benchmarkCurve = useStore((s) => s.benchmarkCurve);
  // #1957: engine-computed, deposit-neutral KPIs (null on an older engine → legacy fallback below).
  const twrPct = useStore((s) => s.twrPct);
  const netDeposits = useStore((s) => s.netDeposits);
  const roundTable = useStore((s) => s.roundTable);
  const marketOpen = useStore((s) => s.marketOpen);
  const brokerLabel = useStore((s) => s.brokerLabel);
  const audit = useStore((s) => s.audit);
  const setDesktopPage = useStore((s) => s.setDesktopPage);

  // Greet the user by the name they entered in the setup wizard (setup.json,
  // renderer-only). Falls back to a name-less greeting in the browser / pre-setup.
  const [userName, setUserName] = useState<string | null>(null);
  useEffect(() => {
    getSetupState()
      .then((s) => {
        const n = typeof s?.name === "string" ? s.name.trim() : "";
        if (n) setUserName(n);
      })
      .catch(() => {});
  }, []);

  const lastSyncAt = useStore((s) => s.lastSyncAt);
  // 1s clock so the "Last sync" label stays current between polls.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // #3071: honest KPI-card hint — a user-chosen baseline is ALWAYS named next to the
  // numbers it re-anchors; only without one may the cards say "since inception".
  // #3145: it also clamps every chart window, so it is read BEFORE the curves are derived.
  const baselineDate = useStore((s) => s.baselineDate);

  // #3180 (owner, 03.09.): the chart opens on ONE DAY, not a week. The hero change and the
  // S&P overlay follow the selected range, so this also makes "today" the first figure shown.
  const [range, setRange] = useState<EquityRange>("1D");
  const [percent, setPercent] = useState(false);
  // #2124: 1D/1W use the real intraday curve (local state, NOT the store — keeps
  // the daily equityCurve + lastEquity/dayPL KPI intact). Fail-soft to daily.
  const intradayCurve = useIntradayEquity(range);
  const view = useMemo(
    () => selectEquityView(range, intradayCurve, equityCurve),
    [range, intradayCurve, equityCurve],
  );
  // No intraday S&P benchmark yet (v1) — overlay only on the daily ranges, else
  // an intraday portfolio line vs a daily benchmark would misalign the x-axis.
  const benchView = useMemo(
    () =>
      isIntradayRange(range)
        ? undefined
        : filterCurveByRange(benchmarkCurve, range, baselineDate),
    [benchmarkCurve, range, baselineDate],
  );

  // The full engine control (Kill Switch / go-live) lives in the sidebar + Settings > Trading
  // (#1642); the Overview shows no engine-control button and no idle banner.
  const systemHalted = useStore((s) => s.systemHalted);
  const strategyRunning = useStore((s) => s.strategyRunning);

  // ── Derived KPIs (null → "—") ───────────────────────────────────────────
  // #overview-ia: the hero change follows the selected range (brokerage convention) — first→last of
  // the displayed curve `view`, so it always matches the chart. ("ALL" here = no re-filter; `view`
  // is already the range-selected curve.) Replaces the old always-"today" Daily P/L.
  // #2717: deposit-neutral (TWR) hero change — a deposit inside the window no longer
  // shows up as a gain (the "+54 % past week" bug). Consistent with the since-inception
  // TWR, the cash-flow-adjusted max-drawdown and the chart's %-mode.
  // #3145: the baseline is time zero for the hero too — the window never reaches before it,
  // and the label states the window that was actually measured (a "past week" next to a
  // baseline set yesterday is what made this figure look like it contradicted the KPI card).
  const rangeChange = rangeReturnCashflowAdjusted(view, "ALL");
  const rangeLabel = rangeWindowLabel(
    range,
    baselineDate,
    view.length > 0 ? view[view.length - 1].t : new Date(),
  );
  const startEquity = equityCurve.length > 0 ? equityCurve[0].eur : null;
  // #1957: "since inception" = the engine's deposit-neutral time-weighted return, so a deposit no
  // longer shows up as a gain. Fail-soft to the legacy (now-start)/start only when the engine did
  // not supply twr_pct (older engine) — that legacy value IS deposit-contaminated, but it keeps a
  // number on screen rather than a dash during a version skew.
  const legacyReturnPct =
    startEquity != null && startEquity !== 0 && currentEquity != null
      ? ((currentEquity - startEquity) / startEquity) * 100
      : null;
  const totalReturnPct = twrPct != null ? twrPct : legacyReturnPct;
  // #overview-ia: deposit-neutral, consistent with the TWR "Since inception" — a deposit/withdrawal
  // no longer fabricates or masks a drawdown (the raw curve did both).
  const maxDD = computeMaxDrawdownCashflowAdjusted(
    equityCurve.map((p) => ({ eur: p.eur, cashflow: p.cashflow })),
  );
  // Live-metrics honesty (#2618): right after a paper→live switch the paper history is
  // intentionally NOT carried over, so the live curve is thin for the first ~20 live days.
  // With so few points the trailing S&P point can be a carry-forward of yesterday's close
  // (today's SPY daily bar not finalised) → benchmarkTodayPct reads a FAKE "0.00 %"; and
  // Sharpe is not yet meaningful. While the live history is still building, present both as
  // "—" with an explanatory banner instead of a misleading number. Paper is unaffected.
  const liveMetricsBuilding =
    paperTrading === false && !liveHistoryReady(equityCurve);
  const sharpe = liveMetricsBuilding ? null : computeSharpe(equityCurve);
  // #overview-ia: the S&P shown SINCE INCEPTION (first→last of the rebased benchmark) so it is
  // directly comparable to the portfolio's since-inception (TWR) figure. Ungated (like the portfolio
  // card) — a first-vs-last return is robust to the trailing carry-forward that made the 1-day
  // "S&P today" read a fake 0% while the live curve was still thin.
  const benchInception = benchmarkReturnSinceInception(benchmarkCurve);
  const sinceHint = baselineHint(baselineDate);
  const investedPct = positions.reduce((a, p) => a + (p.weight ?? 0), 0);
  const investedEUR = positions.reduce((a, p) => a + p.marketValue, 0);
  // T6 Option B (#1474): when the ephemeral round-table store is empty (e.g.
  // after an engine restart), surface the last known verdict per symbol from the
  // permanent audit log — labelled "last known", never shown as a live verdict.
  const lastKnown = useMemo(
    () =>
      roundTable.length === 0 ? latestVerdictPerSymbol(audit).slice(0, 4) : [],
    [roundTable.length, audit],
  );

  return (
    <div className="px-8 py-7 space-y-7 max-w-[1100px]">
      {/* Hero header */}
      <div className="flex items-end justify-between">
        <div>
          <div className="eyebrow mb-2">Portfolio · Live · Paper trading</div>
          <h1 className="text-[54px] font-bold tracking-tight3 leading-[1.02] text-white/92">
            {greeting(userName)}
          </h1>
          {/* #overview-ia: the decisions count lives once, in the Decision-queue header below — the
              hero no longer duplicates it. */}
        </div>
        <div className="flex items-center gap-2">
          {brokerLabel && <span className="pill">{brokerLabel}</span>}
          <StatusDot
            tone={marketOpen == null ? "neutral" : marketOpen ? "on" : "off"}
          >
            Market · {marketPillLabel(marketOpen)}
          </StatusDot>
        </div>
      </div>

      {/* #2/#3: no idle prompt here. Paper auto-starts (INC-1) and its state is already shown by the
          sidebar footer ("Live Trading" + engine status) and the Overview header. #2465: a live boot
          happens only via the go-live consent and auto-arms — there is no LIVE-idle re-confirm banner. */}

      {/* C-D: a fresh install has no data yet — say so, so the blank "—" values below don't read as
          "broken". RC2-1: but if the engine booted LIVE (paper_trading===false) and equity is still
          null, that is a dropped engine connection, NOT a fresh paper setup — say so honestly instead
          of the misleading "starts paper-trading" banner. */}
      {currentEquity == null &&
        (paperTrading === false ? (
          <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
            <IconLightbulb
              width={16}
              height={16}
              className="text-white/70 mt-1 shrink-0"
            />
            <div className="text-[13.5px] text-white/70 leading-relaxed">
              <p>
                <span className="font-semibold text-white/90">
                  Connection to engine lost — reconnecting…
                </span>{" "}
                Your live account data reappears as soon as the engine responds.
              </p>
              <p className="text-[13px] text-white/45 mt-1">
                Note: Open positions at your broker are unaffected — the
                dashboard is only waiting on the engine.
              </p>
            </div>
          </div>
        ) : (
          <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
            <IconLightbulb
              width={16}
              height={16}
              className="text-white/70 mt-1 shrink-0"
            />
            <div className="text-[13.5px] text-white/70 leading-relaxed">
              <p>
                <span className="font-semibold text-white/90">
                  Setting up dashboard.
                </span>{" "}
                Your dashboard fills in as the engine syncs its first data and
                starts paper-trading.
              </p>
              <p className="text-[13px] text-white/45 mt-1">
                Note: Empty metrics are expected on a fresh start, not an error,
                and will populate in a moment.
              </p>
            </div>
          </div>
        ))}

      {/* PR-3 / #2618: LIVE with a known equity but a still-thin live history (the paper→live
          switch carries NO paper history over — see /benchmark-equity "no_live_history"). This
          is a THIRD, disjoint condition: both banners above require currentEquity==null, this one
          requires a non-null equity, so it never overlaps the reconnect / paper-setup banners.
          It stays up for the whole building window (0..~20 live days), which is exactly when the
          S&P-comparison would read a fabricated "0.00 %" and Sharpe is not yet meaningful — so
          those two are shown as "—" (see liveMetricsBuilding) until real live history accrues. */}
      {paperTrading === false &&
        currentEquity != null &&
        liveMetricsBuilding && (
          <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
            <IconLightbulb
              width={16}
              height={16}
              className="text-white/70 mt-1 shrink-0"
            />
            <div className="text-[13.5px] text-white/70 leading-relaxed">
              <p>
                <span className="font-semibold text-white/90">
                  Live track record is building.
                </span>{" "}
                Your live equity, positions and P/L are live below. The S&amp;P
                500 comparison and the annualized Sharpe ratio appear once about
                20 live trading days have accumulated — until then they show
                “—”, not a number, because your paper-trading history is
                intentionally not carried into the live account.
              </p>
              <p className="text-[13px] text-white/45 mt-1">
                This is expected right after going live, not an error — the
                benchmark line and risk metrics fill in from your first live
                trading days.
              </p>
            </div>
          </div>
        )}

      {/* #3430: the reconciliation block (#3389) — visible and releasable. Renders nothing while
          no block stands. */}
      <ReconciliationBlockCard />

      {/* Total-equity surface */}
      <div className="surface p-7 relative overflow-hidden">
        <div className="grid grid-cols-[1fr_auto] gap-6 items-start relative">
          <div>
            <div className="eyebrow mb-2">Total equity · {iso}</div>
            <div className="flex items-baseline gap-4 flex-wrap">
              <div className="num text-[56px] font-bold tracking-tight3 leading-none text-white/92">
                {currentEquity != null
                  ? money(currentEquity).replace(sym, sym + " ")
                  : "—"}
              </div>
              {rangeChange != null && (
                <div
                  className={`flex items-baseline gap-3 ${changeTone(rangeChange) === "bull" ? "text-bull" : "text-bear"}`}
                >
                  <span className="num text-[20px] font-semibold">
                    {money(rangeChange.abs, { sign: true })}
                  </span>
                  <span className="num text-[14px]">
                    {fmtPct(rangeChange.pct)} {rangeLabel}
                  </span>
                </div>
              )}
            </div>
            {/* #overview-ia: capital context (Started / deposits / cash) moved OUT of the hero into a
                dedicated Balances tile row below — the hero holds only the account value + change. */}
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <div className="flex items-center gap-1.5">
              {/* UXC-1 S8 (#3177): neutral segmented controls — selection sits on white/12,
                  never a green tint (accent carries meaning, not selection). */}
              <SegmentPicker
                ariaLabel="Equity scale"
                options={[
                  { id: "abs", label: sym },
                  { id: "pct", label: "%" },
                ]}
                value={percent ? "pct" : "abs"}
                onChange={(id) => setPercent(id === "pct")}
              />
              <SegmentPicker
                ariaLabel="Equity range"
                options={EQUITY_RANGES.map((p) => ({ id: p, label: p }))}
                value={range}
                onChange={(id) => setRange(id as EquityRange)}
              />
            </div>
            <div className="text-[12px] text-white/45 num">
              Last sync · {ago(lastSyncAt, now)}
            </div>
          </div>
        </div>
        <div className="mt-5 equity-grid rounded-xl">
          {/* #2588: pass the REAL account currency — the chart's axis/tooltip default to "€",
              which mislabelled the USD paper account (e.g. "€129.4k"). */}
          <EquityChart
            data={view}
            benchmark={benchView}
            percent={percent}
            height={240}
            glowColor="#00c27a"
            currency={sym}
          />
        </div>
        <div className="flex items-center gap-5 mt-4 text-[12px] text-white/60">
          <span className="flex items-center gap-2">
            <span
              className="w-3 h-[2px] rounded"
              style={{ background: "#00c27a" }}
            />{" "}
            autonomous_
          </span>
          {benchView && benchView.length >= 2 && (
            <span className="flex items-center gap-2">
              <span
                className="w-3 h-[1px] border-t border-dashed"
                style={{ borderColor: "rgba(255,255,255,0.6)" }}
              />{" "}
              S&amp;P 500
            </span>
          )}
        </div>
      </div>

      {/* #overview-ia / #3321 (C1): Balances band — liquidity/composition. Shared component so the
          console dashboard AND the public live-demo render the identical band. */}
      <BalancesBand
        cashEUR={cashEUR}
        currentEquity={currentEquity}
        investedEUR={investedEUR}
        investedPct={investedPct}
        positionsCount={positions.length}
        netDeposits={netDeposits}
        startEquity={startEquity}
        iso={iso}
        money={money}
      />

      {/* KPI cards — performance & risk (#3321 C1: shared KpiBand, since-inception framing). */}
      <KpiBand
        totalReturnPct={totalReturnPct}
        benchInception={benchInception}
        maxDD={maxDD}
        sharpe={sharpe}
        sinceHint={sinceHint}
        sharpeHint={
          liveMetricsBuilding ? "after ~20 live days" : "realised curve"
        }
      />

      {/* #overview-ia: Working orders sit ABOVE the holdings — they are the actionable, awaiting-you
          items. Shown ONLY in LIVE and ONLY when NOT full-autonomous (Mode C): under full autonomy
          nothing queues for a human, so the book would always be empty. Per-order HITL Cancel
          (EU AI Act Art. 14) lives on the Orders page. */}
      {showOrderBook && (
        <div className="surface p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="eyebrow mb-2">Order book</div>
              <div className="text-[14px] font-semibold tracking-tight2 text-white/92">
                {openOrders.length} working{" "}
                {openOrders.length === 1 ? "order" : "orders"}
              </div>
            </div>
            <button
              onClick={() => setDesktopPage("activities")}
              className="btn-link"
            >
              View all <IconChevronRight width={10} height={10} />
            </button>
          </div>
          {openOrders.length === 0 ? (
            <div className="text-[13px] text-white/45 p-4 text-center">
              No open orders — nothing working at the broker right now.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12.5px]">
                <thead>
                  <tr>
                    <th className={`${obTh} text-left`}>Symbol</th>
                    <th className={`${obTh} text-left`}>Side</th>
                    <th className={`${obTh} text-right`}>Qty</th>
                    <th className={`${obTh} text-left`}>Type</th>
                    <th className={`${obTh} text-right`}>Limit</th>
                    <th className={`${obTh} text-left`}>Status</th>
                    <th className={`${obTh} text-right`}>Submitted</th>
                    <th className={`${obTh} text-right`}></th>
                  </tr>
                </thead>
                <tbody>
                  {openOrders.slice(0, 6).map((o) => (
                    <tr
                      key={o.id}
                      className="border-t border-white/5 hover:bg-white/[0.025] transition-colors"
                    >
                      <td className="px-4 py-3 font-bold tracking-tight2 text-white/92">
                        <SymbolLink symbol={o.symbol} />
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`text-[11px] font-semibold uppercase tracking-wider ${o.side === "buy" ? "text-bull" : "text-bear"}`}
                        >
                          {o.side}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right num text-white/92">
                        {fmtQty(o.qty)}
                      </td>
                      <td className="px-4 py-3 text-white/55">
                        {o.type || "—"}
                      </td>
                      <td className="px-4 py-3 text-right num text-white/92">
                        {obLimit(o.limitPrice, o.stopPrice, sym)}
                      </td>
                      <td className="px-4 py-3 text-white/55">
                        {o.status || "open"}
                      </td>
                      <td className="px-4 py-3 text-right num text-white/45">
                        {obWhen(o.submittedAt)}
                      </td>
                      {/* HITL Cancel (EU AI Act Art. 14) — always available, even in full-autonomous mode. */}
                      <td className="px-4 py-3 text-right">
                        <CancelButton
                          onCancel={() => cancelOpenOrder(o.id, cancelOrder)}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {openOrders.length > 6 && (
                <div className="text-[12px] text-white/40 pt-2 px-4">
                  Showing 6 of {openOrders.length} — view all on the Orders
                  page.
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Decision queue + Top positions */}
      <div className="grid grid-cols-[1.15fr_1fr] gap-5">
        <div className="surface p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="eyebrow mb-2">Decision queue</div>
              <div className="text-[14px] font-semibold tracking-tight2 text-white/92">
                {roundTable.length}{" "}
                {roundTable.length === 1 ? "decision" : "decisions"} this
                session
              </div>
            </div>
            <button
              onClick={() => setDesktopPage("decisions")}
              className="btn-link"
            >
              View all <IconChevronRight width={10} height={10} />
            </button>
          </div>
          <div className="space-y-2">
            {roundTable.slice(0, 4).map((d) => (
              <button
                key={d.symbol}
                onClick={() => setDesktopPage("decisions")}
                className="w-full text-left p-3 rounded-lg bg-white/[0.025] border border-white/5 hover:bg-white/[0.045] hover:border-white/12 transition-all"
              >
                <div className="flex items-center gap-3 mb-2">
                  <span className="font-bold text-[13px] tracking-tight2 text-white/92">
                    <SymbolLink symbol={d.symbol} />
                  </span>
                  <span
                    className={`pill ${d.action === "BUY" ? "pill-bull" : d.action === "SELL" ? "pill-bear" : "pill-warn"}`}
                  >
                    {d.action}
                  </span>
                  {/* #overview-ia: the honest raw consensus (0.72 → "72%"), not the meter's rebased 0.44. */}
                  <span className="ml-auto pill pill-strong text-[12px]">
                    {fmtNum(d.consensusScore * 100, 0)}% consensus
                  </span>
                </div>
                {getCompanyName(d.symbol) && (
                  <div className="text-[11px] text-white/40 -mt-1 mb-2 truncate">
                    {getCompanyName(d.symbol)}
                  </div>
                )}
                <RoundTableBar
                  votesFor={d.votesFor}
                  votesAbstain={d.votesAbstain}
                  votesAgainst={d.votesAgainst}
                />
              </button>
            ))}
            {roundTable.length === 0 && lastKnown.length > 0 && (
              <>
                <div className="text-[12px] text-white/45 px-1 pb-1">
                  No live decisions this session — showing the last known
                  verdict per symbol from the audit log.
                </div>
                {lastKnown.map((v) => (
                  <div
                    key={v.symbol}
                    className="w-full p-3 rounded-lg bg-white/[0.015] border border-white/5 opacity-60"
                  >
                    <div className="flex items-center gap-3">
                      <span className="font-bold text-[13px] tracking-tight2 text-white/80">
                        <SymbolLink symbol={v.symbol} />
                      </span>
                      <span
                        className={`pill ${v.action === "BUY" ? "pill-bull" : v.action === "SELL" ? "pill-bear" : "pill-warn"}`}
                      >
                        {v.action}
                      </span>
                      <span className="ml-auto text-[12px] text-white/50 num">
                        last known · {fmtTime(v.ts)}
                      </span>
                    </div>
                  </div>
                ))}
              </>
            )}
            {roundTable.length === 0 && lastKnown.length === 0 && (
              <div className="text-[13px] text-white/45 p-4 text-center">
                No decisions yet — the round table runs as the engine ticks.
              </div>
            )}
          </div>
        </div>

        {/* #3321 C1: shared PositionsTable — the console injects the navigating SymbolLink + View-all. */}
        <PositionsTable
          positions={positions}
          money={money}
          investedPct={investedPct}
          onViewAll={() => setDesktopPage("positions")}
          renderSymbol={(s) => <SymbolLink symbol={s} />}
        />
      </div>
    </div>
  );
}
