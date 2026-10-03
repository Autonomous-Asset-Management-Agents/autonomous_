import { useState, useEffect } from "react";
import { AlertCircle, RefreshCw, Terminal, RotateCcw, LifeBuoy } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
    isDesktop,
    getEngineStatus,
    getEngineLogs,
    onEngineStatus,
    startEngine,
    recoverToPaper,
    type EngineStatus,
} from "@/lib/desktopBridge";

const BackendOffline = () => {
    const desktop = isDesktop();
    const [status, setStatus] = useState<EngineStatus | "checking...">("checking...");
    const [logs, setLogs] = useState<string[]>([]);
    const [restarting, setRestarting] = useState(false);
    const [recovering, setRecovering] = useState(false);

    useEffect(() => {
        if (!desktop) return;
        getEngineStatus().then(setStatus);
        getEngineLogs().then((l) => setLogs(l.slice(-8)));
        const unsub = onEngineStatus((p) => {
            setStatus(p.status);
            // Recovery happens via the App.tsx health-check → navigate to /console
        });
        return unsub;
    }, [desktop]);

    const handleRestart = async () => {
        setRestarting(true);
        setLogs([]);
        await startEngine();
        // Status subscription will update; App.tsx recovery will redirect back
    };

    // INC-3 (#2213): recover a dead/failed LIVE boot onto paper. Forces the NEXT boot to
    // paper (one-shot shell flag) and atomically restarts — it never writes the WORM chain
    // (Python owns the disable record, issued once the engine is healthy on paper). This is
    // the way out when a live boot keeps failing: /api/live/disable needs a running engine,
    // and there isn't one.
    const handleRecoverToPaper = async () => {
        setRecovering(true);
        setLogs([]);
        await recoverToPaper();
        // Status subscription will update; App.tsx recovery redirects back once healthy.
    };

    return (
        <div className="min-h-screen bg-black flex flex-col items-center justify-center p-4">
            <div className="max-w-md w-full bg-zinc-900 border border-zinc-800 rounded-xl p-6 text-center space-y-6">
                <div className="mx-auto w-16 h-16 bg-red-500/10 rounded-full flex items-center justify-center">
                    <AlertCircle className="w-8 h-8 text-red-500" />
                </div>

                <div className="space-y-2">
                    <h1 className="text-2xl font-bold text-white tracking-tight">
                        Backend Offline
                    </h1>
                    <p className="text-zinc-400 text-sm">
                        The autonomous_ Desktop app could not connect to the local Python backend.
                    </p>
                </div>

                {desktop ? (
                    /* ── Desktop: Engine Status + Logs + Restart ── */
                    <div className="space-y-4">
                        <div className="flex items-center justify-center gap-2 text-sm">
                            <span className="text-zinc-500">Engine:</span>
                            <span
                                className={
                                    status === "running"
                                        ? "text-green-400"
                                        : status === "error"
                                          ? "text-red-400"
                                          : status === "starting"
                                            ? "text-white"
                                            : "text-zinc-400"
                                }
                            >
                                {status}
                            </span>
                        </div>

                        {logs.length > 0 && (
                            <div className="bg-black/50 border border-zinc-800 rounded-lg p-3 text-left">
                                <div className="flex items-center gap-2 text-zinc-500 mb-2 text-xs font-medium">
                                    <Terminal className="w-3 h-3" />
                                    <span>Recent engine output</span>
                                </div>
                                <pre className="text-xs text-zinc-400 whitespace-pre-wrap break-all max-h-32 overflow-y-auto font-mono leading-relaxed">
                                    {logs.join("\n")}
                                </pre>
                            </div>
                        )}

                        <Button
                            onClick={handleRestart}
                            disabled={restarting || status === "starting"}
                            variant="outline"
                            className="w-full border-zinc-700 text-zinc-300 hover:bg-zinc-800"
                        >
                            <RotateCcw
                                className={`w-4 h-4 mr-2 ${restarting ? "animate-spin" : ""}`}
                            />
                            {restarting || status === "starting"
                                ? "Starting…"
                                : "Restart Engine"}
                        </Button>

                        {/* INC-3 (#2213): escape hatch out of a dead/failed LIVE boot.
                            Forces the next boot onto paper WITHOUT touching the WORM
                            chain — the engine comes up clean and the operator can then
                            issue the regular disable. */}
                        <Button
                            onClick={handleRecoverToPaper}
                            disabled={recovering || restarting || status === "starting"}
                            variant="ghost"
                            className="w-full text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800"
                        >
                            <LifeBuoy
                                className={`w-4 h-4 mr-2 ${recovering ? "animate-spin" : ""}`}
                            />
                            {recovering ? "Recovering…" : "Recover to Paper"}
                        </Button>
                        <p className="text-xs text-zinc-500 -mt-2">
                            Stuck after enabling live? Boot back into paper trading
                            (safe) — your live-enable record is preserved.
                        </p>
                    </div>
                ) : (
                    /* ── Cloud / Self-hosted: existing Docker instructions ── */
                    <div className="bg-black/50 border border-zinc-800 rounded-lg p-4 text-left">
                        <div className="flex items-center gap-2 text-zinc-300 mb-2 font-medium">
                            <Terminal className="w-4 h-4" />
                            <span>How to fix this:</span>
                        </div>
                        <ol className="list-decimal list-inside text-sm text-zinc-400 space-y-2">
                            <li>Open a terminal or PowerShell window.</li>
                            <li>Navigate to the autonomous_ installation folder.</li>
                            <li>
                                Run{" "}
                                <code className="bg-zinc-800 px-1 py-0.5 rounded text-zinc-300">
                                    setup.ps1
                                </code>{" "}
                                or{" "}
                                <code className="bg-zinc-800 px-1 py-0.5 rounded text-zinc-300">
                                    docker compose up -d
                                </code>
                                .
                            </li>
                            <li>Wait for the containers to become healthy.</li>
                        </ol>
                    </div>
                )}

                <Button
                    onClick={() => window.location.reload()}
                    className="w-full bg-white text-black hover:bg-zinc-200"
                >
                    <RefreshCw className="w-4 h-4 mr-2" />
                    Retry Connection
                </Button>
            </div>
        </div>
    );
};

export default BackendOffline;
