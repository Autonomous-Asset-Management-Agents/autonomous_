// XAI-1 / XAI-T9 command-surface (#1338): pins the Chat wiring. With VITE_XAI_CONSOLE_EMBED
// on, a typed navigation command drives the console (setDesktopPage) and does NOT hit the
// engine; with the flag off (default) behaviour is unchanged. A real question always goes to
// the engine regardless of the flag.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Chat } from "../console/desktop/pages/Chat";
import { useStore } from "../console/store/useStore";

vi.mock("../lib/api", () => ({ sendChat: vi.fn() }));
import { sendChat } from "../lib/api";

function send(text: string) {
  fireEvent.change(screen.getByPlaceholderText(/message the engine/i), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole("button", { name: /send/i }));
}

describe("console command-surface (XAI-T9, #1338)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useStore.setState({ chatMessages: [], desktopPage: "chat" });
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => {
    delete import.meta.env.VITE_XAI_CONSOLE_EMBED;
  });

  it("navigates instead of calling the engine for a nav command (flag on)", async () => {
    import.meta.env.VITE_XAI_CONSOLE_EMBED = "true";
    render(<Chat />);
    send("öffne das Dashboard");
    await waitFor(() => expect(useStore.getState().desktopPage).toBe("overview"));
    expect(sendChat).not.toHaveBeenCalled();
  });

  it("does NOT intercept when the flag is off (default) — goes to the engine", () => {
    vi.mocked(sendChat).mockResolvedValue("ok");
    render(<Chat />);
    send("öffne das Dashboard");
    expect(sendChat).toHaveBeenCalledWith("öffne das Dashboard");
    expect(useStore.getState().desktopPage).toBe("chat");
  });

  it("still routes a real question to the engine even with the flag on", () => {
    import.meta.env.VITE_XAI_CONSOLE_EMBED = "true";
    vi.mocked(sendChat).mockResolvedValue("the market is open");
    render(<Chat />);
    send("is the market open?");
    expect(sendChat).toHaveBeenCalledWith("is the market open?");
    expect(useStore.getState().desktopPage).toBe("chat");
  });
});
