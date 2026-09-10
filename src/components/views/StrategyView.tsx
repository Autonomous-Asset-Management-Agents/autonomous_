import { useState } from "react";
import { motion } from "framer-motion";
import { Play, Square, AlertTriangle, Activity } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/hooks/use-toast";
import { useQueryClient } from "@tanstack/react-query";
import { startLive, stop, panicSell, setStrategy as apiSetStrategy } from "@/lib/api";

interface StrategyViewProps {
  currentStrategy?: string;
  isRunning?: boolean;
}

export const StrategyView = ({
  currentStrategy = "RLAgent",
  isRunning = false,
}: StrategyViewProps) => {
  const [strategy, setStrategy] = useState(currentStrategy);
  const running = isRunning;
  const [isLoading, setIsLoading] = useState(false);
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["strategy"] });
  };

  const handleStartLive = async () => {
    setIsLoading(true);
    try {
      const data = await startLive();
      if (data.status === "success") {
        setRunning(true);
        invalidate();
        toast({ title: "Live trading started" });
      }
    } catch {
      toast({ title: "Engine not reachable", variant: "destructive" });
    }
    setIsLoading(false);
  };

  const handleStop = async () => {
    setIsLoading(true);
    try {
      const data = await stop();
      if (data.status === "success") {
        setRunning(false);
        invalidate();
        toast({ title: "Trading stopped" });
      }
    } catch {
      toast({ title: "Engine not reachable", variant: "destructive" });
    }
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
    } catch {
      toast({ title: "Engine not reachable", variant: "destructive" });
    }
    setIsLoading(false);
  };

  const handleSetStrategy = async (newStrategy: string) => {
    if (newStrategy !== "RLAgent" && newStrategy !== "LSTMDynamic") return;
    setIsLoading(true);
    try {
      const data = await apiSetStrategy(newStrategy as "RLAgent" | "LSTMDynamic");
      if (data.status === "success") {
        setStrategy(newStrategy);
        invalidate();
        toast({ title: `Strategy set to ${newStrategy}` });
      } else {
        toast({ title: "Error", description: data.message, variant: "destructive" });
      }
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
          Strategy Control
        </motion.h2>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="mb-4 sm:mb-8"
        >
          <Card className="bg-card/50 border-border/50 backdrop-blur-sm">
            <CardHeader className="p-4 sm:p-6">
              <CardTitle className="flex items-center gap-2 sm:gap-3 text-base sm:text-lg">
                <Activity className={`w-4 h-4 sm:w-5 sm:h-5 ${running ? "text-success animate-pulse-subtle" : "text-muted-foreground"}`} />
                Status
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 sm:p-6 pt-0">
              <div className="flex flex-wrap items-center gap-2 sm:gap-4">
                <div className={`w-2 h-2 sm:w-3 sm:h-3 rounded-full ${running ? "bg-success" : "bg-muted-foreground"}`} />
                <span className="font-display text-lg sm:text-xl">
                  {running ? "Live Trading Active" : "Stopped"}
                </span>
                <span className="text-muted-foreground text-sm sm:text-base">• {strategy}</span>
              </div>
            </CardContent>
          </Card>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="mb-4 sm:mb-8"
        >
          <Card className="bg-card/50 border-border/50 backdrop-blur-sm">
            <CardHeader className="p-4 sm:p-6">
              <CardTitle className="text-base sm:text-lg">Strategy Selection</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2 sm:gap-4 p-4 sm:p-6 pt-0">
              <Button
                variant={strategy === "RLAgent" ? "default" : "outline"}
                onClick={() => handleSetStrategy("RLAgent")}
                disabled={isLoading}
                className={`text-sm sm:text-base ${strategy === "RLAgent" ? "bg-foreground text-background hover:bg-foreground/90" : ""}`}
              >
                RL Agent
              </Button>
              <Button
                variant={strategy === "LSTMDynamic" ? "default" : "outline"}
                onClick={() => handleSetStrategy("LSTMDynamic")}
                disabled={isLoading}
                className={`text-sm sm:text-base ${strategy === "LSTMDynamic" ? "bg-foreground text-background hover:bg-foreground/90" : ""}`}
              >
                LSTM Dynamic
              </Button>
            </CardContent>
          </Card>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
        >
          <Card className="bg-card/50 border-border/50 backdrop-blur-sm">
            <CardHeader className="p-4 sm:p-6">
              <CardTitle className="text-base sm:text-lg">Controls</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2 sm:gap-4 p-4 sm:p-6 pt-0">
              <Button
                size="lg"
                onClick={handleStartLive}
                disabled={isLoading || running}
                className="bg-foreground text-background hover:bg-foreground/90 text-sm sm:text-base"
              >
                <Play className="w-4 h-4 sm:w-5 sm:h-5 mr-2" />
                Start Live
              </Button>

              <Button
                size="lg"
                variant="outline"
                onClick={handleStop}
                disabled={isLoading || !running}
                className="text-sm sm:text-base"
              >
                <Square className="w-4 h-4 sm:w-5 sm:h-5 mr-2" />
                Stop
              </Button>

              <Button
                size="lg"
                variant="destructive"
                onClick={handlePanicSell}
                disabled={isLoading}
                className="bg-destructive hover:bg-destructive/90 text-sm sm:text-base"
              >
                <AlertTriangle className="w-4 h-4 sm:w-5 sm:h-5 mr-2" />
                Panic Sell All
              </Button>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </motion.div>
  );
};
