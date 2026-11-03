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

  it("M7: portals the dialog to <body> so the fixed overlay centres on the viewport, not a transformed ancestor", () => {
    const { container } = render(<SettingsApplyModal {...props()} />);
    // Not a descendant of the caller's container — portaled out.
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    // Lives directly under <body>, where `fixed inset-0` resolves against the viewport.
    const dialog = document.body.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.parentElement).toBe(document.body);
    for (const cls of ["fixed", "inset-0", "items-center", "justify-center"]) {
      expect(dialog?.className).toContain(cls);
    }
  });

  describe("live sizing-method switch (#4989)", () => {
    const SWITCH = [
      { key: "CLEAN_WEIGHT_SIZING", label: "Sizing method", from: "b", to: "a" },
      CHANGES[0],
    ];
    const ACK =
      'I confirm changing the live sizing method from "b" to "a". The change applies to real-money trading and is recorded on a tamper-evident audit trail.';
    const live = () => act(() => useStore.setState({ paperTrading: false }));
    const confirmButton = () =>
      screen.getByRole("button", { name: /change and restart/i }) as HTMLButtonElement;
    const reconsent = () =>
      fireEvent.click(screen.getByRole("checkbox", { name: /continues trading LIVE/i }));

    it("M13: names the old and the new method and blocks without its own confirmation", () => {
      live();
      const p = props({ changes: SWITCH });
      render(<SettingsApplyModal {...p} />);
      expect(screen.getByRole("checkbox", { name: ACK })).toBeTruthy();
      reconsent();
      expect(confirmButton().disabled).toBe(true);
      fireEvent.click(confirmButton());
      expect(p.record).not.toHaveBeenCalled();
    });

    it("M14: the confirmed text reaches record as the acknowledgment", async () => {
      live();
      const p = props({ changes: SWITCH });
      render(<SettingsApplyModal {...p} />);
      reconsent();
      fireEvent.click(screen.getByRole("checkbox", { name: ACK }));
      fireEvent.click(confirmButton());
      await waitFor(() => expect(p.onApplied).toHaveBeenCalled());
      expect((p.record as ReturnType<typeof vi.fn>).mock.calls[0][1]).toBe(ACK);
    });

    it("M15: paper mode keeps the normal confirmation and no method acknowledgment", async () => {
      const p = props({ changes: SWITCH });
      render(<SettingsApplyModal {...p} />);
      expect(screen.queryByRole("checkbox")).toBeNull();
      fireEvent.click(confirmButton());
      await waitFor(() => expect(p.onApplied).toHaveBeenCalled());
      expect((p.record as ReturnType<typeof vi.fn>).mock.calls[0][1]).toBeUndefined();
    });

    it("M16: live without a method change shows only the generic checkbox", () => {
      live();
      render(<SettingsApplyModal {...props()} />);
      expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    });

    it("M17: a paper slot (restart prop) has no method confirmation", () => {
      live();
      const restart = vi.fn().mockResolvedValue(undefined);
      render(<SettingsApplyModal {...props({ changes: SWITCH, restart })} />);
      expect(screen.queryByRole("checkbox")).toBeNull();
    });
  });

  describe("restart prop (#4953: an engine other than the live profile's)", () => {
    const live = () => act(() => useStore.setState({ paperTrading: false }));

    it("M8: with restart no live checkbox, although the live profile runs live", () => {
      live();
      render(<SettingsApplyModal {...props({ restart: vi.fn().mockResolvedValue(undefined) })} />);
      expect(screen.queryByRole("checkbox")).toBeNull();
      expect((screen.getByRole("button", { name: /change and restart/i }) as HTMLButtonElement).disabled).toBe(false);
    });

    it("M9: with restart restartEngine is not called, no goLive, no reconcile", async () => {
      live();
      const restart = vi.fn().mockResolvedValue(undefined);
      const p = props({ restart });
      render(<SettingsApplyModal {...p} />);
      fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
      await waitFor(() => expect(p.onApplied).toHaveBeenCalled());
      expect(restartEngine).not.toHaveBeenCalled();
      expect(fetchHealth).not.toHaveBeenCalled();
      expect(restart).toHaveBeenCalledTimes(1);
      expect(typeof restart.mock.calls[0][0]).toBe("string");
    });

    it("M10: order record → persist → restart", async () => {
      const order: string[] = [];
      const step = (name: string) => vi.fn(async () => { order.push(name); });
      const p = props({ record: step("record"), persist: step("persist"), restart: step("restart") });
      render(<SettingsApplyModal {...p} />);
      fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
      await waitFor(() => expect(p.onApplied).toHaveBeenCalled());
      expect(order).toEqual(["record", "persist", "restart"]);
    });

    it("M11: a failing restart shows its text and does not report applied", async () => {
      const p = props({ restart: vi.fn().mockRejectedValue(new Error("restart refused")) });
      render(<SettingsApplyModal {...p} />);
      fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
      expect((await screen.findByRole("alert")).textContent).toBe("restart refused");
      expect(p.onApplied).not.toHaveBeenCalled();
    });

    // STW-2d (#5117), owner design of 02.11.2026: the diff stands in the slot, the dialog is neutral.
    const DEFAULT_DESCRIPTION = /These settings apply to all future trading decisions/;
    const confirm = () => screen.getByRole("button", { name: /change and restart/i });

    it("M18: with restart btn btn-primary, no pill, no diff, no default sentence, overlay has aaa-console", () => {
      render(<SettingsApplyModal {...props({ restart: vi.fn().mockResolvedValue(undefined) })} />);
      expect(confirm().className).toBe("btn btn-primary");
      expect(confirm().className).not.toMatch(/bg-\[#ff5a52\]|uppercase/);
      expect(screen.queryByText("Stop loss")).toBeNull();
      expect(screen.queryByText("→")).toBeNull();
      expect(screen.queryByText(DEFAULT_DESCRIPTION)).toBeNull();
      expect(screen.getByRole("dialog").classList.contains("aaa-console")).toBe(true);
    });

    it("M19: with restart and empty changes the emptyHint shows", () => {
      const restart = vi.fn().mockResolvedValue(undefined);
      render(<SettingsApplyModal {...props({ changes: [], emptyHint: "No value changes.", restart })} />);
      expect(screen.getByText("No value changes.")).toBeTruthy();
    });

    it("M20: without restart pill, diff, default sentence, overlay without aaa-console — unchanged", () => {
      render(<SettingsApplyModal {...props()} />);
      expect(confirm().className).toMatch(/bg-\[#ff5a52\]/);
      expect(confirm().className).toMatch(/uppercase/);
      expect(screen.getByText("Stop loss")).toBeTruthy();
      expect(screen.getByText(DEFAULT_DESCRIPTION)).toBeTruthy();
      expect(screen.getByRole("dialog").className).toBe(
        "fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4",
      );
    });

    // STW-2f (#5125): the paper branch shows the sealed sentence as text, without a checkbox.
    const ACK = "I confirm this sealed sentence for the test.";

    it("M21: with restart and acknowledgment the text stands verbatim, no checkbox, btn btn-primary", () => {
      render(<SettingsApplyModal {...props({ restart: vi.fn().mockResolvedValue(undefined), acknowledgment: ACK })} />);
      const ack = screen.getByText(ACK);
      expect(ack.textContent).toBe(ACK);
      expect(ack.tagName).toBe("P");
      expect(screen.queryByRole("checkbox")).toBeNull();
      expect(confirm().className).toBe("btn btn-primary");
      expect(!!(ack.compareDocumentPosition(confirm()) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
    });

    it("M22: live branch: identical markup with and without acknowledgment, re-consent stays", () => {
      live();
      const without = render(<SettingsApplyModal {...props()} />);
      const html = screen.getByRole("dialog").innerHTML;
      without.unmount();
      render(<SettingsApplyModal {...props({ acknowledgment: ACK })} />);
      expect(screen.getByRole("dialog").innerHTML).toBe(html);
      expect(screen.queryByText(ACK)).toBeNull();
      expect(
        screen.getByRole("checkbox", {
          name: "I confirm the engine restarts and afterwards continues trading LIVE with real money.",
        }),
      ).toBeInTheDocument();
    });

    it("M12: without restart the live branch stays as it was", async () => {
      live();
      const p = props();
      render(<SettingsApplyModal {...p} />);
      expect((screen.getByRole("button", { name: /change and restart/i }) as HTMLButtonElement).disabled).toBe(true);
      fireEvent.click(screen.getByRole("checkbox", { name: /live/i }));
      fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
      await waitFor(() => expect(p.onApplied).toHaveBeenCalled());
      expect((restartEngine as ReturnType<typeof vi.fn>).mock.calls[0][1]).toEqual({ goLive: true });
      expect(fetchHealth).toHaveBeenCalled();
    });
  });
});
