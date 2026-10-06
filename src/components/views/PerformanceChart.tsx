import React, { useState, useMemo } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts';
import { motion } from 'framer-motion';

export interface EquityPoint {
  date: string;
  equity: number;
}

interface PerformanceChartProps {
  data: EquityPoint[];
  isLoading: boolean;
}

type TimeRange = '1D' | '5D' | '1M' | '6M' | 'YTD' | '1Y' | '5Y' | 'Max';

const RANGES: TimeRange[] = ['1D', '5D', '1M', '6M', 'YTD', '1Y', '5Y', 'Max'];

export const PerformanceChart: React.FC<PerformanceChartProps> = ({ data, isLoading }) => {
  const [selectedRange, setSelectedRange] = useState<TimeRange>('1M');

  // Filter data based on selected range
  const filteredData = useMemo(() => {
    if (!data || data.length === 0) return [];
    
    const sortedData = [...data].sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());
    const latestDate = new Date(sortedData[sortedData.length - 1].date);
    
    let cutoffDate = new Date(latestDate);
    
    switch (selectedRange) {
      case '1D':
        cutoffDate.setDate(cutoffDate.getDate() - 1);
        break;
      case '5D':
        cutoffDate.setDate(cutoffDate.getDate() - 5);
        break;
      case '1M':
        cutoffDate.setMonth(cutoffDate.getMonth() - 1);
        break;
      case '6M':
        cutoffDate.setMonth(cutoffDate.getMonth() - 6);
        break;
      case 'YTD':
        cutoffDate = new Date(latestDate.getFullYear(), 0, 1);
        break;
      case '1Y':
        cutoffDate.setFullYear(cutoffDate.getFullYear() - 1);
        break;
      case '5Y':
        cutoffDate.setFullYear(cutoffDate.getFullYear() - 5);
        break;
      case 'Max':
      default:
        return sortedData;
    }
    
    // Filter and ensure we have at least some data, otherwise fallback to a few points if possible
    const filtered = sortedData.filter(d => new Date(d.date) >= cutoffDate);
    return filtered.length > 0 ? filtered : sortedData.slice(-2);
  }, [data, selectedRange]);

  const stats = useMemo(() => {
    if (filteredData.length === 0) return null;
    
    const equities = filteredData.map(d => d.equity);
    const high = Math.max(...equities);
    const low = Math.min(...equities);
    const open = equities[0];
    const prevClose = data.length > 1 ? data[data.length - 2].equity : open;
    
    return { high, low, open, prevClose };
  }, [filteredData, data]);

  const fmt = (v: number) =>
    new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 }).format(v);

  if (isLoading) {
    return (
      <div data-testid="chart-loading" className="w-full h-[400px] flex items-center justify-center bg-card/20 rounded-xl border border-border/50">
        <div className="animate-pulse flex flex-col items-center">
          <div className="h-4 w-32 bg-white/10 rounded mb-4"></div>
          <div className="h-64 w-[600px] bg-white/5 rounded"></div>
        </div>
      </div>
    );
  }

  if (filteredData.length === 0 || !stats) {
    return (
      <div className="w-full h-[400px] flex items-center justify-center bg-card/20 rounded-xl border border-border/50">
        <span className="text-white/40 text-sm">No data available</span>
      </div>
    );
  }

  const latestVal = filteredData[filteredData.length - 1].equity;

  return (
    <div className="w-full bg-[#111111] rounded-xl border border-white/10 p-5 font-sans">
      {/* Top Header: Filters & Title */}
      <div className="flex justify-between items-start mb-6">
        <div>
          <div className="text-3xl font-display font-medium text-white tracking-tight">
            {fmt(latestVal)}
          </div>
          <div className="text-xs text-white/50 mt-1 uppercase tracking-wider font-semibold">
            Portfolio Performance
          </div>
        </div>
        <div className="flex items-center gap-1 bg-[#1a1a1a] p-1 rounded-lg border border-white/5">
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setSelectedRange(r)}
              className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors ${
                selectedRange === r 
                  ? 'bg-[#2a2a2a] text-white shadow-sm' 
                  : 'text-white/40 hover:text-white/80'
              }`}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      {/* Chart */}
      <div className="h-[260px] w-full mt-4">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={filteredData} margin={{ top: 10, right: 0, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#30d158" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#30d158" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(255,255,255,0.05)" />
            <XAxis 
              dataKey="date" 
              hide 
            />
            <YAxis 
              domain={['auto', 'auto']} 
              hide 
            />
            <Tooltip 
              contentStyle={{ backgroundColor: '#1a1a1a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', color: '#fff', fontSize: '13px' }}
              itemStyle={{ color: '#30d158', fontWeight: 600 }}
              formatter={(value: number) => [fmt(value), 'Equity']}
              labelStyle={{ color: 'rgba(255,255,255,0.5)', marginBottom: '4px' }}
            />
            <Area 
              type="monotone" 
              dataKey="equity" 
              stroke="#30d158" 
              strokeWidth={2}
              fillOpacity={1} 
              fill="url(#colorEquity)" 
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Footer Stats */}
      <div className="grid grid-cols-4 gap-4 mt-8 pt-4 border-t border-white/10">
        <div>
          <div className="text-[11px] text-white/40 mb-1">Open</div>
          <div className="text-[13px] font-medium text-white/90">{fmt(stats.open)}</div>
        </div>
        <div>
          <div className="text-[11px] text-white/40 mb-1">High</div>
          <div className="text-[13px] font-medium text-white/90">{fmt(stats.high)}</div>
        </div>
        <div>
          <div className="text-[11px] text-white/40 mb-1">Low</div>
          <div className="text-[13px] font-medium text-white/90">{fmt(stats.low)}</div>
        </div>
        <div>
          <div className="text-[11px] text-white/40 mb-1">Prev close</div>
          <div className="text-[13px] font-medium text-white/90">{fmt(stats.prevClose)}</div>
        </div>
      </div>
    </div>
  );
};
