import { useEffect, useState } from "react";
import {
  isDesktop,
  startEngine,
  stopEngine,
  getEngineStatus,
  getEngineLogs,
  onEngineStatus,
  onEngineLog,
  type EngineStatus,
} from "@/lib/desktopBridge";

const MAX_LOG_LINES = 200;

export interface UseEngine {
  isDesktop: boolean;
  status: EngineStatus;
  detail: string | null;
  logs: string[];
  start: () => Promise<void>;
  stop: () => Promise<void>;
}

/**
 * Bridges the Electron engine-lifecycle IPC (start/stop/status/logs) into React
 * state (G3d, #1050) — all through desktopBridge, never window.aaagents direct.
 * In the cloud build the bridge is absent → status "unavailable", empty logs,
 * and start/stop are no-ops; the Settings page renders a desktop-only note.
 */
export function useEngine(): UseEngine {
  const desktop = isDesktop();
  const [status, setStatus] = useState<EngineStatus>(desktop ? "stopped" : "unavailable");
  const [detail, setDetail] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);

  useEffect(() => {
    if (!desktop) return;
    // Seed status + replay buffered log lines (an early spawn/ENOENT error can
    // arrive before we subscribe). Only seed logs if none have streamed yet.
    void getEngineStatus().then(setStatus);
    void getEngineLogs().then((lines) => {
      if (lines.length) setLogs((prev) => (prev.length === 0 ? lines.slice(-MAX_LOG_LINES) : prev));
    });
    const unsubStatus = onEngineStatus((p) => {
      setStatus(p.status);
      setDetail(p.detail ?? null);
    });
    const unsubLog = onEngineLog((line) =>
      setLogs((prev) => [...prev, line].slice(-MAX_LOG_LINES)),
    );
    return () => {
      unsubStatus();
      unsubLog();
    };
  }, [desktop]);

  return { isDesktop: desktop, status, detail, logs, start: startEngine, stop: stopEngine };
}
