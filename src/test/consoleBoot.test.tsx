import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { BootSplash } from "../console/splash/BootSplash";
import { useStore } from "../console/store/useStore";
import type { UseEngine } from "../console/live/useEngine";

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
vi.mock("@/lib/api", () => ({ fetchPortfolioSummary: vi.fn().mockResolvedValue(null), fetchHealth: vi.fn().mockResolvedValue(null) }));
vi.mock("@/console/live/usePortfolioPolling", () => ({ usePortfolioPolling: () => {} }));

// vi.hoisted ensures mockUseEngine is created before vi.mock hoisting runs,
// so the factory closure `() => mockUseEngine()` captures the right reference.
const { mockUseEngine } = vi.hoisted(() => ({
  mockUseEngine: vi.fn<[], UseEngine>(),
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

describe("BootSplash", () => {
  beforeEach(() => {
    useStore.setState({ currentEquity: null });
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