/**
 * Deep-health consumers for the honest dashboard (DASH-1 T5 #1473 + T6 #1474).
 *
 * T5: poll /health/deep into the store (market-open state + broker label) and
 * expose pure label helpers so the Overview Market-pill / Broker tag and the
 * Audit "Stream live" pill reflect the REAL engine state — never a placeholder.
 * Every "unknown" renders an honest "—" / "Desktop-only", never a fake value.
 *
 * T6 (Option B): derive the latest verdict per symbol from the PERMANENT audit
 * log so the (ephemeral) Round-Table view is never confusingly empty after an
 * engine restart. The caller labels these "last known · HH:MM" — they are never
 * shown as live, current-session verdicts.
 */
import { useEffect } from "react";

import { fetchDeepHealth, type DeepHealth } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import type { AuditEvent } from "./audit";

const POLL_MS = 30_000;

/** Market-pill label — honest "—" until the state is known, never a fake. */
export function marketPillLabel(marketOpen: boolean | null): string {
  if (marketOpen == null) return "—";
  return marketOpen ? "Open" : "Closed";
}

/** Broker label from the /health/deep Alpaca component. "Not connected" when the
 *  broker is unreachable; null until the first successful poll (show nothing). */
export function brokerLabel(deep: DeepHealth | null): string | null {
  if (!deep) return null;
  const s = deep.components?.alpaca?.status;
  if (s === "ok") return "Alpaca";
  if (s === "unavailable" || s === "error") return "Not connected";
  return null;
}

/** Audit "Stream live" pill: live only on desktop WITH data; "Desktop-only" in
 *  the browser (no local log); "Idle" on desktop before the first decision. */
export function streamPill(
  isDesktopApp: boolean,
  auditLen: number,
): { label: string; live: boolean } {
  if (!isDesktopApp) return { label: "Desktop-only", live: false };
  if (auditLen > 0) return { label: "Stream live", live: true };
  return { label: "Idle", live: false };
}

export type VerdictAction = "BUY" | "SELL" | "HOLD" | "BLOCKED";

export interface LastKnownVerdict {
  symbol: string;
  action: VerdictAction;
  ts: Date;
  detail: string;
}

/** T6 Option B: latest verdict per symbol from the permanent audit log (which is
 *  newest-first). Used to hydrate the Round-Table view when the ephemeral store
 *  is empty after a restart — the caller marks these "last known", never live. */
export function latestVerdictPerSymbol(audit: AuditEvent[]): LastKnownVerdict[] {
  const seen = new Map<string, LastKnownVerdict>();
  for (const e of audit) {
    if (!e.symbol || seen.has(e.symbol)) continue; // first hit = latest (newest-first)
    const action: VerdictAction =
      e.kind === "rejection"
        ? "BLOCKED"
        : e.message.startsWith("BUY")
          ? "BUY"
          : e.message.startsWith("SELL")
            ? "SELL"
            : "HOLD";
    seen.set(e.symbol, { symbol: e.symbol, action, ts: e.ts, detail: e.message });
  }
  return [...seen.values()];
}

/** Polls /health/deep (~30s) into the store: market-open state + broker label.
 *  Runs in both editions — the call returns null when unreachable, which the
 *  pills render as an honest "—". Recursive setTimeout (no overlapping calls). */
export function useHealthPolling(): void {
  const setMarketHealth = useStore((s) => s.setMarketHealth);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (!alive) return;
      try {
        const deep = await fetchDeepHealth();
        if (alive) {
          setMarketHealth(deep ? !!deep.is_market_open : null, brokerLabel(deep));
        }
      } catch {
        // Health polling must never throw — keep the last known state.
      } finally {
        if (alive) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [setMarketHealth]);
}
