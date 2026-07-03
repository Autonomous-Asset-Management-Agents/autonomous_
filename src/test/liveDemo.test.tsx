import { describe, it, expect, vi, afterEach } from "vitest";
import { render, renderHook, screen, waitFor } from "@testing-library/react";

import { LiveDemo } from "../pages/LiveDemo";
import {
  useSnapshotPolling,
  type DemoSnapshot,
} from "../console/live/useSnapshotPolling";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/lib/firebase", () => ({ auth: {}, googleProvider: {} }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

const snap = (overrides: Partial<DemoSnapshot> = {}): DemoSnapshot => ({
  generated_at: new Date().toISOString(),
  status: "live",
  disclaimer: "Paper-Trading-Demo — keine Anlageberatung / keine Kauf- oder Verkaufsempfehlung.",
  equity: 10847.32,
  cash: 1203.55,
  day_pl_pct: 0.84,
  positions: [
    { symbol: "AAPL", qty: 12, market_value: 2640, unrealized_pl_pct: 3.2 },
  ],
  decisions: [
    { symbol: "AAPL", action: "buy", consensus: 0.71, conviction: 0.64, summary: "EDGAR-grounded" },
  ],
  report: { symbol: "AAPL", summary: "Stabiler Cashflow", sentiment: "leicht positiv", as_of: "2026-06-30" },
  equity_curve: [
    { date: "2026-06-29", equity: 10620, benchmark: 10550 },
    { date: "2026-06-30", equity: 10847.32, benchmark: 10610 },
  ],
  ...overrides,
});

const mockFetch = (resp: Partial<Response> & { json?: () => Promise<unknown> }) =>
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(resp));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useSnapshotPolling", () => {
  it("a successful fetch returns the snapshot and is not paused", async () => {
    mockFetch({ ok: true, json: async () => snap() });
    const { result } = renderHook(() => useSnapshotPolling("/x.json", 999_999));
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    expect(result.current.snapshot?.equity).toBe(10847.32);
    expect(result.current.paused).toBe(false);
  });

  it("a failed fetch sets error + paused and keeps snapshot null (fail-soft)", async () => {
    mockFetch({ ok: false, status: 503 });
    const { result } = renderHook(() => useSnapshotPolling("/x.json", 999_999));
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.snapshot).toBeNull();
    expect(result.current.paused).toBe(true);
  });

  it("a stale snapshot (generated_at > 5 min old) is flagged paused", async () => {
    const stale = snap({ generated_at: new Date(Date.now() - 10 * 60 * 1000).toISOString() });
    mockFetch({ ok: true, json: async () => stale });
    const { result } = renderHook(() => useSnapshotPolling("/x.json", 999_999));
    await waitFor(() => expect(result.current.paused).toBe(true));
  });
});

describe("LiveDemo page", () => {
  const renderDemo = () =>
    render(
      <MemoryRouter>
        <LiveDemo />
      </MemoryRouter>,
    );

  it("renders the disclaimer, the shared header (LOGIN), and a position", async () => {
    mockFetch({ ok: true, json: async () => snap() });
    renderDemo();
    await waitFor(() =>
      expect(screen.queryByText(/Paper-Trading-Demo/)).not.toBeNull()
    );
    // shared SiteHeader renders a LOGIN control
    expect(screen.queryAllByText(/login/i).length).toBeGreaterThan(0);
    // the position symbol appears (positions table + round-table both render AAPL)
    expect(screen.queryAllByText("AAPL").length).toBeGreaterThan(0);
    // a round-table vote is shown
    expect(screen.queryAllByText(/buy/i).length).toBeGreaterThan(0);
  });

  it("shows the shared header immediately, before the snapshot arrives", () => {
    mockFetch({ ok: true, json: async () => snap() });
    renderDemo();
    // header (shared SiteHeader) is present immediately; content waits for the snapshot
    expect(screen.queryAllByText(/login/i).length).toBeGreaterThan(0);
  });

  it("shows 'Demo pausiert' when the snapshot fetch fails (box offline) — #1591", async () => {
    mockFetch({ ok: false, status: 503 });
    renderDemo();
    await waitFor(() =>
      expect(screen.queryByText(/Demo pausiert/)).not.toBeNull()
    );
  });

  it("renders the console-look dashboard (equity surface, KPIs, consensus) when a snapshot is present", async () => {
    mockFetch({ ok: true, json: async () => snap() });
    const { container } = renderDemo();
    await waitFor(() => expect(screen.queryByText(/10\.847,32/)).not.toBeNull());
    // console theme scope is applied (the shared components' classes are .aaa-console-scoped)
    expect(container.querySelector(".aaa-console")).not.toBeNull();
    // KPI card + decision consensus render; no fabricated vote tally / kill switch leaks in
    expect(screen.getAllByText("Tages-P/L").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Konsens 71/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/kill switch/i)).toBeNull();
  });
});
