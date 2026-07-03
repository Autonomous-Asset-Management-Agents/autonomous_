import { useQuery } from "@tanstack/react-query";
import {
    LineChart,
    Line,
    XAxis,
    YAxis,
    Tooltip,
    ResponsiveContainer,
    Legend,
} from "recharts";
import { fetchBenchmarkEquity } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useState } from "react";

type Range = "1m" | "3m" | "1y" | "all";

const RANGE_LABELS: Record<Range, string> = {
    "1m": "1M",
    "3m": "3M",
    "1y": "1Y",
    "all": "All",
};

function filterByRange(
    points: { date: string; equity: number }[],
    range: Range
) {
    if (range === "all" || points.length === 0) return points;
    const now = new Date();
    const cutoff = new Date(now);
    if (range === "1m") cutoff.setMonth(now.getMonth() - 1);
    else if (range === "3m") cutoff.setMonth(now.getMonth() - 3);
    else if (range === "1y") cutoff.setFullYear(now.getFullYear() - 1);
    return points.filter((p) => new Date(p.date) >= cutoff);
}

export const BenchmarkEquityChart = () => {
    const [range, setRange] = useState<Range>("all");

    const { data, isLoading } = useQuery({
        queryKey: ["benchmark-equity"],
        queryFn: fetchBenchmarkEquity,
        refetchInterval: 60_000,
        staleTime: 30_000,
    });

    const botPoints = filterByRange(data?.points ?? [], range);
    const spyPoints = filterByRange(data?.spy_points ?? [], range);

    // Merge into single array keyed by date
    const merged: Record<string, { date: string; bot?: number; spy?: number }> = {};
    botPoints.forEach((p) => {
        merged[p.date] = { ...merged[p.date], date: p.date, bot: p.equity };
    });
    spyPoints.forEach((p) => {
        merged[p.date] = { ...merged[p.date], date: p.date, spy: p.equity };
    });
    const chartData = Object.values(merged).sort(
        (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
    );

    const formatDate = (d: string) => {
        try {
            return new Date(d).toLocaleDateString("de-DE", {
                month: "short",
                year: "2-digit",
            });
        } catch {
            return d;
        }
    };

    const formatPct = (v: number) =>
        `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;

    return (
        <Card className="bg-card/50 border-border/50 backdrop-blur-sm">
            <CardHeader className="p-4 sm:p-6 pb-2">
                <div className="flex items-center justify-between flex-wrap gap-2">
                    <CardTitle className="font-display text-lg sm:text-xl">
                        Equity — Bot vs. S&P 500
                    </CardTitle>
                    <div className="flex gap-1">
                        {(Object.keys(RANGE_LABELS) as Range[]).map((r) => (
                            <button
                                key={r}
                                onClick={() => setRange(r)}
                                className={`px-2 py-1 text-xs rounded transition-colors ${range === r
                                        ? "bg-primary text-primary-foreground"
                                        : "text-muted-foreground hover:text-foreground"
                                    }`}
                            >
                                {RANGE_LABELS[r]}
                            </button>
                        ))}
                    </div>
                </div>
            </CardHeader>

            <CardContent className="p-4 sm:p-6 pt-0">
                {isLoading ? (
                    <div className="h-48 flex items-center justify-center text-muted-foreground text-sm">
                        Loading…
                    </div>
                ) : chartData.length === 0 ? (
                    <div className="h-48 flex items-center justify-center text-muted-foreground text-sm">
                        No equity data available yet
                    </div>
                ) : (
                    <ResponsiveContainer width="100%" height={220}>
                        <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
                            <XAxis
                                dataKey="date"
                                tickFormatter={formatDate}
                                tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                                axisLine={false}
                                tickLine={false}
                                interval="preserveStartEnd"
                            />
                            <YAxis
                                tickFormatter={formatPct}
                                tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                                axisLine={false}
                                tickLine={false}
                                width={48}
                            />
                            <Tooltip
                                contentStyle={{
                                    background: "hsl(var(--card))",
                                    border: "1px solid hsl(var(--border))",
                                    borderRadius: "8px",
                                    fontSize: 12,
                                }}
                                formatter={(v: number) => formatPct(v)}
                                labelFormatter={formatDate}
                            />
                            <Legend
                                wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
                                formatter={(value) =>
                                    value === "bot" ? "Bot" : "S&P 500"
                                }
                            />
                            <Line
                                type="monotone"
                                dataKey="bot"
                                stroke="hsl(var(--chart-portfolio, 210 100% 60%))"
                                strokeWidth={2}
                                dot={false}
                                activeDot={{ r: 4 }}
                            />
                            <Line
                                type="monotone"
                                dataKey="spy"
                                stroke="hsl(var(--muted-foreground))"
                                strokeWidth={1.5}
                                strokeDasharray="4 2"
                                dot={false}
                                activeDot={{ r: 4 }}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                )}
            </CardContent>
        </Card>
    );
};
