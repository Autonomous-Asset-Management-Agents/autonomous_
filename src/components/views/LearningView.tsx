import { useState } from "react";
import { motion } from "framer-motion";
import { GraduationCap, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";
import { runLearning } from "@/lib/api";

export const LearningView = () => {
  const [startDate, setStartDate] = useState("2023-01-01");
  const [endDate, setEndDate] = useState("2024-12-31");
  const [initialCapital, setInitialCapital] = useState("100000");
  const [isLoading, setIsLoading] = useState(false);
  const { toast } = useToast();

  const handleRunLearning = async () => {
    setIsLoading(true);
    try {
      const data = await runLearning({
        start_date: startDate,
        end_date: endDate,
        initial_capital: parseFloat(initialCapital),
      });
      toast({
        title: data.status === "success" ? "Learning started" : "Error",
        description: data.message || "Training the model...",
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
          Learning
        </motion.h2>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <Card className="bg-card/50 border-border/50 backdrop-blur-sm">
            <CardHeader className="p-4 sm:p-6">
              <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
                <GraduationCap className="w-4 h-4 sm:w-5 sm:h-5" />
                Model Training Configuration
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 sm:space-y-6 p-4 sm:p-6 pt-0">
              <p className="text-muted-foreground text-sm sm:text-base">
                Train the AI model on historical data to improve trading decisions.
              </p>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 sm:gap-6">
                <div className="space-y-2">
                  <Label htmlFor="learn-start-date" className="text-sm">Start Date</Label>
                  <Input
                    id="learn-start-date"
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                    className="bg-input border-border text-sm sm:text-base"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="learn-end-date" className="text-sm">End Date</Label>
                  <Input
                    id="learn-end-date"
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    className="bg-input border-border text-sm sm:text-base"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="learn-capital" className="text-sm">Initial Capital ($)</Label>
                <Input
                  id="learn-capital"
                  type="number"
                  value={initialCapital}
                  onChange={(e) => setInitialCapital(e.target.value)}
                  className="bg-input border-border text-sm sm:text-base"
                />
              </div>

              <Button
                size="lg"
                onClick={handleRunLearning}
                disabled={isLoading}
                className="w-full bg-foreground text-background hover:bg-foreground/90 text-sm sm:text-base"
              >
                <Play className="w-4 h-4 sm:w-5 sm:h-5 mr-2" />
                {isLoading ? "Training..." : "Run Learning"}
              </Button>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </motion.div>
  );
};
