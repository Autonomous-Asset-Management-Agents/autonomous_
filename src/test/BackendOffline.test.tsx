import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import BackendOffline from "../pages/BackendOffline";

describe("BackendOffline Component", () => {
  beforeEach(() => {
    // Reset any mocks
    vi.clearAllMocks();
    
    // Mock the global Tauri object to simulate Tauri environment
    Object.defineProperty(window, "__TAURI__", {
      value: {
        invoke: vi.fn(),
      },
      writable: true,
    });
  });

  it("renders the offline message and instructions correctly", () => {
    render(<BackendOffline />);
    
    // Check main heading
    expect(screen.getByText("Backend Offline")).toBeInTheDocument();
    
    // Check description
    expect(screen.getByText(/The AAAgents Desktop app could not connect to the local Python backend/i)).toBeInTheDocument();
    
    // Check instructions
    expect(screen.getByText("setup.ps1")).toBeInTheDocument();
    expect(screen.getByText("docker compose up -d")).toBeInTheDocument();
    
    // Check retry button
    expect(screen.getByRole("button", { name: /Retry Connection/i })).toBeInTheDocument();
  });
});
