import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

/** UXC-1 S5 (#3158) — pending semantics + S3 apply seam (no write before the WORM flow). */

vi.mock("../lib/api", () => ({
  getTradingSettings: vi.fn(),
  postTradingSettings: vi.fn(),
}));

import { getTradingSettings, postTradingSettings } from "../lib/api";
import { AdvancedTradingSection } from "../console/desktop/AdvancedTradingSection";
import { payload, setBridge } from "./advancedTradingFixtures";

const mockGet = getTradingSettings as unknown as ReturnType<typeof vi.fn>;

const saveMock = () =>
  (window as unknown as { aaagents: { saveSetupState: ReturnType<typeof vi.fn> } })
    .aaagents.saveSetupState;

async function expand() {
  fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
  await waitFor(() => screen.getByLabelText("Buy threshold"));
}

describe("Advanced trading apply flow (UXC-1 S5)", () => {
  beforeEach(() => {
    setBridge();
    mockGet.mockResolvedValue(payload());
  });
  afterEach(() => {
    (window as unknown as { aaagents?: unknown }).aaagents = undefined;
    vi.clearAllMocks();
  });

  it("A1: an edit stays pending — no immediate write of any kind, Changed badge + pending list", async () => {
    const onApply = vi.fn();
    render(<AdvancedTradingSection onApply={onApply} />);
    await expand();
    fireEvent.change(screen.getByLabelText("Stop loss"), { target: { value: "5.0" } });

    expect(onApply).not.toHaveBeenCalled();
    expect(saveMock()).not.toHaveBeenCalled();
    await waitFor(() => screen.getByText("Changed"));
    const pending = screen.getByTestId("pending-changes-advanced");
    expect(pending.textContent).toContain("Stop loss");
    expect(pending.textContent).toContain("7.0");
    expect(pending.textContent).toContain("5.0");
  });

  it("A2: Apply hands the diff under the EXACT engine keys to the S3 seam", async () => {
    const onApply = vi.fn();
    render(<AdvancedTradingSection onApply={onApply} />);
    await expand();
    fireEvent.change(screen.getByLabelText("Stop loss"), { target: { value: "5.0" } });
    fireEvent.change(screen.getByLabelText("Buy threshold"), { target: { value: "0.7" } });

    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));
    expect(onApply).toHaveBeenCalledWith({
      STOP_LOSS_PCT: "5.0",
      SIGNAL_BUY_THRESHOLD: "0.7",
    });
  });

  it("A3: Default settings stages the shipped defaults as pending — still no direct write", async () => {
    mockGet.mockResolvedValue(payload({ STOP_LOSS_PCT: "5.5" }));
    const onApply = vi.fn();
    render(<AdvancedTradingSection onApply={onApply} />);
    await expand();
    fireEvent.click(screen.getByRole("button", { name: /default settings/i }));

    expect(saveMock()).not.toHaveBeenCalled();
    expect(onApply).not.toHaveBeenCalled();
    // pending now differs from engine-current (5.5 -> default 7.0) => apply carries it
    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({ STOP_LOSS_PCT: "7.0" }),
    );
  });

  it("A4: without changes Apply is inert (no empty payload to the seam)", async () => {
    const onApply = vi.fn();
    render(<AdvancedTradingSection onApply={onApply} />);
    await expand();
    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));
    expect(onApply).not.toHaveBeenCalled();
  });

  it("A5: WITHOUT an onApply prop, Apply runs the REAL save path (modal, POST, persist, restart)", async () => {
    (postTradingSettings as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      success: true,
      applied: {},
      changes: [{ key: "STOP_LOSS_PCT", from: "7.0", to: "5.0" }],
      detail: "recorded",
    });
    const restartMock = vi.fn().mockResolvedValue(undefined);
    (window as unknown as { aaagents: Record<string, unknown> }).aaagents.restartEngine =
      restartMock;
    render(<AdvancedTradingSection />);
    await expand();
    fireEvent.change(screen.getByLabelText("Stop loss"), { target: { value: "5.0" } });
    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("7.0");
    expect(dialog.textContent).toContain("5.0");

    fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
    await waitFor(() => expect(restartMock).toHaveBeenCalled());
    expect(postTradingSettings).toHaveBeenCalledWith(
      { STOP_LOSS_PCT: "5.0" },
      expect.stringMatching(/audit trail/i),
      expect.any(String),
    );
    expect(saveMock()).toHaveBeenCalledWith({ STOP_LOSS_PCT: "5.0" });
  });
});
