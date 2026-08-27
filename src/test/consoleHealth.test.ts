import { describe, it, expect, beforeEach } from "vitest";

import {
  marketPillLabel,
  brokerLabel,
  streamPill,
  latestVerdictPerSymbol,
} from "@/console/live/health";
import { useStore } from "@/console/store/useStore";
import type { DeepHealth } from "@/lib/api";
import type { AuditEvent } from "@/console/live/audit";

// DASH-1 T5 (#1473) + T6 (#1474): the honesty contract — no field ever shows a
// fabricated value; "unknown" renders an honest "—" / "Desktop-only"; and the
// round-table view, when its ephemeral store is empty, is hydrated from the
// permanent audit log labelled "last known", never as a live verdict.

describe("T5 — market pill", () => {
  it("renders an honest dash until the state is known", () => {
    expect(marketPillLabel(null)).toBe("—");
  });
  it("reflects the real market state", () => {
    expect(marketPillLabel(true)).toBe("Open");
    expect(marketPillLabel(false)).toBe("Closed");
  });
});

describe("T5 — broker label", () => {
  const mk = (status: string): DeepHealth => ({
    status: "x",
    is_market_open: false,
    components: { alpaca: { status } },
  });
  it("is null until the first successful poll", () => {
    expect(brokerLabel(null)).toBeNull();
  });
  it("names the broker when connected", () => {
    expect(brokerLabel(mk("ok"))).toBe("Alpaca");
  });
  it("is honest when the broker is unreachable", () => {
    expect(brokerLabel(mk("unavailable"))).toBe("Not connected");
    expect(brokerLabel(mk("error"))).toBe("Not connected");
  });
});

describe("T5 — stream-live pill", () => {
  it("is desktop-only in the browser (no local log)", () => {
    expect(streamPill(false, 5)).toEqual({ label: "Desktop-only", live: false });
  });
  it("is live on desktop with data", () => {
    expect(streamPill(true, 3)).toEqual({ label: "Stream live", live: true });
  });
  it("is idle on desktop before the first decision", () => {
    expect(streamPill(true, 0)).toEqual({ label: "Idle", live: false });
  });
});

describe("T6 — latest verdict per symbol (Option B)", () => {
  const ev = (
    symbol: string,
    kind: AuditEvent["kind"],
    message: string,
    ms: number,
  ): AuditEvent => ({ ts: new Date(ms), kind, symbol, message, hash: `${symbol}-${ms}` });

  it("keeps only the latest entry per symbol (audit is newest-first)", () => {
    const audit: AuditEvent[] = [
      ev("AAPL", "approval", "BUY · 72% consensus", 200), // newest AAPL
      ev("MSFT", "decision", "Hold · 55% consensus", 150),
      ev("AAPL", "approval", "SELL · 60% consensus", 100), // older AAPL — ignored
    ];
    const out = latestVerdictPerSymbol(audit);
    expect(out).toHaveLength(2);
    const aapl = out.find((v) => v.symbol === "AAPL")!;
    expect(aapl.action).toBe("BUY");
    expect(aapl.ts.getTime()).toBe(200);
  });

  it("maps verdict actions honestly", () => {
    const audit: AuditEvent[] = [
      ev("X", "rejection", "Blocked by risk gatekeeper", 9),
      ev("Y", "approval", "SELL · 60% consensus", 9),
      ev("Z", "decision", "Hold · 50% consensus", 9),
    ];
    const m = Object.fromEntries(
      latestVerdictPerSymbol(audit).map((v) => [v.symbol, v.action]),
    );
    expect(m).toEqual({ X: "BLOCKED", Y: "SELL", Z: "HOLD" });
  });

  it("ignores entries without a symbol", () => {
    const audit: AuditEvent[] = [
      { ts: new Date(1), kind: "decision", message: "Hold", hash: "h" },
    ];
    expect(latestVerdictPerSymbol(audit)).toEqual([]);
  });
});

describe("T5 — store setMarketHealth", () => {
  beforeEach(() => useStore.setState({ marketOpen: null, brokerLabel: null }));
  it("stores the real market + broker state", () => {
    useStore.getState().setMarketHealth(true, "Alpaca");
    expect(useStore.getState().marketOpen).toBe(true);
    expect(useStore.getState().brokerLabel).toBe("Alpaca");
  });
  it("falls back to null (honest unknown) when health is unavailable", () => {
    useStore.getState().setMarketHealth(null, null);
    expect(useStore.getState().marketOpen).toBeNull();
    expect(useStore.getState().brokerLabel).toBeNull();
  });
});
