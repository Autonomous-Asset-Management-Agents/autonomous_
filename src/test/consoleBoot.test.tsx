import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";
import { BootSplash } from "../console/splash/BootSplash";
import { useStore } from "../console/store/useStore";
import type { UseEngine } from "../console/live/useEngine";
import type { ProvisionStatus } from "../console/splash/bootStatus";

/**
 * BootSplash (#1050): the desktop boot splash. Types the `autonomous_` wordmark
 * on black; reveals the dashboard once real data arrives or a hard safety
 * timeout (never hangs); shows an honest error/retry state on engine failure.
 *
 * Mock strategy:
 * - `usePortfolioPolling` is mocked as a no-op: dataReady is controlled directly
 *   via useStore.setState(). Without this, the initial poll Promise resolves
 *   outside act() and produces spurious warnings.
 * - `useEngine` is mocked via vi.hoisted() + vi.fn() so the factory closure can
 *   reference the mock fn before import hoisting runs. Per-test overrides use
 *   mockReturnValue(). Test 4 uses the error state to exercise the error UI path.
 *
 * Note on act() warnings: the remaining warnings in tests 1-3 come from
 * useTypewriter's setInterval ticks firing during vi.advanceTimersByTimeAsync()
 * calls. These are cosmetic -- the assertions are correct and the tests are
 * deterministic. Full elimination would require restructuring useTypewriter to
 * accept an external clock, which is out of scope for this fix.
 */
vi.mock("@/lib/api", () => ({
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }), fetchPortfolioSummary: vi.fn().mockResolvedValue(null), fetchHealth: vi.fn().mockResolvedValue(null) }));
vi.mock("@/console/live/usePortfolioPolling", () => ({ usePortfolioPolling: () => {} }));
vi.mock("@/console/live/useHealthPolling", () => ({ useHealthPolling: () => {} }));

// vi.hoisted ensures mockUseEngine is created before vi.mock hoisting runs,
// so the factory closure `() => mockUseEngine()` captures the right reference.
const { mockUseEngine } = vi.hoisted(() => ({
  mockUseEngine: vi.fn<() => UseEngine>(),
}));
vi.mock("@/console/live/useEngine", () => ({ useEngine: () => mockUseEngine() }));

const IDLE_ENGINE: UseEngine = {
  isDesktop: false,
  status: "unavailable",
  detail: null,
  logs: [],
  start: async () => {},
  stop: async () => {},
};

// A desktop engine that is up and running — the boot-ready gate additionally requires it to be
// SERVING (engineServing) before the pulse clears, so a portfolio number alone can't reveal early.
const DESKTOP_ENGINE: UseEngine = { ...IDLE_ENGINE, isDesktop: true, status: "running" };

describe("BootSplash", () => {
  beforeEach(() => {
    useStore.setState({ currentEquity: null, engineServing: false });
    mockUseEngine.mockReturnValue(IDLE_ENGINE);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("types the autonomous wordmark in the booting state", async () => {
    vi.useFakeTimers();
    await act(async () => {
      render(<BootSplash onDone={() => {}} />);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000); // run the typewriter to completion
    });
    expect(screen.getByText(/autonomous/)).toBeTruthy();
  });

  it("reveals via the safety timeout so the splash never hangs", async () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    await act(async () => {
      render(<BootSplash onDone={onDone} />);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6100);
    });
    expect(onDone).toHaveBeenCalled();
  });

  it("reveals once real portfolio data has loaded", async () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    await act(async () => {
      render(<BootSplash onDone={onDone} />);
    });
    // Wrap the store mutation in act() so React flushes the re-render triggered
    // by the zustand state change before we advance the timers.
    await act(async () => {
      useStore.setState({ currentEquity: 105_000 }); // live equity arrives
    });
    
    // minMs (1200) elapsed -> BootPhase becomes "ready"
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200);
    });
    
    // fade (420) elapsed -> onDone is called
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500); 
    });
    
    expect(onDone).toHaveBeenCalled();
  });

  it("desktop: keeps the pulse up while the portfolio loaded but the engine is NOT yet serving", async () => {
    vi.useFakeTimers();
    mockUseEngine.mockReturnValue(DESKTOP_ENGINE);
    const onDone = vi.fn();
    await act(async () => {
      render(<BootSplash onDone={onDone} />);
    });
    // Equity arrived, but /health still reports "starting" (engineServing:false) — the app is NOT
    // fully operational yet. The pulse must stay (no early reveal), well within the safety cap.
    await act(async () => {
      useStore.setState({ currentEquity: 105_000, engineServing: false });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1700); // past minMs (1200) + fade — would have revealed before
    });
    expect(onDone).not.toHaveBeenCalled();
  });

  it("desktop: reveals only once the engine is SERVING and the portfolio has loaded", async () => {
    vi.useFakeTimers();
    mockUseEngine.mockReturnValue(DESKTOP_ENGINE);
    const onDone = vi.fn();
    await act(async () => {
      render(<BootSplash onDone={onDone} />);
    });
    await act(async () => {
      useStore.setState({ currentEquity: 105_000, engineServing: true }); // fully operational
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200); // minMs elapsed → phase "ready"
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500); // fade elapsed → onDone
    });
    expect(onDone).toHaveBeenCalled();
  });

  it("shows the honest error state on engine failure", async () => {
    // Override useEngine to return an engine-error state.
    mockUseEngine.mockReturnValue({
      ...IDLE_ENGINE,
      isDesktop: true,
      status: "error",
      logs: ["boom"],
    });
    await act(async () => {
      render(<BootSplash onDone={() => {}} />);
    });
    expect(screen.getByText(/engine didn.?t start/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });
});

// B4 (live on 0.4.13-rc4): on a first macOS launch the shell downloads the ~2 GB runtime while the
// splash is held. The engine cannot start before that (status stays "stopped"), so the boot timeout
// tripped into "engine didn't start" + Retry after 120 s, and Retry produced "Engine source not
// found". While provisioning runs there must be no timeout and no Retry; error + Retry appear only
// once the shell reports a provisioning failure.
const STOPPED_DESKTOP_ENGINE: UseEngine = { ...IDLE_ENGINE, isDesktop: true, status: "stopped" };
const PROVISIONING: ProvisionStatus = { active: true, percent: 23, stage: "Python-Laufzeit", error: null };

describe("BootSplash — first-launch runtime provisioning (mac)", () => {
  beforeEach(() => {
    useStore.setState({ currentEquity: null, engineServing: false });
    mockUseEngine.mockReturnValue(STOPPED_DESKTOP_ENGINE);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("no timeout and no Retry while the shell provisions; the status line shows the percent", async () => {
    vi.useFakeTimers();
    await act(async () => {
      render(<BootSplash hold onDone={() => {}} provision={PROVISIONING} />);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(130_000); // well past timeoutMs (120 s)
    });
    expect(screen.queryByText(/engine didn.?t start/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
    expect(screen.getByText(/Downloading the engine runtime .* 23%/)).toBeTruthy();
  });

  it("verify / unpack stages show a clear text, not a frozen percent", async () => {
    vi.useFakeTimers();
    let view: ReturnType<typeof render> | undefined;
    await act(async () => {
      view = render(<BootSplash hold onDone={() => {}} provision={{ ...PROVISIONING, percent: 32, stage: "Prüfen" }} />);
    });
    expect(screen.getByText(/Verifying the engine runtime/)).toBeTruthy();
    expect(screen.queryByText(/32%/)).toBeNull();
    await act(async () => {
      view!.rerender(<BootSplash hold onDone={() => {}} provision={{ ...PROVISIONING, percent: 38, stage: "Entpacken" }} />);
    });
    expect(screen.getByText(/Unpacking the engine runtime/)).toBeTruthy();
  });

  it("an engine error while provisioning does not surface Retry (the engine cannot start yet)", async () => {
    vi.useFakeTimers();
    mockUseEngine.mockReturnValue({ ...STOPPED_DESKTOP_ENGINE, status: "error", logs: ["Engine source not found"] });
    await act(async () => {
      render(<BootSplash hold onDone={() => {}} provision={PROVISIONING} />);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(130_000);
    });
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });

  it("a failed provisioning shows the error text and Retry", async () => {
    const start = vi.fn(async () => {});
    mockUseEngine.mockReturnValue({ ...STOPPED_DESKTOP_ENGINE, start });
    await act(async () => {
      render(
        <BootSplash
          hold
          onDone={() => {}}
          provision={{ active: false, percent: 23, stage: "Python-Laufzeit", error: "SHA-256 mismatch for aaagents-python-arm64.tar.gz" }}
        />,
      );
    });
    expect(screen.getByText(/engine runtime download failed/i)).toBeTruthy();
    expect(screen.getByText(/SHA-256 mismatch for aaagents-python-arm64\.tar\.gz/)).toBeTruthy();
    expect(screen.queryByText(/engine didn.?t start/i)).toBeNull();
    // Retry keeps the existing engine.start() contract (the shell has no re-provision IPC yet).
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(start).toHaveBeenCalled();
  });

  it("without provisioning (Windows / a provisioned Mac) the timeout still surfaces error + Retry", async () => {
    vi.useFakeTimers();
    await act(async () => {
      render(<BootSplash onDone={() => {}} />);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(121_000);
    });
    expect(screen.getByText(/engine didn.?t start/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });
});