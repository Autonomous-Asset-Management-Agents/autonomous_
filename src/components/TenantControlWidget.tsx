import { useState, useEffect } from "react";
import { Power, ShieldAlert, Save, RefreshCw } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { fetchRiskLimits, updateRiskLimits, updateBotStatus, RiskLimits } from "@/lib/api";
import { motion } from "framer-motion";

export const TenantControlWidget = () => {
    const [data, setData] = useState<RiskLimits | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const [maxDrawdown, setMaxDrawdown] = useState<string>("5");
    const [maxPosition, setMaxPosition] = useState<string>("20");

    useEffect(() => {
        let mounted = true;
        const loadData = async () => {
            setLoading(true);
            const res = await fetchRiskLimits();
            if (!mounted) return;
            if (res && res.status === "success") {
                setData(res);
                if (res.risk_limits?.max_daily_drawdown_pct) {
                    setMaxDrawdown(res.risk_limits.max_daily_drawdown_pct.toString());
                }
                if (res.risk_limits?.max_position_size_pct) {
                    setMaxPosition(res.risk_limits.max_position_size_pct.toString());
                }
            }
            setLoading(false);
        };
        loadData();
        return () => { mounted = false; };
    }, []);

    const handleToggleBot = async () => {
        if (!data) return;
        const newStatus = data.bot_status === "active" ? "inactive" : "active";
        // Optimistic update
        setData({ ...data, bot_status: newStatus });

        const res = await updateBotStatus(newStatus);
        if (res.status === "error") {
            // Revert on error
            setData({ ...data, bot_status: data.bot_status });
            setError("Failed to update bot status");
        }
    };

    const handleSaveLimits = async () => {
        setSaving(true);
        setError(null);
        const payload = {
            max_daily_drawdown_pct: parseFloat(maxDrawdown),
            max_position_size_pct: parseFloat(maxPosition)
        };

        const res = await updateRiskLimits(payload);
        if (res.status !== "success") {
            setError("Failed to save risk limits");
        }
        setSaving(false);
    };

    if (loading) {
        return (
            <Card className="bg-card/50 border-border/50 backdrop-blur-sm animate-pulse h-48 mb-6 sm:mb-8"></Card>
        );
    }

    if (!data || data.status === "error") {
        // Hidden if not connected to a broker yet
        return null;
    }

    const isActive = data.bot_status === "active";

    return (
        <Card className="bg-card/50 border-border/50 backdrop-blur-sm mb-6 sm:mb-8">
            <CardHeader className="p-4 sm:p-6 pb-2 border-b border-border/30">
                <div className="flex items-center justify-between">
                    <CardTitle className="font-display text-lg sm:text-xl flex items-center gap-2">
                        <ShieldAlert className="w-5 h-5 text-primary" />
                        Trading Engine Controls
                    </CardTitle>
                    <div className="flex items-center gap-3">
                        <span className="text-sm font-medium text-muted-foreground mr-1">Engine Status:</span>
                        <button
                            onClick={handleToggleBot}
                            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 focus:ring-offset-background ${isActive ? 'bg-success' : 'bg-muted'}`}
                        >
                            <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${isActive ? 'translate-x-6' : 'translate-x-1'}`} />
                        </button>
                        <span className={`text-sm font-bold ${isActive ? 'text-success' : 'text-muted-foreground'}`}>
                            {isActive ? 'ACTIVE' : 'PAUSED'}
                        </span>
                    </div>
                </div>
            </CardHeader>

            <CardContent className="p-4 sm:p-6 pt-4 space-y-6">
                {error && (
                    <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-md text-destructive text-sm">
                        {error}
                    </div>
                )}

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div className="space-y-4">
                        <div>
                            <label className="text-sm font-medium block mb-1">Max Daily Drawdown (%)</label>
                            <p className="text-xs text-muted-foreground mb-2">The bot will halt trading if your portfolio falls by this percentage in a single day.</p>
                            <input
                                type="number"
                                value={maxDrawdown}
                                onChange={(e) => setMaxDrawdown(e.target.value)}
                                className="w-full sm:w-1/2 rounded-md border border-border/50 bg-background/50 px-3 py-2 text-sm md:w-full"
                                min="1" max="50"
                            />
                        </div>

                        <div>
                            <label className="text-sm font-medium block mb-1">Max Position Size (%)</label>
                            <p className="text-xs text-muted-foreground mb-2">Maximum percentage of total equity allocated to a single trade.</p>
                            <input
                                type="number"
                                value={maxPosition}
                                onChange={(e) => setMaxPosition(e.target.value)}
                                className="w-full sm:w-1/2 rounded-md border border-border/50 bg-background/50 px-3 py-2 text-sm md:w-full"
                                min="1" max="100"
                            />
                        </div>
                    </div>

                    <div className="bg-muted/30 p-4 rounded-lg border border-border/50 flex flex-col justify-between">
                        <div>
                            <h4 className="text-sm font-semibold mb-2 flex items-center gap-1.5">
                                <Power className="w-4 h-4 text-amber-500" /> Paper Trading Protection
                            </h4>
                            <p className="text-xs text-muted-foreground">
                                As a new user, your engine is currently locked to Paper Trading mode to protect your capital while discovering the system. You will receive real-time explanations if any trades are rejected due to Pattern Day Trader (PDT) limits or insufficient funds.
                            </p>
                        </div>
                        <motion.button
                            whileHover={{ scale: 1.02 }}
                            whileTap={{ scale: 0.98 }}
                            onClick={handleSaveLimits}
                            disabled={saving}
                            className="mt-4 w-full px-4 py-2 bg-secondary text-secondary-foreground font-medium text-sm rounded-md shadow-sm transition-colors hover:bg-secondary/80 flex items-center justify-center gap-2"
                        >
                            {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                            Save Configuration
                        </motion.button>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
};
