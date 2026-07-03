import { useState, useEffect } from "react";
import { Link, CheckCircle, AlertCircle, Terminal } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

interface BrokerConnectionWidgetProps {
    isConnected: boolean;
}

export const BrokerConnectionWidget = ({ isConnected }: BrokerConnectionWidgetProps) => {
    const [envError, setEnvError] = useState<string | null>(null);

    // Check for ?error= in URL (e.g. leftover redirect from old OAuth flow)
    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        const err = params.get("error");
        if (err) {
            setTimeout(() => setEnvError(`Connection error: ${err}`), 0);
            window.history.replaceState({}, document.title, window.location.pathname);
        }
    }, []);

    return (
        <Card className="bg-card/50 border-border/50 backdrop-blur-sm overflow-hidden mb-6 sm:mb-8">
            <CardHeader className="p-4 sm:p-6 pb-2">
                <div className="flex items-center justify-between">
                    <div>
                        <CardTitle className="font-display text-lg sm:text-xl flex items-center gap-2">
                            <Link className="w-5 h-5 text-primary" />
                            Broker Connection
                        </CardTitle>
                        <CardDescription className="mt-1">
                            Connect your Alpaca broker account to enable the AI Trading Engine for your portfolio.
                        </CardDescription>
                    </div>
                    {isConnected ? (
                        <div className="flex items-center gap-2 text-success bg-success/10 px-3 py-1.5 rounded-full border border-success/20">
                            <CheckCircle className="w-4 h-4" />
                            <span className="text-sm font-medium">Connected</span>
                        </div>
                    ) : (
                        <div className="flex items-center gap-2 text-amber-500 bg-amber-500/10 px-3 py-1.5 rounded-full border border-amber-500/20">
                            <AlertCircle className="w-4 h-4" />
                            <span className="text-sm font-medium">Not Connected</span>
                        </div>
                    )}
                </div>
            </CardHeader>

            <CardContent className="p-4 sm:p-6 pt-4">
                {envError && (
                    <div className="mb-4 p-3 bg-destructive/10 border border-destructive/20 rounded-md flex items-start gap-2 text-destructive text-sm">
                        <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                        <p>{envError}</p>
                    </div>
                )}

                {isConnected ? (
                    <div className="space-y-4">
                        <p className="text-sm text-muted-foreground">
                            Your broker account is successfully linked. The AI Engine is actively monitoring your portfolio and evaluating market opportunities.
                        </p>
                    </div>
                ) : (
                    <div className="flex flex-col gap-4 bg-muted/30 p-4 rounded-lg border border-border/50">
                        <div className="flex items-start gap-3">
                            <Terminal className="w-5 h-5 text-primary mt-0.5 shrink-0" />
                            <div>
                                <h4 className="text-sm font-medium text-foreground mb-1">
                                    OSS Edition: Configure API Keys via <code className="text-primary">.env.oss</code>
                                </h4>
                                <p className="text-xs text-muted-foreground max-w-md">
                                    In the OSS Edition, broker credentials are configured exclusively via your <code>.env.oss</code> file.
                                    The dashboard does not store or transmit API keys.
                                </p>
                            </div>
                        </div>
                        <div className="bg-background border border-border rounded-md p-3 font-mono text-xs text-muted-foreground">
                            <div className="text-primary/60 mb-1"># .env.oss</div>
                            <div>ALPACA_API_KEY=<span className="text-amber-400">your_paper_key</span></div>
                            <div>ALPACA_SECRET_KEY=<span className="text-amber-400">your_paper_secret</span></div>
                            <div>PAPER_TRADING=<span className="text-green-400">true</span></div>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            After updating <code>.env.oss</code>, restart the stack with <code className="text-primary">make start</code>.
                            The connection status above will update automatically on the next health check.
                        </p>
                    </div>
                )}
            </CardContent>
        </Card>
    );
};
