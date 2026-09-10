import { motion } from "framer-motion";
import { Briefcase, TrendingUp, TrendingDown } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Position } from "@/lib/api";

interface PortfolioViewProps {
  positions?: Position[];
  equity?: number;
  startingCapital?: number;
}

export const PortfolioView = ({ positions = [], equity, startingCapital = 100000 }: PortfolioViewProps) => {
  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
    }).format(value);
  };

  const totalValue = equity ?? positions.reduce((sum, p) => sum + p.market_value, 0);
  const totalPnL = equity != null ? equity - startingCapital : positions.reduce((sum, p) => sum + (p.unrealized_pnl ?? 0), 0);

  if (positions.length === 0) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="min-h-screen pt-20 sm:pt-24 pb-8 sm:pb-12 px-4 sm:px-8 md:px-16"
      >
        <div className="max-w-7xl mx-auto">
          <motion.h2
            className="font-display text-2xl sm:text-4xl md:text-5xl mb-6 sm:mb-12"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
          >
            Portfolio
          </motion.h2>
          <Card className="bg-card/50 border-border/50 backdrop-blur-sm">
            <CardContent className="py-8 sm:py-12">
              <p className="text-muted-foreground text-center text-sm sm:text-base">No positions. Start the engine and run live or simulation to see holdings.</p>
            </CardContent>
          </Card>
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="min-h-screen pt-20 sm:pt-24 pb-8 sm:pb-12 px-4 sm:px-8 md:px-16"
    >
      <div className="max-w-7xl mx-auto">
        <motion.h2
          className="font-display text-2xl sm:text-4xl md:text-5xl mb-6 sm:mb-12"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
        >
          Portfolio
        </motion.h2>

        {/* Summary */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="grid grid-cols-2 md:grid-cols-3 gap-3 sm:gap-6 mb-4 sm:mb-8"
        >
          <Card className="bg-card/50 border-border/50 backdrop-blur-sm">
            <CardHeader className="pb-1 sm:pb-2 p-3 sm:p-6">
              <CardTitle className="text-xs sm:text-sm font-normal text-muted-foreground flex items-center gap-1 sm:gap-2">
                <Briefcase className="w-3 h-3 sm:w-4 sm:h-4" />
                Total Value
              </CardTitle>
            </CardHeader>
            <CardContent className="p-3 sm:p-6 pt-0">
              <p className="font-display text-lg sm:text-2xl">{formatCurrency(totalValue)}</p>
            </CardContent>
          </Card>

          <Card className="bg-card/50 border-border/50 backdrop-blur-sm">
            <CardHeader className="pb-1 sm:pb-2 p-3 sm:p-6">
              <CardTitle className="text-xs sm:text-sm font-normal text-muted-foreground flex items-center gap-1 sm:gap-2">
                {totalPnL >= 0 ? <TrendingUp className="w-3 h-3 sm:w-4 sm:h-4 text-success" /> : <TrendingDown className="w-3 h-3 sm:w-4 sm:h-4 text-destructive" />}
                {equity != null ? "Total P&L" : "Unrealized P&L"}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-3 sm:p-6 pt-0">
              <p className={`font-display text-lg sm:text-2xl ${totalPnL >= 0 ? 'text-success' : 'text-destructive'}`}>
                {totalPnL >= 0 ? '+' : ''}{formatCurrency(totalPnL)}
              </p>
            </CardContent>
          </Card>

          <Card className="bg-card/50 border-border/50 backdrop-blur-sm col-span-2 md:col-span-1">
            <CardHeader className="pb-1 sm:pb-2 p-3 sm:p-6">
              <CardTitle className="text-xs sm:text-sm font-normal text-muted-foreground">
                Positions
              </CardTitle>
            </CardHeader>
            <CardContent className="p-3 sm:p-6 pt-0">
              <p className="font-display text-lg sm:text-2xl">{positions.length}</p>
            </CardContent>
          </Card>
        </motion.div>

        {/* Positions Table */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          <Card className="bg-card/50 border-border/50 backdrop-blur-sm overflow-hidden">
            <CardHeader className="p-4 sm:p-6">
              <CardTitle className="text-base sm:text-lg">Positions</CardTitle>
            </CardHeader>
            <CardContent className="p-0 sm:p-6 sm:pt-0">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="border-border/50">
                      <TableHead className="text-xs sm:text-sm">Symbol</TableHead>
                      <TableHead className="text-right text-xs sm:text-sm">Qty</TableHead>
                      <TableHead className="text-right text-xs sm:text-sm hidden sm:table-cell">Value</TableHead>
                      <TableHead className="text-right text-xs sm:text-sm">P&L</TableHead>
                      <TableHead className="text-right text-xs sm:text-sm hidden md:table-cell">P&L %</TableHead>
                      <TableHead className="text-right text-xs sm:text-sm hidden lg:table-cell">Score</TableHead>
                      <TableHead className="text-right text-xs sm:text-sm hidden lg:table-cell">Days</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {positions.map((position) => (
                      <TableRow key={position.symbol} className="border-border/30">
                        <TableCell className="font-display text-sm sm:text-lg">{position.symbol}</TableCell>
                        <TableCell className="text-right text-xs sm:text-sm">{position.qty}</TableCell>
                        <TableCell className="text-right text-xs sm:text-sm hidden sm:table-cell">{formatCurrency(position.market_value)}</TableCell>
                        <TableCell className={`text-right text-xs sm:text-sm ${position.unrealized_pnl >= 0 ? 'text-success' : 'text-destructive'}`}>
                          {position.unrealized_pnl >= 0 ? '+' : ''}{formatCurrency(position.unrealized_pnl)}
                        </TableCell>
                        <TableCell className={`text-right text-xs sm:text-sm hidden md:table-cell ${position.unrealized_pnl_pct >= 0 ? 'text-success' : 'text-destructive'}`}>
                          {position.unrealized_pnl_pct >= 0 ? '+' : ''}{position.unrealized_pnl_pct.toFixed(1)}%
                        </TableCell>
                        <TableCell className="text-right text-xs sm:text-sm hidden lg:table-cell">{((position.total_score ?? 0) * 100).toFixed(0)}</TableCell>
                        <TableCell className="text-right text-muted-foreground text-xs sm:text-sm hidden lg:table-cell">{position.days_held ?? "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </motion.div>
  );
};
