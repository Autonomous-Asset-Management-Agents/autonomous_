import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, TrendingUp, TrendingDown, Sparkles, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { API_BASE } from "@/lib/api";

interface StockRecommendation {
  symbol: string;
  score: number;
  momentum?: number;
  conviction?: number;
  price?: number;
  change_pct?: number;
}

interface StockRecommendationsProps {
  isOpen: boolean;
  onClose: () => void;
}

export const StockRecommendations = ({ isOpen, onClose }: StockRecommendationsProps) => {
  const [recommendations, setRecommendations] = useState<StockRecommendation[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchRecommendations = async () => {
    setIsLoading(true);
    setError(null);

    try {
      // 1) Prefer engine scanner top picks (real LSTM/scanner top 10)
      const topPicksRes = await fetch(`${API_BASE}/top-picks`);
      const topPicksData = await topPicksRes.json();
      if (topPicksData.status === "success" && topPicksData.picks?.length) {
        type PickItem = { symbol?: string; trending_score?: number; breakout_score?: number; ranging_score?: number };
const picks = topPicksData.picks.slice(0, 10).map((p: PickItem, i: number) => ({
          symbol: p.symbol,
          score: (p.trending_score != null || p.breakout_score != null)
            ? ((p.trending_score ?? 0) + (p.breakout_score ?? 0)) / 100
            : (10 - i) / 10,
          momentum: p.trending_score != null ? p.trending_score / 100 : undefined,
          conviction: p.ranging_score != null ? p.ranging_score / 100 : undefined,
          change_pct: undefined,
        }));
        setRecommendations(picks);
        setLastUpdated(new Date());
        setIsLoading(false);
        return;
      }

      // 2) Portfolio positions: top 10 by total_score, or by market_value if no scores
      const portfolioRes = await fetch(`${API_BASE}/portfolio-summary`);
      const portfolioData = await portfolioRes.json();

      if (portfolioData.status === "success" && portfolioData.positions?.length) {
        type PositionItem = { symbol?: string; total_score?: number; market_value?: number; momentum_score?: number; conviction_score?: number; qty?: number; unrealized_pnl_pct?: number };
        const positions = portfolioData.positions as PositionItem[];
        const withScores = positions.filter((p: PositionItem) => p.total_score != null);
        const sorted =
          withScores.length > 0
            ? [...withScores].sort((a, b) => (b.total_score || 0) - (a.total_score || 0)).slice(0, 10)
            : [...positions].sort((a, b) => (b.market_value || 0) - (a.market_value || 0)).slice(0, 10);
        const mapped = sorted.map((p: PositionItem) => ({
          symbol: p.symbol,
          score: p.total_score ?? (p.market_value ? 0.5 : 0),
          momentum: p.momentum_score,
          conviction: p.conviction_score,
          price: p.qty ? p.market_value / p.qty : undefined,
          change_pct: p.unrealized_pnl_pct,
        }));
        if (mapped.length > 0) {
          setRecommendations(mapped);
          setLastUpdated(new Date());
          setIsLoading(false);
          return;
        }
      }

      // 3) No real data - don't show generic mock list
      setRecommendations([]);
      setError("No top picks yet. Start the engine and run live or a simulation to get scanner rankings.");
    } catch (e) {
      setError("Could not connect to engine. Make sure it's running (default port 8001).");
      setRecommendations([]);
    }

    setIsLoading(false);
  };

  useEffect(() => {
    if (isOpen) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      fetchRecommendations();
    }
  }, [isOpen]);

  const formatScore = (score: number) => `${(score * 100).toFixed(0)}`;

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0, y: "100%" }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: "100%" }}
          transition={{ type: "spring", damping: 25, stiffness: 300 }}
          className="fixed bottom-0 left-0 right-0 h-[60vh] sm:h-[50vh] bg-card/95 backdrop-blur-lg border-t border-border z-50 flex flex-col rounded-t-2xl"
        >
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-border">
            <div className="flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-chart-portfolio" />
              <span className="font-display text-lg">LSTM Top 10 Picks</span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                onClick={fetchRecommendations}
                disabled={isLoading}
              >
                <RefreshCw className={`w-4 h-4 ${isLoading ? "animate-spin" : ""}`} />
              </Button>
              <Button variant="ghost" size="icon" onClick={onClose}>
                <X className="w-5 h-5" />
              </Button>
            </div>
          </div>

          {/* Content */}
          <ScrollArea className="flex-1 p-4">
            {isLoading ? (
              <div className="flex items-center justify-center h-full">
                <div className="flex items-center gap-3 text-muted-foreground">
                  <Loader2 className="w-6 h-6 animate-spin" />
                  <span>Fetching LSTM rankings...</span>
                </div>
              </div>
            ) : error ? (
              <div className="flex items-center justify-center h-full">
                <p className="text-muted-foreground text-center">{error}</p>
              </div>
            ) : recommendations.length === 0 ? (
              <div className="flex items-center justify-center h-full">
                <p className="text-muted-foreground text-center">
                  No recommendations yet. Run a simulation to generate LSTM rankings.
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {recommendations.map((stock, index) => (
                  <motion.div
                    key={stock.symbol}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: index * 0.05 }}
                    className="flex items-center justify-between p-3 bg-muted/30 rounded-lg hover:bg-muted/50 transition-colors"
                  >
                    <div className="flex items-center gap-4">
                      <span className="text-muted-foreground text-sm w-6">#{index + 1}</span>
                      <div>
                        <span className="font-display text-lg">{stock.symbol}</span>
                        <div className="flex gap-3 text-xs text-muted-foreground">
                          {stock.momentum !== undefined && (
                            <span>Mom: {formatScore(stock.momentum)}</span>
                          )}
                          {stock.conviction !== undefined && (
                            <span>Conv: {formatScore(stock.conviction)}</span>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-4">
                      <div className="text-right">
                        <div className="font-display text-xl text-chart-portfolio">
                          {formatScore(stock.score)}
                        </div>
                        <div className="text-xs text-muted-foreground">Score</div>
                      </div>

                      {stock.change_pct !== undefined && (
                        <div
                          className={`flex items-center gap-1 min-w-[70px] justify-end ${
                            stock.change_pct >= 0 ? "text-success" : "text-destructive"
                          }`}
                        >
                          {stock.change_pct >= 0 ? (
                            <TrendingUp className="w-4 h-4" />
                          ) : (
                            <TrendingDown className="w-4 h-4" />
                          )}
                          <span className="text-sm font-medium">
                            {stock.change_pct >= 0 ? "+" : ""}
                            {stock.change_pct.toFixed(1)}%
                          </span>
                        </div>
                      )}
                    </div>
                  </motion.div>
                ))}
              </div>
            )}
          </ScrollArea>

          {/* Footer */}
          {lastUpdated && (
            <div className="p-3 border-t border-border text-center text-xs text-muted-foreground">
              Last updated: {lastUpdated.toLocaleTimeString()}
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
};
