import { useState } from "react";
import { motion } from "framer-motion";
import { Play, Calendar } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";
import { runSimulation } from "@/lib/api";

export const SimulationView = () => {
  const [startDate, setStartDate] = useState("2024-01-01");
  const [endDate, setEndDate] = useState("2024-12-31");
  const [initialCapital, setInitialCapital] = useState("100000");
  const [symbolMode, setSymbolMode] = useState<"full_market" | "sp500">("sp500");
  const [isLoading, setIsLoading] = useState(false);
  const { toast } = useToast();

  const handleRunSimulation = async () => {
    setIsLoading(true);
    try {
      const data = await runSimulation({
        start_date: startDate,
        end_date: endDate,
        initial_capital: parseFloat(initialCapital),
        symbol_sample_mode: symbolMode,
      });
      toast({
        title: data.status === "success" ? "Simulation started" : "Error",
        description: data.message || "Running backtest...",
        variant: data.status === "success" ? "default" : "destructive",
      });
    } catch {
      toast({ title: "Engine not reachable", variant: "destructive" });
    }
    setIsLoading(false);
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="min-h-screen pt-20 sm:pt-24 pb-8 sm:pb-12 px-4 sm:px-8 md:px-16"
    >
      <div className="max-w-4xl mx-auto">
        <motion.h2
          className="font-display text-2xl sm:text-4xl md:text-5xl mb-6 sm:mb-12"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
        >
          Simulation
        </motion.h2>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <Card className="bg-card/50 border-border/50 backdrop-blur-sm">
            <CardHeader className="p-4 sm:p-6">
              <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
                <Calendar className="w-4 h-4 sm:w-5 sm:h-5" />
                Backtest Configuration
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 sm:space-y-6 p-4 sm:p-6 pt-0">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 sm:gap-6">
                <div className="space-y-2">
                  <Label htmlFor="start-date" className="text-sm">Start Date</Label>
                  <Input
                    id="start-date"
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                    className="bg-input border-border text-sm sm:text-base"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="end-date" className="text-sm">End Date</Label>
                  <Input
                    id="end-date"
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    className="bg-input border-border text-sm sm:text-base"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="capital" className="text-sm">Initial Capital ($)</Label>
                <Input
                  id="capital"
                  type="number"
                  value={initialCapital}
                  onChange={(e) => setInitialCapital(e.target.value)}
                  className="bg-input border-border text-sm sm:text-base"
                />
              </div>

              <div className="space-y-2">
                <Label className="text-sm">Symbol Universe</Label>
                <div className="flex flex-wrap gap-2 sm:gap-4">
                  <Button
                    variant={symbolMode === "sp500" ? "default" : "outline"}
                    onClick={() => setSymbolMode("sp500")}
                    className={`text-sm sm:text-base ${symbolMode === "sp500" ? "bg-foreground text-background hover:bg-foreground/90" : ""}`}
                  >
                    S&P 500
                  </Button>
                  <Button
                    variant={symbolMode === "full_market" ? "default" : "outline"}
                    onClick={() => setSymbolMode("full_market")}
                    className={`text-sm sm:text-base ${symbolMode === "full_market" ? "bg-foreground text-background hover:bg-foreground/90" : ""}`}
                  >
                    Full Market
                  </Button>
                </div>
              </div>

              <Button
                size="lg"
                onClick={handleRunSimulation}
                disabled={isLoading}
                className="w-full bg-foreground text-background hover:bg-foreground/90 text-sm sm:text-base"
              >
                <Play className="w-4 h-4 sm:w-5 sm:h-5 mr-2" />
                {isLoading ? "Running..." : "Run Simulation"}
              </Button>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </motion.div>
  );
};
