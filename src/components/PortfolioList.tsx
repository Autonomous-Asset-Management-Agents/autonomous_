import { motion } from "framer-motion";

export interface Position {
  symbol: string;
  market_value: number;
  unrealized_pnl_pct: number;
}

interface PortfolioListProps {
  positions: Position[];
  isLoading?: boolean;
  onSymbolClick?: (symbol: string) => void;
}

export const PortfolioList = ({ positions, isLoading, onSymbolClick }: PortfolioListProps) => {
  const formatValue = (value: number) => {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(value);
  };

  const formatPnl = (pnl: number) => {
    const sign = pnl >= 0 ? "+" : "";
    return `${sign}${pnl.toFixed(1)}%`;
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="flex items-baseline gap-3 animate-pulse">
            <div className="h-6 sm:h-8 w-16 sm:w-24 bg-muted rounded" />
            <div className="h-4 w-12 sm:w-16 bg-muted/50 rounded" />
          </div>
        ))}
      </div>
    );
  }

  if (!positions || positions.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">No positions</p>
    );
  }

  return (
    <div className="space-y-1">
      {positions.map((position, index) => (
        <motion.div
          key={position.symbol}
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{
            duration: 0.4,
            delay: 0.8 + index * 0.1,
            ease: [0.16, 1, 0.3, 1],
          }}
          role={onSymbolClick ? "button" : undefined}
          tabIndex={onSymbolClick ? 0 : undefined}
          onClick={() => onSymbolClick?.(position.symbol)}
          onKeyDown={(e) => onSymbolClick && (e.key === "Enter" || e.key === " ") && onSymbolClick(position.symbol)}
          className={`stock-item ${onSymbolClick ? "cursor-pointer hover:opacity-80" : ""}`}
        >
          <span className="stock-item-symbol">{position.symbol}</span>
          <span className="stock-item-value">
            {formatValue(position.market_value)}
          </span>
          <span
            className={`stock-item-value ${
              position.unrealized_pnl_pct >= 0
                ? "stock-item-positive"
                : "stock-item-negative"
            }`}
          >
            {formatPnl(position.unrealized_pnl_pct)}
          </span>
        </motion.div>
      ))}
    </div>
  );
};
