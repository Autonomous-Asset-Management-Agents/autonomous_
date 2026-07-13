import { useMemo, useState, useEffect } from "react";
import { fmtEUR, fmtPct, fmtNum, fmtTime } from "@/console/lib/format";
import { greeting } from "@/console/lib/greeting";
import { getCompanyName } from "@/console/lib/companyName";
import { getSetupState } from "@/lib/desktopBridge";
import { EquityChart } from "@/console/shared/EquityChart";
import { Sparkline } from "@/console/shared/Sparkline";
import { RoundTableBar } from "@/console/shared/RoundTableView";
import { StatusDot } from "@/console/shared/StatusDot";
import { IconChevronRight, IconShield, IconLightbulb } from "@/console/shared/Icons";
import { useStore } from "@/console/store/useStore";
import { usePortfolioPolling } from "@/console/live/usePortfolioPolling";
import { useEquityPolling } from "@/console/live/useEquityPolling";
import { useRoundTablePolling } from "@/console/live/useRoundTablePolling";
import { useHealthPolling, marketPillLabel, latestVerdictPerSymbol } from "@/console/live/health";
import { computeMaxDrawdown } from "@/console/live/drawdown";
import { computeSharpe, benchmarkTodayPct } from "@/console/live/metrics";
import { filterCurveByRange, EQUITY_RANGES, type EquityRange } from "@/console/live/equityRange";
import { ago } from "@/console/live/lastSync";
import { useKillSwitch } from "@/console/desktop/useKillSwitch";

/**
 * Console Overview page (G3b, #1050) — faithful UX port of the bundle dashboard:
 * greeting hero + market pill, the total-equity surface (intraday change,
 * since-inception / S&P-today / started row, range toggles, equity-grid chart
 * with S&P overlay), the four KPI cards, and the two-up Decision-queue +
 * Top-positions section. Wired to main's polling store; figures main's engine
 * doesn't expose yet (market state, uptime, win rate, last-sync) render an
 * honest "—". The Senate vote widget is main's renamed Round-Table bar.
 */
export function Overview() {
  usePortfolioPolling();
  useEquityPolling();
  useRoundTablePolling();
  useHealthPolling();
  const positions = useStore((s) => s.positions);
  const currentEquity = useStore((s) => s.currentEquity);
  const lastEquity = useStore((s) => s.lastEquity);
  const cashEUR = useStore((s) => s.cashEUR);
  const equityCurve = useStore((s) => s.equityCurve);
  const benchmarkCurve = useStore((s) => s.benchmarkCurve);
  const roundTable = useStore((s) => s.roundTable);
  const marketOpen = useStore((s) => s.marketOpen);
  const brokerLabel = useStore((s) => s.brokerLabel);
  const audit = useStore((s) => s.audit);
  const setDesktopPage = useStore((s) => s.setDesktopPage);
  const [showConfirmModal, setShowConfirmModal] = useState(false);

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

  const [range, setRange] = useState<EquityRange>("1W");
  const [percent, setPercent] = useState(false);
  const view = useMemo(() => filterCurveByRange(equityCurve, range), [equityCurve, range]);
  const benchView = useMemo(() => filterCurveByRange(benchmarkCurve, range), [benchmarkCurve, range]);

  // Kill switch — live state from /health + shared halt/reset actions (#1642).
  const systemHalted = useStore((s) => s.systemHalted);
  const { killArmed, killMsg, killResetting, armTimeLeft, handleKill, handleResetKill } = useKillSwitch();

  // ── Derived KPIs (null → "—") ───────────────────────────────────────────
  const dayPL = currentEquity != null && lastEquity != null ? currentEquity - lastEquity : null;
  const dayPLPct = dayPL != null && lastEquity ? (dayPL / lastEquity) * 100 : null;
  const startEquity = equityCurve.length > 0 ? equityCurve[0].eur : null;
  const totalReturnPct =
    startEquity != null && startEquity !== 0 && currentEquity != null
      ? ((currentEquity - startEquity) / startEquity) * 100
      : null;
  const maxDD = computeMaxDrawdown(equityCurve.map((p) => ({ eur: p.eur })));
  const sharpe = computeSharpe(equityCurve);
  const benchToday = benchmarkTodayPct(benchmarkCurve);
  const investedPct = positions.reduce((a, p) => a + (p.weight ?? 0), 0);
  // T6 Option B (#1474): when the ephemeral round-table store is empty (e.g.
  // after an engine restart), surface the last known verdict per symbol from the
  // permanent audit log — labelled "last known", never shown as a live verdict.
  const lastKnown = useMemo(
    () => (roundTable.length === 0 ? latestVerdictPerSymbol(audit).slice(0, 4) : []),
    [roundTable.length, audit],
  );

  return (
    <div className="px-8 py-7 space-y-7 max-w-[1100px]">
      {/* Hero header */}
      <div className="flex items-end justify-between">
        <div>
          <div className="eyebrow mb-2">Portfolio · Live · Paper trading</div>
          <h1 className="text-[54px] font-bold tracking-tight3 leading-[1.02] text-white/92">{greeting(userName)}</h1>
          <p className="text-white/55 mt-2 text-[13px]">
            The round table raised {roundTable.length} {roundTable.length === 1 ? "decision" : "decisions"} today.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {brokerLabel && <span className="pill">{brokerLabel}</span>}
          <StatusDot tone={marketOpen == null ? 'neutral' : marketOpen ? 'on' : 'off'}>Market · {marketPillLabel(marketOpen)}</StatusDot>
        </div>
      </div>

      {/* C-D: a fresh install has no data yet — say so, so the blank "—" values below don't read as "broken". */}
      {currentEquity == null && (
        <div className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
          <IconLightbulb width={16} height={16} className="text-white/70 mt-1 shrink-0" />
          <div className="text-[13.5px] text-white/70 leading-relaxed">
            <p>
              <span className="font-semibold text-white/90">Setting up dashboard.</span> Your dashboard fills in as the engine syncs its first data and starts paper-trading.
            </p>
            <p className="text-[13px] text-white/45 mt-1">
              Note: Empty metrics are expected on a fresh start, not an error, and will populate in a moment.
            </p>
          </div>
        </div>
      )}

      {/* Total-equity surface */}
      <div className="surface p-7 relative overflow-hidden">
        <div className="grid grid-cols-[1fr_auto] gap-6 items-start relative">
          <div>
            <div className="eyebrow mb-3">Total equity · EUR</div>
            <div className="flex items-baseline gap-4 flex-wrap">
              <div className="num text-[56px] font-bold tracking-tight3 leading-none text-white/92">
                {currentEquity != null ? fmtEUR(currentEquity).replace("€", "€ ") : "—"}
              </div>
              {dayPL != null && (
                <div className={`flex items-baseline gap-3 ${dayPL >= 0 ? "text-bull" : "text-bear"}`}>
                  <span className="num text-[20px] font-semibold">{fmtEUR(dayPL, { sign: true })}</span>
                  <span className="num text-[14px]">{dayPLPct != null ? `${fmtPct(dayPLPct)} today` : "today"}</span>
                </div>
              )}
            </div>
            <div className="flex items-center gap-6 mt-4 text-[13px] text-white/60">
              <span>
                Since inception{" "}
                {totalReturnPct != null ? (
                  <span className={`num ${totalReturnPct >= 0 ? "text-bull" : "text-bear"}`}>{fmtPct(totalReturnPct)}</span>
                ) : (
                  <span className="num text-white/40">—</span>
                )}
              </span>
              <span className="hairline-v h-3" />
              <span>S&amp;P 500 today <span className="num text-white/92">{benchToday != null ? fmtPct(benchToday) : "—"}</span></span>
              <span className="hairline-v h-3" />
              <span>Started <span className="num text-white/92">{startEquity != null ? fmtEUR(startEquity, { compact: true }) : "—"}</span></span>
            </div>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => setPercent((p) => !p)}
                className={`text-[12px] px-3 py-1 rounded-md border transition-colors ${percent ? "border-transparent text-[#00c27a] bg-[#00c27a]/12" : "border-white/5 text-white/55 hover:text-white/92 hover:border-white/15"}`}
              >
                %
              </button>
              {EQUITY_RANGES.map((p) => (
                <button
                  key={p}
                  onClick={() => setRange(p)}
                  className={`text-[12px] px-3 py-1 rounded-md border transition-colors ${
                    p === range
                      ? "border-transparent text-[#00c27a] bg-[#00c27a]/12"
                      : "border-white/5 text-white/55 hover:text-white/92 hover:border-white/15"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
            <div className="text-[12px] text-white/45 num">Last sync · {ago(lastSyncAt, now)}</div>
          </div>
        </div>
        <div className="mt-5 equity-grid rounded-xl">
          <EquityChart data={view} benchmark={benchView} percent={percent} height={240} glowColor="#00c27a" />
        </div>
        <div className="flex items-center gap-5 mt-4 text-[12px] text-white/60">
          <span className="flex items-center gap-2"><span className="w-3 h-[2px] rounded" style={{ background: "#00c27a" }} /> autonomous_</span>
          {benchView.length >= 2 && (
            <span className="flex items-center gap-2">
              <span className="w-3 h-[1px] border-t border-dashed" style={{ borderColor: "rgba(212,168,83,0.6)" }} /> S&amp;P 500
            </span>
          )}
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-4 gap-4">
        {[
          {
            label: "Daily P / L",
            value: dayPL != null ? fmtEUR(dayPL, { sign: true }) : "—",
            hint: dayPLPct != null ? `${fmtPct(dayPLPct)} today` : "today · EUR",
            color: dayPL != null ? (dayPL >= 0 ? "text-bull" : "text-bear") : undefined,
          },
          { label: "Max drawdown", value: maxDD < 0 ? fmtPct(maxDD) : "—", hint: "since inception", color: "text-bear" },
          { label: "Sharpe (annualized)", value: sharpe != null ? sharpe.toFixed(2) : "—", hint: "realised curve" },
          {
            label: "Cash / margin",
            value: cashEUR != null ? fmtEUR(cashEUR, { compact: true }) : "—",
            hint: cashEUR != null && cashEUR < 0 ? "margin used · EUR" : "settled cash · EUR",
          },
        ].map((k) => (
          <div key={k.label} className="surface-flat p-4">
            <div className="eyebrow mb-2">{k.label}</div>
            <div className={`num text-[22px] font-semibold tracking-tight2 flex items-baseline ${k.color ?? "text-white/92"}`}>{k.value}</div>
            <div className="text-[12px] text-white/45 mt-1">{k.hint}</div>
          </div>
        ))}
      </div>

      {/* Kill switch — prominent safety control (#1642, Option 1 in English) */}
      <div className="surface p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-white/[0.04] border border-white/10 shrink-0">
              <IconShield width={16} height={16} className="text-white/70" />
            </div>
            <h3 className="text-[14px] font-semibold text-white/92">Emergency Safety Control</h3>
          </div>
          <div className="flex items-center gap-2 text-[12px] font-medium text-white/70">
            {systemHalted == null ? (
              <span className="text-white/40">—</span>
            ) : systemHalted ? (
              <>
                <span className="w-1.5 h-1.5 rounded-full bg-[#ff5a52] shrink-0" />
                <span>System Status: Halted</span>
              </>
            ) : (
              <>
                <span className="w-1.5 h-1.5 rounded-full bg-[#00c27a] shrink-0" />
                <span>System Status: Active</span>
              </>
            )}
          </div>
        </div>

        <p className="text-[13px] text-white/60 leading-relaxed max-w-2xl">
          This safety control allows you to immediately halt all automated trading operations. Open positions in your Alpaca account remain unaffected and must be managed manually. Halt and reset actions are permanently recorded on the WORM compliance audit log.
        </p>

        {killMsg && (
          <div className="text-[13px] px-3 py-2 rounded bg-white/[0.03] border border-white/5 text-white/70">
            {killMsg}
          </div>
        )}

        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-3 border-t border-white/5">
          <div className="text-[12px] text-white/45 text-left w-full sm:w-auto">
            Click to initiate emergency stop. Confirmation will be required.
          </div>
          <div className="flex items-center gap-3 w-full sm:w-auto justify-end">
            {systemHalted && (
              <button
                className="px-5 py-2 text-[13px] font-semibold text-white/80 bg-white/[0.06] hover:bg-white/[0.1] rounded-full border border-white/10 transition-colors"
                onClick={() => void handleResetKill()}
                disabled={killResetting}
                style={{ opacity: killResetting ? 0.5 : 1 }}
              >
                {killResetting ? "Resetting…" : "Reset System & Resume"}
              </button>
            )}
            <button
              className="rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide text-white bg-[#ff5a52] hover:bg-[#ff6c65] border border-transparent transition-all transform active:scale-[0.98]"
              onClick={() => setShowConfirmModal(true)}
              disabled={systemHalted === true}
              style={{ opacity: systemHalted ? 0.4 : 1 }}
            >
              Kill Switch
            </button>
          </div>
        </div>
      </div>

      {/* Decision queue + Top positions */}
      <div className="grid grid-cols-[1.15fr_1fr] gap-5">
        <div className="surface p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="eyebrow mb-1">Decision queue</div>
              <div className="text-[14px] font-semibold tracking-tight2 text-white/92">
                {roundTable.length} {roundTable.length === 1 ? "decision" : "decisions"} this session
              </div>
            </div>
            <button onClick={() => setDesktopPage("decisions")} className="text-[12px] text-white/55 hover:text-white/92 flex items-center gap-1">
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
                  <span className="font-bold text-[13px] tracking-tight2 text-white/92">{d.symbol}</span>
                  <span className={`pill ${d.action === "BUY" ? "pill-bull" : d.action === "SELL" ? "pill-bear" : "pill-warn"}`}>{d.action}</span>
                  <span className="ml-auto pill pill-strong text-[12px]">{fmtPct(d.conviction * 100, 0).replace("+", "")} conviction</span>
                </div>
                {getCompanyName(d.symbol) && (
                  <div className="text-[11px] text-white/40 -mt-1 mb-2 truncate">{getCompanyName(d.symbol)}</div>
                )}
                <RoundTableBar votesFor={d.votesFor} votesAbstain={d.votesAbstain} votesAgainst={d.votesAgainst} />
              </button>
            ))}
            {roundTable.length === 0 && lastKnown.length > 0 && (
              <>
                <div className="text-[12px] text-white/45 px-1 pb-1">
                  No live decisions this session — showing the last known verdict per symbol from the audit log.
                </div>
                {lastKnown.map((v) => (
                  <div key={v.symbol} className="w-full p-3 rounded-lg bg-white/[0.015] border border-white/5 opacity-60">
                    <div className="flex items-center gap-3">
                      <span className="font-bold text-[13px] tracking-tight2 text-white/80">{v.symbol}</span>
                      <span className={`pill ${v.action === "BUY" ? "pill-bull" : v.action === "SELL" ? "pill-bear" : "pill-warn"}`}>{v.action}</span>
                      <span className="ml-auto text-[12px] text-white/50 num">last known · {fmtTime(v.ts)}</span>
                    </div>
                  </div>
                ))}
              </>
            )}
            {roundTable.length === 0 && lastKnown.length === 0 && (
              <div className="text-[13px] text-white/45 p-4 text-center">No decisions yet — the round table runs as the engine ticks.</div>
            )}
          </div>
        </div>

        <div className="surface p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="eyebrow mb-1">Top positions</div>
              <div className="text-[14px] font-semibold tracking-tight2 text-white/92">
                {positions.length} open · {fmtNum(investedPct, 1)}% invested
              </div>
            </div>
            <button onClick={() => setDesktopPage("positions")} className="text-[12px] text-white/55 hover:text-white/92 flex items-center gap-1">
              View all <IconChevronRight width={10} height={10} />
            </button>
          </div>
          <div className="space-y-0">
            {positions.slice(0, 5).map((p) => {
              const sparkData = [p.avgEntry, p.last];
              return (
                <div key={p.symbol} className="flex items-center gap-3 py-2.5 border-b border-white/5 last:border-0">
                  <div className="w-24 min-w-0">
                    <div className="font-bold text-[13px] tracking-tight2 text-white/92 truncate">{p.symbol.split(".")[0]}</div>
                    {p.name && p.name !== p.symbol && (
                      <div className="text-[10px] text-white/40 truncate leading-tight">{p.name}</div>
                    )}
                  </div>
                  <Sparkline data={sparkData} width={56} height={20} />
                  <div className="num text-[12px] text-white/92 ml-auto">{fmtEUR(p.marketValue, { compact: true })}</div>
                  <div className={`num text-[12px] w-16 text-right ${p.unrealizedPct >= 0 ? "text-bull" : "text-bear"}`}>{fmtPct(p.unrealizedPct)}</div>
                </div>
              );
            })}
            {positions.length === 0 && <div className="text-[13px] text-white/45 p-4 text-center">No open positions.</div>}
          </div>
        </div>
      </div>

      {/* Confirmation Modal */}
      {showConfirmModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="surface max-w-md w-full p-6 space-y-4 rounded-xl border border-white/10 shadow-2xl relative">
            <div className="flex items-center gap-3 text-bear">
              <IconShield width={24} height={24} />
              <h3 className="text-lg font-bold text-white">Trigger Emergency Stop?</h3>
            </div>
            <p className="text-[13px] text-white/70 leading-relaxed">
              Are you sure you want to trigger the Kill Switch? This will immediately halt the trading strategy and prevent the engine from sending any new orders. Existing open positions will remain in your account and must be closed manually if needed.
            </p>
            <p className="text-[12px] text-[#ffcc66]/80 bg-[#ffcc66]/10 border border-[#ffcc66]/20 p-2.5 rounded">
              <strong>Audit Notice:</strong> This emergency action is permanently recorded on the WORM compliance audit trail.
            </p>
            <div className="flex justify-end gap-3 pt-2">
              <button
                className="btn rounded-full px-5 py-2"
                onClick={() => setShowConfirmModal(false)}
              >
                Cancel
              </button>
              <button
                className="rounded-full px-5 py-2 font-bold text-white bg-[#ff5a52] hover:bg-[#ff6c65] border border-transparent transition-all"
                onClick={() => {
                  setShowConfirmModal(false);
                  void handleKill();
                }}
              >
                Yes, Trigger Halt
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
