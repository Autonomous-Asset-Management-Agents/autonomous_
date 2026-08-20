import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DailyUpdatesCard } from "../console/desktop/DailyUpdatesCard";
import { useStore } from "@/console/store/useStore";

/**
 * Daily Report card (Settings → Notifications). An automatic daily report delivered to the
 * user's OWN private channels (chat webhook / email). The whole config is shown fully
 * expanded — no accordion ("Manage") and no per-channel on/off toggle. It is a Senior (PRO)
 * feature: Basic sees the very same config greyed out + non-functional, with an Upgrade CTA.
 * Delivery is private only — no public social posting — and every report carries the legal notice.
 */
const setBridge = (impl: Record<string, unknown> | undefined) => {
  (window as unknown as { aaagents?: unknown }).aaagents = impl;
};

const bridge = (over: Record<string, unknown> = {}) => ({
  isDesktop: true,
  getSetupState: vi.fn().mockResolvedValue({}),
  saveSetupState: vi.fn().mockResolvedValue(undefined),
  saveSecret: vi.fn().mockResolvedValue({ ok: true }),
  claimBeta: vi.fn().mockResolvedValue({ status: "claimed" }),
  ...over,
});

describe("Daily Report card", () => {
  beforeEach(() => {
    setBridge(bridge());
    useStore.setState({ tier: null });
  });
  afterEach(() => {
    setBridge(undefined);
    useStore.setState({ tier: null });
    vi.clearAllMocks();
  });

  it("Basic (not Senior) sees the config greyed out + an Upgrade CTA, not a functional form", () => {
    useStore.setState({ tier: "BASIC" });
    render(<DailyUpdatesCard />);

    // Upsell present
    expect(screen.getByText(/Automated daily reports are a Senior feature/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /upgrade to senior/i })).toBeTruthy();

    // The full config is now VISIBLE (a preview of what unlocks) but non-functional
    expect(screen.getByText(/Send daily at/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /save configuration/i })).toBeDisabled();

    // No accordion "Manage" and no on/off toggles anywhere
    expect(screen.queryByRole("button", { name: /manage/i })).toBeNull();
    expect(screen.queryByLabelText("webhook-toggle")).toBeNull();
    expect(screen.queryByLabelText("email-toggle")).toBeNull();
  });

  it("Senior (PRO) sees the full config expanded — no manage, no on/off toggles", () => {
    useStore.setState({ tier: "PRO" });
    render(<DailyUpdatesCard />);

    // Fully expanded immediately — no click needed; both channels' fields are shown directly
    expect(screen.getByText(/Send daily at/i)).toBeTruthy();
    expect(screen.getByLabelText("webhook-url")).toBeTruthy(); // slack default → URL field visible
    expect(screen.getByLabelText("report-recipient-email")).toBeTruthy(); // email fields visible

    // The removed controls
    expect(screen.queryByRole("button", { name: /manage/i })).toBeNull();
    expect(screen.queryByLabelText("webhook-toggle")).toBeNull();
    expect(screen.queryByLabelText("email-toggle")).toBeNull();

    // Legal notice always present; no public social posting
    expect(screen.getAllByText(/not investment advice/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/LinkedIn/i)).toBeNull();
    expect(screen.queryByText(/Instagram/i)).toBeNull();
  });

  it("Senior configures a chat webhook and persists it (setup + keychain) — no toggle needed", async () => {
    const saveSetupState = vi.fn().mockResolvedValue(undefined);
    const saveSecret = vi.fn().mockResolvedValue({ ok: true });
    setBridge(bridge({ saveSetupState, saveSecret }));
    useStore.setState({ tier: "PRO" });

    render(<DailyUpdatesCard />);
    fireEvent.change(screen.getByLabelText("webhook-url"), {
      target: { value: "https://hooks.slack.com/services/T/B/x" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save configuration/i }));

    await waitFor(() =>
      expect(saveSecret).toHaveBeenCalledWith("WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x"),
    );
    expect(saveSetupState).toHaveBeenCalled();
  });

  it("shows a per-channel Active/Off marker driven by configuration", () => {
    useStore.setState({ tier: "PRO" });
    render(<DailyUpdatesCard />);
    // Nothing configured yet → both channels read "Off"
    expect(screen.getAllByText("Off").length).toBeGreaterThanOrEqual(2);
    // Configure the chat webhook → that channel flips to "Active"
    fireEvent.change(screen.getByLabelText("webhook-url"), {
      target: { value: "https://hooks.slack.com/services/T/B/x" },
    });
    expect(screen.getByText("Active")).toBeTruthy();
  });

  it("Telegram uses bot token + chat ID instead of a webhook URL", () => {
    useStore.setState({ tier: "PRO" });
    render(<DailyUpdatesCard />);
    fireEvent.click(screen.getByLabelText("provider-telegram"));

    expect(screen.getByLabelText("telegram-token")).toBeTruthy();
    expect(screen.getByLabelText("telegram-chat-id")).toBeTruthy();
    expect(screen.queryByLabelText("webhook-url")).toBeNull();
  });
});
