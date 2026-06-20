/**
 * Journey: Support (ask the engine) — UX E2E #1050.
 *
 * The operator console opens on the Chat surface — the in-app support channel
 * to the running engine. Drives a full support conversation through the real
 * desktop shell: land on chat → ask → user echo + engine reply → a follow-up
 * (transcript persists) → and the clean degraded line when the engine is down.
 *
 * See src/test/journeys/README.md → "J2 Support".
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DesktopApp } from "@/console/desktop/DesktopApp";
import { useStore } from "@/console/store/useStore";
import { makeBridge, installBridge, resetBridge } from "../fixtures/mockBridge";
import * as fx from "../fixtures/consoleFixtures";

// The Chat page reaches the engine through the shared api layer; the trading
// pages aren't mounted on the chat route, but stub the fetchers too so the
// shell is import-safe regardless of which page renders.
vi.mock("@/lib/api", () => ({
  sendChat: vi.fn(),
  fetchPortfolioSummary: vi.fn().mockResolvedValue(null),
  fetchBenchmarkEquity: vi.fn().mockResolvedValue({ points: [], spy_points: [] }),
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
}));
import { sendChat } from "@/lib/api";

const ask = (text: string) => {
  fireEvent.change(screen.getByPlaceholderText(/message the engine/i), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /send/i }));
};

describe("Journey · Support (ask the engine)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = (() => {}) as never;
    useStore.setState({ chatMessages: [], desktopPage: "chat" });
    installBridge(makeBridge().bridge);
  });
  afterEach(() => resetBridge());

  it("lands on the support surface with an empty-state prompt", () => {
    render(<DesktopApp />);
    expect(screen.getByPlaceholderText(/message the engine/i)).toBeTruthy();
    expect(screen.getByText(/no messages yet/i)).toBeTruthy();
  });

  it("a question gets echoed and answered by the engine", async () => {
    vi.mocked(sendChat).mockResolvedValue(fx.chat.reply);
    render(<DesktopApp />);

    ask(fx.chat.question);

    expect(sendChat).toHaveBeenCalledWith(fx.chat.question);
    await waitFor(() => {
      expect(screen.getByText(fx.chat.question)).toBeTruthy(); // echoed user message
      expect(screen.getByText(fx.chat.reply)).toBeTruthy(); // engine reply
    });
  });

  it("a multi-turn conversation keeps the full transcript", async () => {
    vi.mocked(sendChat)
      .mockResolvedValueOnce(fx.chat.reply)
      .mockResolvedValueOnce(fx.chat.followUpReply);
    render(<DesktopApp />);

    ask(fx.chat.question);
    await waitFor(() => expect(screen.getByText(fx.chat.reply)).toBeTruthy());

    ask(fx.chat.followUp);
    await waitFor(() => expect(screen.getByText(fx.chat.followUpReply)).toBeTruthy());

    // Both turns are still on screen.
    expect(screen.getByText(fx.chat.question)).toBeTruthy();
    expect(screen.getByText(fx.chat.followUp)).toBeTruthy();
    expect(screen.getByText(fx.chat.reply)).toBeTruthy();
    expect(useStore.getState().chatMessages).toHaveLength(4); // 2 user + 2 assistant
  });

  it("shows a clean line when the engine can't be reached", async () => {
    vi.mocked(sendChat).mockResolvedValue(null);
    render(<DesktopApp />);

    ask(fx.chat.question);

    await waitFor(() => expect(screen.getByText(/couldn't reach the engine/i)).toBeTruthy());
  });
});
