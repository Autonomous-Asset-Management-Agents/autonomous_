import { motion } from "framer-motion";
import { useEffect, useState, useMemo } from "react";
import { Play, Square, AlertTriangle, Activity, CheckCircle, XCircle, Loader2, Save, RefreshCw, ShieldAlert, Power, Clock } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { useQueryClient, useQuery } from "@tanstack/react-query";

// Deterministic Pseudo-Random Number Generator for Demo Mode
function generateDemoData(): { date: string; equity: number }[] {
  const points = [];
  const now = new Date();
  let currentEquity = 7519.82;
  let seed = 42;
  const lcg = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };
  for (let i = 180; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    currentEquity = currentEquity * (1 + (lcg() * 0.02 - 0.009));
    points.push({
      date: d.toISOString().split('T')[0],
      equity: currentEquity,
    });
  }
  return points;
}
import {
  startLive, stop, panicSell,
  fetchStrategy, fetchRiskLimits, updateRiskLimits, updateBotStatus,
  fetchRecentTrades, fetchBenchmarkEquity, RecentTrade, RiskLimits
} from "@/lib/api";
import { BrokerConnectionWidget } from "@/components/BrokerConnectionWidget";
import { PerformanceChart } from "@/components/views/PerformanceChart";
import "@/styles/dashboard.css";

interface PositionData {
  symbol: string;
  qty: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
}

interface DashboardViewProps {
  equity?: number;
  lastEquity?: number;
  positions?: PositionData[];
  isConnected?: boolean;
  agentStatuses?: {
    name?: string;
    agent_name?: string;
    score?: number;
    weight?: number;
    reasoning?: string;
    vetoed?: boolean;
    signal?: "BUY" | "SELL" | "HOLD" | string;
  }[];
}

const fmt = (v: number) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 }).format(v);

// AGENTS mock removed

// Removed inputSt in favor of Tailwind classes

export const DashboardView = ({ equity, lastEquity, positions = [], isConnected = false, agentStatuses }: DashboardViewProps) => {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  /* ÔöÇÔöÇ Strategy ÔöÇÔöÇ */
  const { data: strategyData, isLoading: isStrategyChecking } = useQuery({
    queryKey: ["strategy"],
    queryFn: fetchStrategy,
    refetchInterval: 5000,
  });

  const { data: benchmarkData, isLoading: isBenchmarkLoading } = useQuery({
    queryKey: ["benchmark-equity"],
    queryFn: fetchBenchmarkEquity,
    refetchInterval: 60000,
  });
  const strategy = strategyData?.strategy || "RLAgent";
  const strategyDesc = strategy === "RLAgent" ? "Reinforcement Learning Portfolio Manager"
                     : strategy === "LSTMDynamic" ? "LSTM Neural Network Prediction"
                     : "Lead Trading Strategy";
  const running = !!strategyData;
  const [isLoading, setIsLoading] = useState(false);

  // DEMO MODE GENERATOR: Only active if backend sends less than 2 points
  const chartData = useMemo(() => {
    let sourceData = benchmarkData?.points;
    if (!sourceData || sourceData.length < 2) {
      sourceData = generateDemoData();
    }
    return sourceData;
  }, [benchmarkData]);

  /* ÔöÇÔöÇ Risk limits ÔöÇÔöÇ */
  const [riskData, setRiskData] = useState<RiskLimits | null>(null);
  const [riskLoading, setRiskLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [maxDrawdown, setMaxDrawdown] = useState("5");
  const [maxPosition, setMaxPosition] = useState("20");

  useEffect(() => {
    let mounted = true;
    fetchRiskLimits().then((res) => {
      if (!mounted) return;
      if (res?.status === "success") {
        setRiskData(res);
        if (res.risk_limits?.max_daily_drawdown_pct) setMaxDrawdown(res.risk_limits.max_daily_drawdown_pct.toString());
        if (res.risk_limits?.max_position_size_pct)  setMaxPosition(res.risk_limits.max_position_size_pct.toString());
      }
      setRiskLoading(false);
    });
    return () => { mounted = false; };
  }, []);

  /* ÔöÇÔöÇ Recent trades ÔöÇÔöÇ */
  const [trades, setTrades] = useState<RecentTrade[]>([]);
  const [tradesLoading, setTradesLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      const data = await fetchRecentTrades(20);
      if (!mounted) return;
      setTrades(data.trades || []);
      setTradesLoading(false);
    };
    load();
    const iv = setInterval(load, 15000);
    return () => { mounted = false; clearInterval(iv); };
  }, []);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["strategy"] });

  const handleStartLive = async () => {
    setIsLoading(true);
    try {
      const data = await startLive();
      if (data.status === "success") { setRunning(true); invalidate(); toast({ title: "Live trading started" }); }
    } catch { toast({ title: "Engine not reachable", variant: "destructive" }); }
    setIsLoading(false);
  };

  const handleStop = async () => {
    setIsLoading(true);
    try {
      const data = await stop();
      if (data.status === "success") { setRunning(false); invalidate(); toast({ title: "Trading stopped" }); }
    } catch { toast({ title: "Engine not reachable", variant: "destructive" }); }
    setIsLoading(false);
  };

  const handlePanicSell = async () => {
    setIsLoading(true);
    try {
      const data = await panicSell();
      toast({
        title: data.status === "success" ? "All positions sold" : "Error",
        description: data.message,
        variant: data.status === "success" ? "default" : "destructive",
      });
      if (data.status === "success") invalidate();
    } catch { toast({ title: "Engine not reachable", variant: "destructive" }); }
    setIsLoading(false);
  };

  const handleToggleBot = async () => {
    if (!riskData) return;
    const newStatus = riskData.bot_status === "active" ? "inactive" : "active";
    setRiskData({ ...riskData, bot_status: newStatus });
    const res = await updateBotStatus(newStatus);
    if (res.status === "error") setRiskData({ ...riskData, bot_status: riskData.bot_status });
  };

  const handleSaveLimits = async () => {
    setSaving(true);
    await updateRiskLimits({
      max_daily_drawdown_pct: parseFloat(maxDrawdown),
      max_position_size_pct: parseFloat(maxPosition),
    });
    setSaving(false);
  };

  /* ├─ Computed ├─ */
  const STARTING_CAPITAL = 100000;
  const totalPnL = equity != null ? equity - STARTING_CAPITAL : null;
  const totalReturnPct = equity != null ? ((equity - STARTING_CAPITAL) / STARTING_CAPITAL) * 100 : null;
  const dailyReturnPct = equity != null && lastEquity != null && lastEquity > 0
    ? ((equity - lastEquity) / lastEquity) * 100 : null;
  const isEngineConnected = strategyData != null;

  /* ÔöÇÔöÇ Stat cards data ÔöÇÔöÇ */
  const stats = [
    { label: "Total Value",  value: equity     != null ? fmt(equity)     : "-", note: null,              col: null },
    { label: "Total P&L",    value: totalPnL   != null ? (totalPnL   >= 0 ? "+" : "") + fmt(totalPnL)   : "-",
      note: totalReturnPct != null ? (totalReturnPct >= 0 ? "+" : "") + totalReturnPct.toFixed(2) + "%" : null,
      col: totalPnL },
    { label: "Positions",    value: String(positions.length), note: isConnected ? "connected" : "offline", col: null },
    { label: "Daily",        value: dailyReturnPct != null ? (dailyReturnPct >= 0 ? "+" : "") + dailyReturnPct.toFixed(2) + "%" : "-",
      note: null, col: dailyReturnPct },
  ];

  const showRisk = riskData && riskData.status !== "error" && !riskLoading;

  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      className="min-h-screen pt-16 pb-12 px-6 dark-dashboard"
      style={{ maxWidth: 980, margin: "0 auto" }}
    >
      {/* Header */}
      <div className="pt-10 pb-6 flex justify-between items-center">
        <div>
          <div className="dash-overline">Dashboard</div>
          <h1 className="dash-title">Portfolio Overview</h1>
        </div>
        <div className="flex gap-2">
           {!isConnected && <span className="aa-pill" style={{ color: "#ff453a", borderColor: "rgba(255,69,58,0.3)" }}>Broker Offline</span>}
        </div>
      </div>

      {/* ÔöÇÔöÇ PERFORMANCE CHART ÔöÇÔöÇ */}
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="mb-5">
        <PerformanceChart
          data={chartData}
          isLoading={isBenchmarkLoading}
        />
      </motion.div>

      {/* ÔöÇÔöÇ ENGINE CONTROLS ÔöÇÔöÇ */}
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }} className="mb-5">
        <div className="surface-card p-5">
          {/* Header row */}
          <div className="flex items-center justify-between mb-4" style={{ borderBottom: "1px solid rgba(255,255,255,0.05)", paddingBottom: 14 }}>
            <div className="flex items-center gap-2">
              <ShieldAlert className="w-4 h-4 text-white/55" />
              <span className="text-[13px] font-semibold text-white/85 tracking-[-0.01em]">Trading Engine</span>
            </div>
            <div className="flex items-center gap-4">
              {/* Connection status */}
              <div className="flex items-center gap-1.5">
                {isStrategyChecking
                  ? <Loader2 style={{ width: 11, height: 11, color: "rgba(255,255,255,0.3)" }} className="animate-spin" />
                  : isEngineConnected
                    ? <CheckCircle style={{ width: 11, height: 11, color: "#30d158" }} />
                    : <XCircle    style={{ width: 11, height: 11, color: "#ff453a" }} />
                }
                <span className={`text-[11px] font-medium ${isEngineConnected ? "text-[#30d158]" : "text-[#ff453a]"}`}>
                  {isStrategyChecking ? "Checking" : isEngineConnected ? "Connected" : "Offline"}
                </span>
              </div>
              {/* Bot toggle */}
              {showRisk && (
                <div className="flex items-center gap-2">
                  <span className="text-[11px] font-medium text-white/30">Engine</span>
                  <button
                    className={"aa-toggle" + (riskData!.bot_status === "active" ? " on" : "")}
                    onClick={handleToggleBot}
                    aria-label="Toggle bot"
                  />
                  <span className={`text-[11px] font-bold ${riskData!.bot_status === "active" ? "text-[#30d158]" : "text-white/30"}`}>
                    {riskData!.bot_status === "active" ? "ACTIVE" : "PAUSED"}
                  </span>
                </div>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {/* Left: strategy + controls */}
            <div>
              <div className="text-[11px] font-semibold text-white/30 uppercase tracking-[0.05em] mb-2.5">Controls</div>
              <div className="flex gap-2 flex-wrap">
                {/* Start */}
                <button onClick={handleStartLive} disabled={isLoading || running} className="flex items-center gap-1.5" style={{
                  padding: "7px 14px", borderRadius: 8, fontSize: 12, fontWeight: 600,
                  background: isLoading || running ? "rgba(255,255,255,0.04)" : "rgba(48,209,88,0.1)",
                  border: "1px solid " + (isLoading || running ? "rgba(255,255,255,0.05)" : "rgba(48,209,88,0.3)"),
                  color: isLoading || running ? "rgba(255,255,255,0.3)" : "#30d158",
                  cursor: isLoading || running ? "not-allowed" : "pointer", transition: "all 0.2s",
                }}>
                  <Play className="w-3 h-3" /> Start Live
                </button>
                {/* Stop */}
                <button onClick={handleStop} disabled={isLoading || !running} className="flex items-center gap-1.5" style={{
                  padding: "7px 14px", borderRadius: 8, fontSize: 12, fontWeight: 600,
                  background: "transparent", border: "1px solid rgba(255,255,255,0.08)",
                  color: isLoading || !running ? "rgba(255,255,255,0.3)" : "rgba(255,255,255,0.85)",
                  cursor: isLoading || !running ? "not-allowed" : "pointer", transition: "all 0.2s",
                }}>
                  <Square className="w-3 h-3" /> Stop
                </button>
                {/* Panic */}
                <button onClick={handlePanicSell} disabled={isLoading} className="flex items-center gap-1.5" style={{
                  padding: "7px 14px", borderRadius: 8, fontSize: 12, fontWeight: 600,
                  background: "rgba(255,69,58,0.08)", border: "1px solid rgba(255,69,58,0.25)",
                  color: "#ff453a", cursor: isLoading ? "not-allowed" : "pointer", transition: "all 0.2s",
                }}>
                  <AlertTriangle className="w-3 h-3" /> Panic Sell
                </button>
              </div>
              {/* Status line removed per user request */}
            </div>

            {/* Right: risk limits or engine offline msg */}
            {showRisk ? (
              <div>
                <div className="text-[11px] font-semibold text-white/30 uppercase tracking-[0.05em] mb-2.5">Risk Limits</div>
                <div className="flex flex-col gap-3">
                  <div>
                    <label className="text-xs font-medium text-white/55 block mb-1">Max Daily Drawdown (%)</label>
                    <input type="number" value={maxDrawdown} onChange={(e) => setMaxDrawdown(e.target.value)} min="1" max="50" className="w-[120px] px-2.5 py-1.5 bg-white/5 border border-white/10 rounded-lg text-white/85 text-[13px] font-mono outline-none" />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-white/55 block mb-1">Max Position Size (%)</label>
                    <input type="number" value={maxPosition} onChange={(e) => setMaxPosition(e.target.value)} min="1" max="100" className="w-[120px] px-2.5 py-1.5 bg-white/5 border border-white/10 rounded-lg text-white/85 text-[13px] font-mono outline-none" />
                  </div>
                  <button onClick={handleSaveLimits} disabled={saving} className="flex items-center gap-1.5" style={{
                    padding: "6px 14px", borderRadius: 8, fontSize: 12, fontWeight: 600, width: "fit-content",
                    background: "rgba(212,168,83,0.08)", border: "1px solid rgba(212,168,83,0.2)",
                    color: "#d4a853", cursor: saving ? "not-allowed" : "pointer", transition: "all 0.2s",
                  }}>
                    {saving ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                    Save
                  </button>
                </div>
              </div>
            ) : !isEngineConnected && !isStrategyChecking ? (
              <div style={{ background: "rgba(255,255,255,0.03)", borderRadius: 10, padding: 16, border: "1px solid rgba(255,255,255,0.05)" }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "rgba(255,255,255,0.55)", marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
                  <Power className="w-3 h-3" /> Engine offline
                </div>
                <p style={{ fontSize: 12, color: "rgba(255,255,255,0.55)", lineHeight: 1.6, margin: 0 }}>
                  The trading engine isn't reachable right now. Please contact support if this persists.
                </p>
              </div>
            ) : null}
          </div>
        </div>
      </motion.div>

      {/* ÔöÇÔöÇ STAT CARDS ÔöÇÔöÇ */}
      <motion.div
        initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}
        className="grid grid-cols-2 lg:grid-cols-4 gap-2.5 mb-5"
      >
        {stats.map((c, i) => (
          <div key={i} className="surface-card p-5">
            <div className="text-[11px] font-semibold text-white/10 uppercase tracking-[0.04em] mb-1.5">
              {c.label}
            </div>
            <div className="stat-value" style={{ color: c.col != null ? (c.col >= 0 ? "#30d158" : "#ff453a") : "rgba(255,255,255,0.85)" }}>
              {c.value}
            </div>
            {c.note && <div style={{ fontSize: 11, color: "rgba(48,209,88,0.8)", fontWeight: 500, marginTop: 3 }}>{c.note}</div>}
          </div>
        ))}
      </motion.div>

      {/* ÔöÇÔöÇ POSITIONS + AGENTS ÔöÇÔöÇ */}
      <motion.div
        initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.25 }}
        style={{ display: "grid", gridTemplateColumns: positions.length > 0 ? "1.3fr 1fr" : "1fr", gap: 10, marginBottom: 20 }}
      >
        {/* Positions */}
        <div className="surface-card p-5">
          <div className="flex justify-between items-center mb-4">
            <span className="text-[13px] font-semibold text-white/85 tracking-[-0.01em]">Open Positions</span>
            <span className="text-[11px] font-medium text-[#d4a853]">{positions.length} open</span>
          </div>
          {positions.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Sym</th>
                    <th className="text-right">Qty</th>
                    <th className="text-right">Value</th>
                    <th className="text-right">P&amp;L</th>
                    <th className="text-right">%</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p) => (
                    <tr key={p.symbol}>
                      <td className="mono">{p.symbol}</td>
                      <td className="text-right text-white/30 text-[13px]">{p.qty.toFixed(2)}</td>
                      <td className="text-right text-[13px]">{fmt(p.market_value)}</td>
                      <td className={`text-right ${p.unrealized_pnl >= 0 ? "up" : "dn"}`}>
                        {p.unrealized_pnl >= 0 ? "+" : ""}{fmt(p.unrealized_pnl)}
                      </td>
                      <td className={`text-right ${p.unrealized_pnl_pct >= 0 ? "up" : "dn"}`}>
                        {p.unrealized_pnl_pct >= 0 ? "+" : ""}{p.unrealized_pnl_pct.toFixed(1)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p style={{ fontSize: 13, color: "rgba(255,255,255,0.3)", textAlign: "center", padding: "24px 0" }}>No open positions</p>
          )}
        </div>

        {/* Agents */}
        <div className="surface-card p-5">
          <div className="flex justify-between items-center mb-4">
            <span className="text-[13px] font-semibold text-white/85 tracking-[-0.01em]">Agent Status</span>
            <span className="text-[11px] font-medium text-white/30">{agentStatuses ? agentStatuses.length : 0} agents</span>
          </div>
          {(!agentStatuses || agentStatuses.length === 0) ? (
            <>
              <div className="mb-3 text-center" style={{ background: "rgba(255,255,255,0.03)", borderRadius: 6, padding: "6px 10px", border: "1px solid rgba(255,255,255,0.05)" }}>
                <span className="text-[11px] font-medium text-white/50">Market Closed • Agents Sleeping</span>
              </div>
              <div className="grid grid-cols-3 gap-1.5">
                {["DrawdownGuardAgent", "SpecialistAlphaAgent", "RegimeDetectionAgent", "MomentumAgent", "VIXAwareRiskAgent", "LSTMSignalAgent", "RLConfidenceAgent", "NewsSentimentAgent", "PatternRecognitionAgent"].map((name) => (
                  <div key={name} className="agent-card" style={{ opacity: 0.6 }}>
                    <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, fontWeight: 600, marginBottom: 3, color: "rgba(255,255,255,0.85)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={name}>{name}</div>
                    <div style={{ fontSize: 10, fontWeight: 500, color: "rgba(255,255,255,0.3)" }}>○ Sleeping</div>
                    <div style={{ fontSize: 10, color: "rgba(255,255,255,0.3)", fontWeight: 600, marginTop: 1 }}>Zzz...</div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="grid grid-cols-3 gap-1.5">
              {agentStatuses.map((ag) => {
                const name = "agent_name" in ag && ag.agent_name ? ag.agent_name : ag.name;
                const active = isEngineConnected && ("weight" in ag && ag.weight != null ? ag.weight > 0 : true);
                const sigColor = ag.signal === "BUY" ? "#30d158" : ag.signal === "SELL" ? "#ff453a" : "rgba(255,255,255,0.5)";
                return (
                  <div key={name} className="agent-card">
                    <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, fontWeight: 600, marginBottom: 3, color: "rgba(255,255,255,0.85)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={name}>{name}</div>
                    <div style={{ fontSize: 10, fontWeight: 500, color: active ? "#30d158" : "rgba(255,255,255,0.3)" }}>{active ? "● Active" : "○ Idle"}</div>
                    <div style={{ fontSize: 10, color: sigColor, fontWeight: 600, marginTop: 1 }}>{ag.signal}</div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </motion.div>

      {/* ÔöÇÔöÇ LATEST TRADES ÔöÇÔöÇ */}
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}>
        <div className="surface-card p-5">
          <div className="flex items-center gap-2 mb-4">
            <Clock className="w-3.5 h-3.5 text-white/55" />
            <span className="text-[13px] font-semibold text-white/85 tracking-[-0.01em]">Latest Trades</span>
          </div>
          {tradesLoading ? (
            <p className="text-xs text-white/30">LoadingÔÇª</p>
          ) : trades.length > 0 ? (
            <div className="data-table" style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    {["Time", "Action", "Symbol", "Qty", "Price"].map((h) => (
                      <th key={h} style={{ textAlign: "left", fontSize: 10, fontWeight: 600, color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.06em", paddingBottom: 8, paddingRight: 16 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => (
                    <tr key={t.id} style={{ borderTop: "1px solid rgba(255,255,255,0.05)" }}>
                      <td style={{ fontSize: 11, fontFamily: "JetBrains Mono, monospace", color: "rgba(255,255,255,0.35)", paddingTop: 7, paddingBottom: 7, paddingRight: 16, whiteSpace: "nowrap" }}>
                        {t.filled_at ? new Date(t.filled_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "-"}
                      </td>
                      <td style={{ paddingRight: 16, paddingTop: 7, paddingBottom: 7 }}>
                        <span style={{ fontSize: 10, fontFamily: "JetBrains Mono, monospace", fontWeight: 700, color: t.side === "buy" ? "#30d158" : "#ff453a", background: t.side === "buy" ? "rgba(48,209,88,0.1)" : "rgba(255,69,58,0.1)", padding: "2px 7px", borderRadius: 4 }}>
                          {t.side.toUpperCase()}
                        </span>
                      </td>
                      <td style={{ fontSize: 12, fontFamily: "JetBrains Mono, monospace", fontWeight: 600, color: "rgba(255,255,255,0.85)", paddingRight: 16, paddingTop: 7, paddingBottom: 7 }}>{t.symbol}</td>
                      <td style={{ fontSize: 12, fontFamily: "JetBrains Mono, monospace", color: "rgba(255,255,255,0.55)", paddingRight: 16, paddingTop: 7, paddingBottom: 7 }}>{t.qty}</td>
                      <td style={{ fontSize: 12, fontFamily: "JetBrains Mono, monospace", color: "rgba(255,255,255,0.85)", paddingTop: 7, paddingBottom: 7 }}>${t.price.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-xs text-white/30">No trades yet.</p>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
};
