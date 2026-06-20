import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopApp } from "../console/desktop/DesktopApp";
import { useStore } from "../console/store/useStore";

vi.mock("../lib/api", () => ({
  sendChat: vi.fn(),
  fetchPortfolioSummary: vi.fn().mockResolvedValue(null),
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
  fetchSpecialistReports: vi.fn().mockResolvedValue({ status: "ok", reports: [] }),
}));

/**
 * G3 (#1050): the console shell — sidebar nav switches the active page; Chat is
 * the live page, the data pages render an honest placeholder, and the Decisions
 * page is the HITL/GAP2 stub. Chat transcript persists across navigation (store).
 */
describe("console shell", () => {
  beforeEach(() => {
    useStore.setState({ desktopPage: "chat", chatMessages: [] });
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("defaults to the live Chat page", () => {
    render(<DesktopApp />);
    expect(screen.getByPlaceholderText(/message the engine/i)).toBeTruthy();
  });

  it("sidebar nav switches to the live Settings page", () => {
    render(<DesktopApp />);
    fireEvent.click(screen.getByRole("button", { name: /settings/i }));
    expect(screen.getByText(/Engine, broker & safety/i)).toBeTruthy();
  });

  it("sidebar nav switches to the live Reports (Round Table) page", () => {
    render(<DesktopApp />);
    fireEvent.click(screen.getByRole("button", { name: /reports/i }));
    expect(screen.getByText(/round table/i)).toBeTruthy();
  });

  it("sidebar nav switches to the live Positions page", () => {
    render(<DesktopApp />);
    fireEvent.click(screen.getByRole("button", { name: /positions/i }));
    expect(screen.getByText(/0 positions/i)).toBeTruthy(); // header count, unique
  });

  it("Decisions renders the HITL/GAP2 stub, not a broken view", () => {
    render(<DesktopApp />);
    fireEvent.click(screen.getByRole("button", { name: /decisions/i }));
    expect(screen.getByText(/human-in-the-loop/i)).toBeTruthy();
  });

  it("chat transcript survives navigating away and back", () => {
    useStore.getState().addChatMessage("user", "remember me");
    render(<DesktopApp />);
    expect(screen.getByText("remember me")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /reports/i }));
    expect(screen.queryByText("remember me")).toBeNull(); // off the chat page
    fireEvent.click(screen.getByRole("button", { name: /^chat$/i }));
    expect(screen.getByText("remember me")).toBeTruthy(); // back, still there
  });
});
