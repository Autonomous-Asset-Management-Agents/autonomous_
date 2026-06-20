import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Chat } from "../console/desktop/pages/Chat";
import { useStore } from "../console/store/useStore";

// The console Chat page talks to the engine via the shared api layer (which
// carries the desktop X-Engine-Key automatically — see api.ts/desktopBridge).
vi.mock("../lib/api", () => ({ sendChat: vi.fn() }));
import { sendChat } from "../lib/api";

describe("console Chat page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useStore.setState({ chatMessages: [] }); // transcript lives in the store now
    // jsdom doesn't implement scrollIntoView (the page auto-scrolls to newest).
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("shows the empty state before any message", () => {
    render(<Chat />);
    expect(screen.getByText(/no messages yet/i)).toBeTruthy();
  });

  it("sends the typed message and renders the engine reply", async () => {
    vi.mocked(sendChat).mockResolvedValue("the market is open");
    render(<Chat />);

    const box = screen.getByPlaceholderText(/message the engine/i);
    fireEvent.change(box, { target: { value: "is the market open?" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(sendChat).toHaveBeenCalledWith("is the market open?");
    await waitFor(() => {
      expect(screen.getByText("is the market open?")).toBeTruthy(); // echoed user msg
      expect(screen.getByText("the market is open")).toBeTruthy(); // reply
    });
  });

  it("surfaces a clean line when the engine can't be reached", async () => {
    vi.mocked(sendChat).mockResolvedValue(null);
    render(<Chat />);
    fireEvent.change(screen.getByPlaceholderText(/message the engine/i), {
      target: { value: "hi" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() =>
      expect(screen.getByText(/couldn't reach the engine/i)).toBeTruthy(),
    );
  });

  it("renders the engine's reply verbatim (no frontend content-parsing)", async () => {
    // Review (Iron Dome): the frontend must NOT parse/interpret the LLM's prose.
    // Whatever the engine returns is shown as-is; reply quality is a backend
    // concern, not papered over with substring matching here.
    const raw = "I couldn't generate an answer. Make sure Gemini API is configured.";
    vi.mocked(sendChat).mockResolvedValue(raw);
    render(<Chat />);
    fireEvent.change(screen.getByPlaceholderText(/message the engine/i), {
      target: { value: "hi" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(screen.getByText(raw)).toBeTruthy());
  });
});
