import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { ArrowLeft, TrendingUp, TrendingDown } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  ReferenceLine,
} from "recharts";
import { fetchStockHistory, type StockHistoryRange } from "@/lib/api";

const RANGES: { key: StockHistoryRange; label: string }[] = [
  { key: "1d", label: "1 Day" },
  { key: "1w", label: "1 Week" },
  { key: "1m", label: "1 Month" },
  { key: "1y", label: "1 Year" },
  { key: "max", label: "All" },
];

interface StockDetailViewProps {
  symbol: string;
  /** Current market value and PnL % from portfolio (optional) */
  marketValue?: number;
  unrealizedPnlPct?: number;
  onBack: () => void;
}

export const StockDetailView = ({
  symbol,
  marketValue,
  unrealizedPnlPct,
  onBack,
}: StockDetailViewProps) => {
  const [period, setPeriod] = useState<StockHistoryRange>("1m");

  const { data: historyResponse, isLoading } = useQuery({
    queryKey: ["stock-history", symbol, period],
    queryFn: () => fetchStockHistory(symbol, period),
    staleTime: 60 * 1000,
  });

  const rawData = historyResponse?.status === "success" ? historyResponse.data ?? [] : [];
  const isIntraday = historyResponse?.intraday ?? false;
  const data = [...rawData].sort((a, b) => a.date.localeCompare(b.date));
  
  // Format labels: for intraday show time (HH:MM), for daily show date (MM-DD)
  const chartData = data.map((d) => {
    let label: string;
    if (isIntraday && d.date.includes("T")) {
      // Intraday: "2026-02-20T14:15" -> "14:15"
      label = d.date.split("T")[1] || d.date.slice(5, 10);
    } else {
      // Daily: "2026-02-20" -> "02-20"
      label = d.date.slice(5, 10);
    }
    return { ...d, dateLabel: label };
  });

  const firstClose = chartData.length > 0 ? chartData[0].close : 0;
  const lastClose = chartData.length > 0 ? chartData[chartData.length - 1].close : 0;
  const isPositive = lastClose >= firstClose;
  const changePct = firstClose > 0 ? ((lastClose - firstClose) / firstClose) * 100 : 0;

  const displayPrice = data.length > 0 ? lastClose : null;
  const displayPct = data.length > 0 ? changePct : unrealizedPnlPct ?? null;

  // Calculate high/low with validation (handle potential data issues like stock splits)
  const rawHigh = data.length ? Math.max(...data.map((d) => d.high)) : null;
  const rawLow = data.length ? Math.min(...data.map((d) => d.low)) : null;
  
  // Validate: if low > high, there's bad data (e.g. stock split mixing) - use close prices as fallback
  const hasValidHighLow = rawHigh !== null && rawLow !== null && rawLow <= rawHigh;
  const high = hasValidHighLow ? rawHigh : (data.length ? Math.max(...data.map((d) => d.close)) : null);
  const low = hasValidHighLow ? rawLow : (data.length ? Math.min(...data.map((d) => d.close)) : null);
  const open = data.length ? data[0].open : null;
  const volume = data.length ? data.reduce((sum, d) => sum + d.volume, 0) : null;
  const prevClose = data.length >= 2 ? data[data.length - 2].close : data[0]?.close ?? null;
  const hasEnoughFor52W = data.length >= 200;
  const high52 = hasEnoughFor52W ? high : null;
  const low52 = hasEnoughFor52W ? low : null;

  const formatCurrency = (value: number) =>
    new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);

  const formatVolume = (v: number) => {
    if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
    if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(2)}K`;
    return String(v);
  };

  const chartColor = isPositive ? "hsl(var(--success))" : "hsl(var(--destructive))";
  const chartConfig = {
    close: { label: "Close", color: chartColor },
    previousClose: { label: "Previous close", color: "hsl(var(--muted-foreground))" },
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="min-h-screen pt-20 sm:pt-24 pb-8 sm:pb-12 px-4 sm:px-8 md:px-16"
    >
      <div className="max-w-5xl mx-auto">
        <Button
          variant="ghost"
          size="sm"
          className="mb-4 sm:mb-6 -ml-2 text-muted-foreground hover:text-foreground"
          onClick={onBack}
        >
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back
        </Button>

        <div className="mb-4 sm:mb-6">
          <h1 className="font-display text-2xl sm:text-4xl md:text-5xl tracking-tight">
            {symbol}
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">Stock · USD</p>
        </div>

        <div className="flex flex-wrap items-baseline gap-3 sm:gap-4 mb-6 sm:mb-8">
          <span className="font-display text-2xl sm:text-3xl md:text-4xl">
            {displayPrice != null ? formatCurrency(displayPrice) : "—"}
          </span>
          {displayPct != null && (
            <span
              className={`inline-flex items-center gap-1 text-sm sm:text-base font-medium ${
                displayPct >= 0 ? "text-success" : "text-destructive"
              }`}
            >
              {displayPct >= 0 ? (
                <TrendingUp className="w-4 h-4" />
              ) : (
                <TrendingDown className="w-4 h-4" />
              )}
              {displayPct >= 0 ? "+" : ""}
              {displayPct.toFixed(2)}%
            </span>
          )}
          <span className="text-xs sm:text-sm text-muted-foreground">
            {new Date().toLocaleString("en-US", {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
              timeZoneName: "short",
            })}
          </span>
        </div>

        {/* Time range buttons */}
        <div className="flex flex-wrap gap-1 sm:gap-2 mb-4 sm:mb-6">
          {RANGES.map(({ key, label }) => (
            <Button
              key={key}
              variant={period === key ? "secondary" : "ghost"}
              size="sm"
              className="text-xs sm:text-sm"
              onClick={() => setPeriod(key)}
            >
              {label}
            </Button>
          ))}
        </div>

        <Card className="bg-card/50 border-border/50 backdrop-blur-sm overflow-hidden">
          <CardContent className="p-0 pt-4 sm:pt-6">
            {isLoading ? (
              <div className="h-[280px] sm:h-[320px] flex items-center justify-center text-muted-foreground text-sm">
                Loading chart…
              </div>
            ) : chartData.length === 0 ? (
              <div className="h-[280px] sm:h-[320px] flex items-center justify-center text-muted-foreground text-sm text-center px-4">
                {period === "1d"
                  ? "No intraday data available. Market may be closed."
                  : "No price data for this range. Start the engine for live data."}
              </div>
            ) : (
              <ChartContainer config={chartConfig} className="h-[280px] sm:h-[320px] w-full aspect-video">
                <AreaChart data={chartData} margin={{ top: 8, right: 8, left: 8, bottom: 8 }}>
                  <defs>
                    <linearGradient
                      id="fillPrice"
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop offset="0%" stopColor={chartColor} stopOpacity={0.35} />
                      <stop offset="100%" stopColor={chartColor} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                  <XAxis
                    dataKey="dateLabel"
                    tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
                    tickLine={false}
                    axisLine={false}
                    interval="preserveStartEnd"
                    minTickGap={30}
                  />
                  <YAxis
                    domain={["auto", "auto"]}
                    tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
                    tickLine={false}
                    axisLine={false}
                    tickFormatter={(v) => (v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v.toFixed(0))}
                  />
                  <ChartTooltip
                    content={
                      <ChartTooltipContent
                        labelFormatter={(v) => v}
                        formatter={(value) => [formatCurrency(Number(value)), "Close"]}
                      />
                    }
                  />
                  {prevClose != null && (
                    <ReferenceLine
                      y={prevClose}
                      stroke="hsl(var(--muted-foreground))"
                      strokeDasharray="4 4"
                      strokeOpacity={0.8}
                    />
                  )}
                  <Area
                    type="monotone"
                    dataKey="close"
                    stroke={chartColor}
                    strokeWidth={2}
                    fill="url(#fillPrice)"
                  />
                </AreaChart>
              </ChartContainer>
            )}
          </CardContent>
        </Card>

        {/* Key stats */}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 sm:gap-4 mt-6 sm:mt-8">
          {[
            { label: "High", value: high != null ? formatCurrency(high) : "—" },
            { label: "Open", value: open != null ? formatCurrency(open) : "—" },
            { label: "Low", value: low != null ? formatCurrency(low) : "—" },
            { label: "Vol.", value: volume != null ? formatVolume(volume) : "—" },
            { label: "52W High", value: high52 != null ? formatCurrency(high52) : "—" },
            { label: "52W Low", value: low52 != null ? formatCurrency(low52) : "—" },
          ].map(({ label, value }) => (
            <Card key={label} className="bg-card/50 border-border/50">
              <CardHeader className="p-3 sm:p-4 pb-0">
                <CardTitle className="text-xs sm:text-sm font-normal text-muted-foreground">
                  {label}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-3 sm:p-4 pt-1">
                <p className="font-display text-base sm:text-lg">{value}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </motion.div>
  );
};
