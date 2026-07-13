import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopApp } from "../console/desktop/DesktopApp";
import { useStore } from "../console/store/useStore";

vi.mock("../lib/api", () => ({
  fetchHealth: vi.fn().mockResolvedValue(null),
  sendChat: vi.fn(),
  fetchPortfolioSummary: vi.fn().mockResolvedValue(null),
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
  fetchSpecialistReports: vi.fn().mockResolvedValue({ status: "ok", reports: [] }),
  fetchBenchmarkEquity: vi.fn().mockResolvedValue({ points: [], spy_points: [] }),
  fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
}));

/**
 * G3 (#1050): the console shell — sidebar nav switches the active page; Chat is
 * the live page, the data pages render an honest placeholder, and the Decisions
 * page is the HITL/GAP2 stub. Chat transcript persists across navigation (store).
 */
describe("console shell", () => {
  beforeEach(() => {
    useStore.setState({ desktopPage: "overview", chatMessages: [] });
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("defaults to the Overview page, not chat", () => {
    render(<DesktopApp />);
    // Overview is the app entry point now; the chat composer must NOT be the landing view.
    expect(screen.queryByPlaceholderText(/message the engine/i)).toBeNull();
  });

  it("sidebar nav switches to the live Settings page", () => {
    render(<DesktopApp />);
    fireEvent.click(screen.getByRole("button", { name: /settings/i }));
    expect(screen.getByRole("heading", { name: /Trading/i })).toBeTruthy();
  });

  it("sidebar nav switches to the live Reports (Specialist reports) page", () => {
    render(<DesktopApp />);
    fireEvent.click(screen.getByRole("button", { name: /reports/i }));
    expect(screen.getByText("Specialist Research")).toBeTruthy(); // the eyebrow header, exact
  });

  it("sidebar nav switches to the live Positions page", () => {
    render(<DesktopApp />);
    fireEvent.click(screen.getByRole("button", { name: /positions/i }));
    expect(screen.getByText(/0 Active Positions/i)).toBeTruthy(); // header count, unique
  });

  it("Decisions renders the live Round-Table decisions page (not a stub)", () => {
    render(<DesktopApp />);
    fireEvent.click(screen.getByRole("button", { name: /decisions/i }));
    expect(screen.getByText(/round-table decisions/i)).toBeTruthy();
    expect(screen.getByText(/no decisions yet/i)).toBeTruthy();
  });

  it("chat transcript survives navigating away and back", () => {
    useStore.setState({ desktopPage: "chat" }); // this test starts on the chat page
    useStore.getState().addChatMessage("user", "remember me");
    render(<DesktopApp />);
    expect(screen.getByText("remember me")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /reports/i }));
    expect(screen.queryByText("remember me")).toBeNull(); // off the chat page
    fireEvent.click(screen.getByRole("button", { name: /^chat$/i }));
    expect(screen.getByText("remember me")).toBeTruthy(); // back, still there
  });
});
