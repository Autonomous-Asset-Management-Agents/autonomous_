// #1425 (LIVE-1 T2) — Paper⇄Live account switcher. Switching to live requires a deliberate Art-14
// acknowledgment → POST /api/live/enable (WORM) → engine restart. Never bypasses the gate.
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const isDesktop = vi.fn();
const startEngine = vi.fn();
const stopEngine = vi.fn();
const fetchHealth = vi.fn();
const liveEnable = vi.fn();
const liveDisable = vi.fn();

vi.mock("@/lib/desktopBridge", () => ({
  isDesktop: () => isDesktop(),
  startEngine: () => startEngine(),
  stopEngine: () => stopEngine(),
}));
vi.mock("@/lib/api", () => ({
  fetchHealth: () => fetchHealth(),
  liveEnable: (a: string, n: string) => liveEnable(a, n),
  liveDisable: (a: string, n: string) => liveDisable(a, n),
}));

import { LiveTradingSwitchCard } from "../console/desktop/LiveTradingSwitchCard";

describe("LiveTradingSwitchCard (#1425)", () => {
  beforeEach(() => {
    isDesktop.mockReset().mockReturnValue(true);
    startEngine.mockReset().mockResolvedValue(undefined);
    stopEngine.mockReset().mockResolvedValue(undefined);
    fetchHealth.mockReset().mockResolvedValue({ paper_trading: true });
    liveEnable.mockReset().mockResolvedValue(undefined);
    liveDisable.mockReset().mockResolvedValue(undefined);
  });

  it("cloud build: renders nothing", () => {
    isDesktop.mockReturnValue(false);
    const { container } = render(<LiveTradingSwitchCard />);
    expect(container.firstChild).toBeNull();
  });

  it("paper account: shows the Paper|Live toggle", async () => {
    render(<LiveTradingSwitchCard />);
    await waitFor(() => expect(screen.getByRole("button", { name: /^live$/i })).toBeTruthy());
    expect(screen.getByRole("button", { name: /^paper$/i })).toBeTruthy();
  });

  it("switch to live REQUIRES the Art-14 ack AND the advance-approval waiver, then enables + restarts", async () => {
    render(<LiveTradingSwitchCard />);
    await waitFor(() => expect(screen.getByText("Paper")).toBeTruthy());
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));

    // Enable is gated on BOTH consents (Art-14 acknowledgment + advance-approval waiver, #1804)
    const enableBtn = screen.getByRole("button", { name: /enable live trading/i });
    expect(enableBtn).toBeDisabled();
    fireEvent.click(screen.getByLabelText("ack-live"));
    expect(enableBtn).toBeDisabled(); // still gated on the advance-approval waiver
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    expect(enableBtn).not.toBeDisabled();

    fireEvent.click(enableBtn);
    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
    // BOTH consents recorded on the WORM chain (Art-14 + advance-approval), plus a nonce
    expect(liveEnable.mock.calls[0][0]).toMatch(/art\.? 14/i);
    expect(liveEnable.mock.calls[0][0]).toMatch(/advance-approval/i);
    expect(liveEnable.mock.calls[0][1]).toBeTruthy();
    // engine restarted so the shell re-reads the WORM chain
    await waitFor(() => expect(startEngine).toHaveBeenCalled());
    expect(stopEngine).toHaveBeenCalled();
  });

  it("live account: the Paper toggle disables live (switch back to paper)", async () => {
    fetchHealth.mockResolvedValue({ paper_trading: false });
    render(<LiveTradingSwitchCard />);
    fireEvent.click(await screen.findByRole("button", { name: /^paper$/i }));
    await waitFor(() => expect(liveDisable).toHaveBeenCalled());
    expect(startEngine).toHaveBeenCalled();
  });
});
