import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * G4-2 (#1050): the setup-wizard flow. Bridge calls are mocked — the test
 * exercises the step machine, the Alpaca validate→keychain-save gate, the
 * Gemini-key path, and that Finish launches the engine. The step machine is the
 * gate: Finish is only reachable after Alpaca validates and an LLM is chosen.
 */
const validateAlpaca = vi.fn();
const saveSecret = vi.fn();
const saveSetupState = vi.fn();
const startEngine = vi.fn();
const provisionOllama = vi.fn();
vi.mock("@/lib/desktopBridge", () => ({
  validateAlpaca: (...a: unknown[]) => validateAlpaca(...a),
  saveSecret: (...a: unknown[]) => saveSecret(...a),
  saveSetupState: (...a: unknown[]) => saveSetupState(...a),
  startEngine: (...a: unknown[]) => startEngine(...a),
  provisionOllama: (...a: unknown[]) => provisionOllama(...a),
}));

import { SetupWizard } from "../console/setup/SetupWizard";

const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = (name: RegExp) => fireEvent.click(screen.getByRole("button", { name }));

describe("SetupWizard (G4-2)", () => {
  beforeEach(() => {
    validateAlpaca.mockReset().mockResolvedValue({ ok: true, status: 200 });
    saveSecret.mockReset().mockResolvedValue({ ok: true, error: null });
    saveSetupState.mockReset().mockResolvedValue(undefined);
    startEngine.mockReset().mockResolvedValue(undefined);
    provisionOllama
      .mockReset()
      .mockResolvedValue({ ok: true, model: "llama3.2", baseUrl: "http://127.0.0.1:11434" });
  });

  it("happy path: welcome → Alpaca (validated + saved) → Ollama → launch", async () => {
    const onComplete = vi.fn();
    render(<SetupWizard onComplete={onComplete} />);

    type("name", "Georg");
    click(/^continue$/i);
    await waitFor(() => expect(saveSetupState).toHaveBeenCalledWith({ name: "Georg" }));

    await screen.findByText(/connect your broker/i);
    type("alpaca-key-id", "pk-id");
    type("alpaca-secret", "sk-secret");
    click(/validate & continue/i);

    await screen.findByText(/choose your llm/i);
    expect(validateAlpaca).toHaveBeenCalledWith("pk-id", "sk-secret");
    expect(saveSecret).toHaveBeenCalledWith("ALPACA_API_KEY", "pk-id");
    expect(saveSecret).toHaveBeenCalledWith("ALPACA_SECRET_KEY", "sk-secret");

    click(/local \(ollama\)/i);
    click(/^continue$/i);
    await waitFor(() => expect(provisionOllama).toHaveBeenCalled());
    expect(saveSetupState).toHaveBeenCalledWith({
      LLM_PROVIDER: "ollama",
      LOCAL_LLM_MODEL: "llama3.2",
      OLLAMA_BASE_URL: "http://127.0.0.1:11434",
    });

    await screen.findByText(/you're ready/i);
    click(/launch/i);
    await waitFor(() => expect(startEngine).toHaveBeenCalled());
    expect(onComplete).toHaveBeenCalled();
  });

  it("rejects bad Alpaca keys, stays on the step, writes no secret", async () => {
    validateAlpaca.mockResolvedValue({ ok: false, status: 401 });
    render(<SetupWizard onComplete={vi.fn()} />);
    type("name", "G");
    click(/^continue$/i);
    await screen.findByText(/connect your broker/i);
    type("alpaca-key-id", "bad");
    type("alpaca-secret", "bad");
    click(/validate & continue/i);
    await waitFor(() => expect(screen.getByText(/rejected these keys/i)).toBeTruthy());
    expect(saveSecret).not.toHaveBeenCalled();
    expect(screen.getByText(/connect your broker/i)).toBeTruthy(); // still on Alpaca
  });

  it("surfaces an engine-start failure at Finish instead of a dead end", async () => {
    startEngine.mockRejectedValue(new Error("spawn failed"));
    const onComplete = vi.fn();
    render(<SetupWizard onComplete={onComplete} />);
    type("name", "G");
    click(/^continue$/i);
    await screen.findByText(/connect your broker/i);
    type("alpaca-key-id", "pk");
    type("alpaca-secret", "sk");
    click(/validate & continue/i);
    await screen.findByText(/choose your llm/i);
    click(/local \(ollama\)/i);
    click(/^continue$/i);
    await screen.findByText(/you're ready/i);
    click(/launch/i);
    await waitFor(() => expect(screen.getByText(/couldn't start the engine/i)).toBeTruthy());
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("Gemini path saves the key before finishing", async () => {
    render(<SetupWizard onComplete={vi.fn()} />);
    type("name", "G");
    click(/^continue$/i);
    await screen.findByText(/connect your broker/i);
    type("alpaca-key-id", "pk");
    type("alpaca-secret", "sk");
    click(/validate & continue/i);
    await screen.findByText(/choose your llm/i);
    click(/cloud \(gemini\)/i);
    type("gemini-key", "g-key");
    click(/^continue$/i);
    await waitFor(() => expect(saveSecret).toHaveBeenCalledWith("GEMINI_API_KEY", "g-key"));
    expect(saveSetupState).toHaveBeenCalledWith({ LLM_PROVIDER: "gemini" });
  });

  it("Ollama that can't auto-install surfaces manual guidance, stays on the step", async () => {
    provisionOllama.mockResolvedValue({
      ok: false,
      needsManual: true,
      error: "Install Ollama from ollama.com, then retry.",
    });
    render(<SetupWizard onComplete={vi.fn()} />);
    type("name", "G");
    click(/^continue$/i);
    await screen.findByText(/connect your broker/i);
    type("alpaca-key-id", "pk");
    type("alpaca-secret", "sk");
    click(/validate & continue/i);
    await screen.findByText(/choose your llm/i);
    click(/local \(ollama\)/i);
    click(/^continue$/i);
    await waitFor(() => expect(screen.getByText(/install ollama from ollama\.com/i)).toBeTruthy());
    expect(saveSetupState).not.toHaveBeenCalledWith(
      expect.objectContaining({ LLM_PROVIDER: "ollama" }),
    );
    expect(screen.getByText(/choose your llm/i)).toBeTruthy(); // still on the LLM step
  });

  it("G5-3b: the welcome step offers a 'Skip for now' demo path that calls onSkip", () => {
    const onSkip = vi.fn();
    render(<SetupWizard onComplete={vi.fn()} onSkip={onSkip} />);
    click(/skip for now/i);
    expect(onSkip).toHaveBeenCalled();
  });

  it("G5-3b: no Skip button when onSkip isn't provided (forced setup)", () => {
    render(<SetupWizard onComplete={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /skip for now/i })).toBeNull();
  });
});
