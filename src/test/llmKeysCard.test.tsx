import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";

vi.mock("@/lib/desktopBridge", () => ({
  isDesktop: () => true,
  getSetupState: vi.fn().mockResolvedValue({ LLM_PROVIDER: "ollama" }),
  saveSecret: vi.fn().mockResolvedValue({ ok: true, error: null }),
  saveSetupState: vi.fn().mockResolvedValue(undefined),
  startEngine: vi.fn().mockResolvedValue(undefined),
  stopEngine: vi.fn().mockResolvedValue(undefined),
}));

import { saveSecret, saveSetupState } from "@/lib/desktopBridge";
import { LlmKeysCard } from "@/console/desktop/LlmKeysCard";

describe("LlmKeysCard (#1705)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the current provider and a Gemini key input", async () => {
    render(<LlmKeysCard />);
    await waitFor(() => expect(screen.getByText(/Ollama/i)).toBeTruthy());
    expect(screen.getByPlaceholderText(/AIza/)).toBeTruthy();
  });

  it("saves the Gemini key to the keychain and records the provider", async () => {
    render(<LlmKeysCard />);
    fireEvent.change(screen.getByPlaceholderText(/AIza/), {
      target: { value: "AIza-test-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save Gemini key/i }));
    await waitFor(() =>
      expect(saveSecret).toHaveBeenCalledWith("GEMINI_API_KEY", "AIza-test-key"),
    );
    expect(saveSetupState).toHaveBeenCalledWith({ LLM_PROVIDER: "gemini" });
  });

  it("does not save an empty key", () => {
    render(<LlmKeysCard />);
    fireEvent.click(screen.getByRole("button", { name: /Save Gemini key/i }));
    expect(saveSecret).not.toHaveBeenCalled();
  });
});
