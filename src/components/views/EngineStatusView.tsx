import { motion } from "framer-motion";
import { Power, CheckCircle, XCircle, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useQuery } from "@tanstack/react-query";
import { fetchStrategy, API_BASE } from "@/lib/api";

export const EngineStatusView = () => {
  const { data: strategyData, isLoading: isChecking } = useQuery({
    queryKey: ["strategy"],
    queryFn: fetchStrategy,
    refetchInterval: 5000,
  });

  const isConnected = strategyData != null;
  const strategy = strategyData?.strategy ?? null;

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
          Engine Status
        </motion.h2>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <Card className="bg-card/50 border-border/50 backdrop-blur-sm">
            <CardHeader className="p-4 sm:p-6">
              <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
                <Power className="w-4 h-4 sm:w-5 sm:h-5" />
                Connection Status
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 sm:space-y-6 p-4 sm:p-6 pt-0">
              <div className="flex items-center gap-3 sm:gap-4">
                {isChecking ? (
                  <Loader2 className="w-6 h-6 sm:w-8 sm:h-8 animate-spin text-muted-foreground" />
                ) : isConnected ? (
                  <CheckCircle className="w-6 h-6 sm:w-8 sm:h-8 text-success" />
                ) : (
                  <XCircle className="w-6 h-6 sm:w-8 sm:h-8 text-destructive" />
                )}
                <div>
                  <p className="font-display text-lg sm:text-2xl">
                    {isChecking
                      ? "Checking..."
                      : isConnected
                        ? "Engine Connected"
                        : "Engine Not Running"
                    }
                  </p>
                  <p className="text-muted-foreground text-sm sm:text-base">
                    {isConnected
                      ? `Active strategy: ${strategy}`
                      : "Start the engine to begin trading"
                    }
                  </p>
                </div>
              </div>

              {!isConnected && !isChecking && (
                <div className="bg-muted/50 rounded-lg p-4 sm:p-6 space-y-3 sm:space-y-4">
                  <p className="font-display text-base sm:text-lg">How to start the engine:</p>
                  <ol className="list-decimal list-inside space-y-1 sm:space-y-2 text-muted-foreground text-sm sm:text-base">
                    <li>From <code className="bg-background px-2 py-1 rounded text-xs">ai_trading_bot</code> folder run: <code className="bg-background px-2 py-1 rounded text-xs sm:text-sm">python -m core.engine</code></li>
                    <li>Engine runs on port 8001 (or next free port). This page will detect it.</li>
                    <li>If your engine uses another port, add <code className="bg-background px-2 py-1 rounded text-xs">?engine_port=8002</code> to the URL and refresh.</li>
                  </ol>
                </div>
              )}

              {isConnected && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                  <div className="bg-muted/30 rounded-lg p-3 sm:p-4">
                    <p className="text-xs sm:text-sm text-muted-foreground">API Endpoint</p>
                    <p className="font-mono text-sm sm:text-base break-all">{API_BASE}</p>
                  </div>
                  <div className="bg-muted/30 rounded-lg p-3 sm:p-4">
                    <p className="text-xs sm:text-sm text-muted-foreground">Strategy</p>
                    <p className="font-display text-sm sm:text-base">{strategy}</p>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </motion.div>
  );
};
