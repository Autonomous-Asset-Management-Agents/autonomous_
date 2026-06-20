import { motion } from "framer-motion";
import { FileText, Newspaper } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const LogsView = () => {
  const systemLogs = [
    { time: "14:32:15", level: "INFO", message: "Portfolio rebalance completed. 2 positions adjusted." },
    { time: "14:30:01", level: "INFO", message: "Market data refresh successful." },
    { time: "14:25:45", level: "WARN", message: "GOOGL momentum declining. Monitoring closely." },
    { time: "14:20:00", level: "INFO", message: "AI analysis cycle completed. 156 symbols processed." },
    { time: "14:15:32", level: "INFO", message: "New position opened: META @ $506.75" },
    { time: "14:10:18", level: "DEBUG", message: "WebSocket connection stable. 847 messages received." },
  ];

  const newsItems = [
    { time: "14:28", source: "Reuters", headline: "Fed signals potential rate pause in upcoming meeting" },
    { time: "14:15", source: "Bloomberg", headline: "NVIDIA announces new AI chip partnership with major cloud providers" },
    { time: "14:02", source: "CNBC", headline: "Tech sector leads market gains amid strong earnings reports" },
    { time: "13:45", source: "WSJ", headline: "Apple expanding services revenue reaches record quarter" },
  ];

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="min-h-screen pt-20 sm:pt-24 pb-8 sm:pb-12 px-4 sm:px-8 md:px-16"
    >
      <div className="max-w-6xl mx-auto">
        <motion.h2
          className="font-display text-2xl sm:text-4xl md:text-5xl mb-6 sm:mb-12"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
        >
          Logs
        </motion.h2>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-8">
          {/* System Logs */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
          >
            <Card className="bg-card/50 border-border/50 backdrop-blur-sm h-full">
              <CardHeader className="p-4 sm:p-6">
                <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
                  <FileText className="w-4 h-4 sm:w-5 sm:h-5" />
                  System Logs
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 sm:p-6 pt-0">
                <div className="space-y-2 sm:space-y-3 font-mono text-xs sm:text-sm">
                  {systemLogs.map((log, i) => (
                    <div key={i} className="flex flex-wrap sm:flex-nowrap gap-1 sm:gap-3">
                      <span className="text-muted-foreground shrink-0">{log.time}</span>
                      <span className={`shrink-0 ${
                        log.level === 'WARN' ? 'text-warning' :
                        log.level === 'ERROR' ? 'text-destructive' :
                        log.level === 'DEBUG' ? 'text-muted-foreground' :
                        'text-foreground'
                      }`}>
                        [{log.level}]
                      </span>
                      <span className="text-foreground/80 break-all">{log.message}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </motion.div>

          {/* News Feed */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
          >
            <Card className="bg-card/50 border-border/50 backdrop-blur-sm h-full">
              <CardHeader className="p-4 sm:p-6">
                <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
                  <Newspaper className="w-4 h-4 sm:w-5 sm:h-5" />
                  News Feed
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 sm:p-6 pt-0">
                <div className="space-y-3 sm:space-y-4">
                  {newsItems.map((item, i) => (
                    <div key={i} className="border-l-2 border-border pl-3 sm:pl-4 py-1">
                      <div className="flex items-center gap-2 text-xs sm:text-sm text-muted-foreground mb-1">
                        <span>{item.time}</span>
                        <span>•</span>
                        <span className="text-chart-portfolio">{item.source}</span>
                      </div>
                      <p className="text-foreground text-sm sm:text-base">{item.headline}</p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </motion.div>
        </div>
      </div>
    </motion.div>
  );
};
