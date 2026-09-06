/**
 * UXC-1 S3 (#3156) — the SHARED WORM-apply modal (generalisation of
 * PortfolioShapeModal): fixed order record → persist → restart, live-mode branch
 * with re-consent + goLive directive (A4 fix), fail-closed on a failed record.
 */
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useStore } from "@/console/store/useStore";

vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return {
    ...actual,
    isDesktop: () => true,
    restartEngine: vi.fn().mockResolvedValue(undefined),
  };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchHealth: vi.fn().mockResolvedValue({ status: "healthy", paper_trading: false }),
  };
});

import { SettingsApplyModal } from "@/console/desktop/SettingsApplyModal";
import { restartEngine } from "@/lib/desktopBridge";
import { fetchHealth } from "@/lib/api";

const CHANGES = [
  { key: "STOP_LOSS_PCT", label: "Stop loss", from: "7.0", to: "5.0" },
  { key: "MOMENTUM_AGENT_WEIGHT", label: "Momentum weight", from: "0.45", to: "0.60" },
];

function props(overrides: Partial<Parameters<typeof SettingsApplyModal>[0]> = {}) {
  return {
    open: true,
    changes: CHANGES,
    record: vi.fn().mockResolvedValue(undefined),
    persist: vi.fn().mockResolvedValue(undefined),
    onCancel: vi.fn(),
    onApplied: vi.fn(),
    ...overrides,
  };
}

describe("SettingsApplyModal (UXC-1 S3)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    act(() => useStore.setState({ paperTrading: true }));
    (fetchHealth as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: "healthy",
      paper_trading: false,
    });
  });

  it("M1: shows exactly the changed fields as old to new rows", () => {
    render(<SettingsApplyModal {...props()} />);
    expect(screen.getByText("Stop loss")).toBeTruthy();
    expect(screen.getByText("Momentum weight")).toBeTruthy();
    expect(screen.getAllByText(/7\.0|0\.45/).length).toBeGreaterThan(0);
  });

  it("M2: paper mode — confirm runs record → persist → restart(nonce) WITHOUT goLive", async () => {
    const order: string[] = [];
    const p = props({
      record: vi.fn(async () => {
        order.push("record");
      }),
      persist: vi.fn(async () => {
        order.push("persist");
      }),
    });
    (restartEngine as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      order.push("restart");
    });
    render(<SettingsApplyModal {...p} />);
    fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
    await waitFor(() => expect(p.onApplied).toHaveBeenCalled());
    expect(order).toEqual(["record", "persist", "restart"]);
    const [nonce, opts] = (restartEngine as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(typeof nonce).toBe("string");
    expect(opts?.goLive).toBeFalsy();
  });

  it("M3: a failed WORM record blocks persist and restart, shows an error", async () => {
    const p = props({
      record: vi.fn().mockRejectedValue(new Error("chain not writable")),
    });
    render(<SettingsApplyModal {...p} />);
    fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
    await waitFor(() => screen.getByRole("alert"));
    expect(p.persist).not.toHaveBeenCalled();
    expect(restartEngine).not.toHaveBeenCalled();
    expect(p.onApplied).not.toHaveBeenCalled();
  });

  it("M4: live mode — apply stays blocked until the live re-consent checkbox is ticked", async () => {
    act(() => useStore.setState({ paperTrading: false }));
    const p = props();
    render(<SettingsApplyModal {...p} />);

    const confirm = screen.getByRole("button", {
      name: /change and restart/i,
    }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(p.record).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("checkbox", { name: /live/i }));
    expect((screen.getByRole("button", { name: /change and restart/i }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("M5: live mode — after re-consent the restart carries goLive and reconciles expecting live", async () => {
    act(() => useStore.setState({ paperTrading: false }));
    const p = props();
    render(<SettingsApplyModal {...p} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /live/i }));
    fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));

    await waitFor(() => expect(p.onApplied).toHaveBeenCalled());
    const [nonce, opts] = (restartEngine as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(typeof nonce).toBe("string");
    expect(opts).toEqual({ goLive: true });
    expect(fetchHealth).toHaveBeenCalled();
  });

  it("M6: live mode — a live-enable failure after the restart surfaces, never silently paper", async () => {
    act(() => useStore.setState({ paperTrading: false }));
    (fetchHealth as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: "healthy",
      paper_trading: true,
      live_enable_failed_reason: "audit chain not verified",
    });
    const p = props();
    render(<SettingsApplyModal {...p} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /live/i }));
    fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));

    await waitFor(() => expect(p.onApplied).toHaveBeenCalled());
    expect(useStore.getState().switchError ?? "").toMatch(/live not enabled/i);
  });
});
