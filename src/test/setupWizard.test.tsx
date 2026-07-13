import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * G4-2 (#1050): the setup-wizard flow. Bridge calls are mocked — the test exercises the step
 * machine, the Alpaca validate→keychain-save gate, and that Finish launches the engine.
 *
 * #1977: a "requirements" preflight step is shown FIRST, and the wizard RESUMES on the Alpaca
 * step (name prefilled) when a name was already saved.
 * #1705: the LLM step embeds the vendor-independent LlmProviderCard — the wizard's Continue
 * unlocks once the card reports a provider was applied (local model pulled / cloud key saved).
 */
const validateAlpaca = vi.fn();
const saveSecret = vi.fn();
const saveSetupState = vi.fn();
const startEngine = vi.fn();
const stopEngine = vi.fn();
const provisionOllama = vi.fn();
const getSetupState = vi.fn();
vi.mock("@/lib/desktopBridge", () => ({
  validateAlpaca: (...a: unknown[]) => validateAlpaca(...a),
  saveSecret: (...a: unknown[]) => saveSecret(...a),
  saveSetupState: (...a: unknown[]) => saveSetupState(...a),
  startEngine: (...a: unknown[]) => startEngine(...a),
  stopEngine: (...a: unknown[]) => stopEngine(...a),
  provisionOllama: (...a: unknown[]) => provisionOllama(...a),
  getSetupState: (...a: unknown[]) => getSetupState(...a),
  isDesktop: () => true,
}));

import { SetupWizard } from "../console/setup/SetupWizard";

const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = (name: RegExp) => fireEvent.click(screen.getByRole("button", { name }));
const continueBtn = () => screen.getByRole("button", { name: /^continue$/i }) as HTMLButtonElement;

// #1977: advance past the preflight "requirements" step to the name entry.
const passRequirements = () => click(/get started/i);

describe("SetupWizard (G4-2)", () => {
  beforeEach(() => {
    validateAlpaca.mockReset().mockResolvedValue({ ok: true, status: 200 });
    saveSecret.mockReset().mockResolvedValue({ ok: true, error: null });
    saveSetupState.mockReset().mockResolvedValue(undefined);
    startEngine.mockReset().mockResolvedValue(undefined);
    stopEngine.mockReset().mockResolvedValue(undefined);
    provisionOllama
      .mockReset()
      .mockResolvedValue({ ok: true, model: "mistral:7b-instruct-v0.3-q4_K_M", baseUrl: "http://127.0.0.1:11434" });
    getSetupState.mockReset().mockResolvedValue({});
  });

  it("#1977 preflight: states what you'll need BEFORE any input, with a free-account link", async () => {
    render(<SetupWizard onComplete={vi.fn()} />);
    expect(screen.getByText(/what you'll need/i)).toBeTruthy();
    expect(screen.getByText(/free alpaca paper account/i)).toBeTruthy();
    expect(screen.getByText(/no deposit/i)).toBeTruthy();
    // one-click path to create the (free) account
    expect(screen.getByRole("link", { name: /create a free account/i })).toBeTruthy();
    // no name field yet — the requirement is stated before any work
    expect(screen.queryByLabelText("name")).toBeNull();
    passRequirements();
    expect(await screen.findByLabelText("name")).toBeTruthy();
  });

  it("#1977 resume: with a saved name, mounts on Alpaca and prefills the name", async () => {
    getSetupState.mockResolvedValue({ name: "Georg" });
    render(<SetupWizard onComplete={vi.fn()} />);
    await screen.findByText(/connect your broker/i);
    expect(screen.queryByText(/what you'll need/i)).toBeNull(); // requirements skipped
    click(/^back$/i); // Alpaca → welcome reveals the prefilled name
    expect((screen.getByLabelText("name") as HTMLInputElement).value).toBe("Georg");
  });

  it("happy path: requirements → welcome → Alpaca (validated + saved) → local model → launch", async () => {
    const onComplete = vi.fn();
    render(<SetupWizard onComplete={onComplete} />);
    passRequirements();

    type("name", "Georg");
    click(/^continue$/i);
    await waitFor(() => expect(saveSetupState).toHaveBeenCalledWith({ name: "Georg" }));

    await screen.findByText(/connect your broker/i);
    type("alpaca-key-id", "pk-id");
    type("alpaca-secret", "sk-secret");
    click(/validate & continue/i);

    await screen.findByText(/choose your ai model/i);
    expect(validateAlpaca).toHaveBeenCalledWith("pk-id", "sk-secret");
    expect(saveSecret).toHaveBeenCalledWith("ALPACA_API_KEY", "pk-id");
    expect(saveSecret).toHaveBeenCalledWith("ALPACA_SECRET_KEY", "sk-secret");

    // Mistral is the default local pick — provisioning it unlocks Continue.
    click(/download & use mistral/i);
    await waitFor(() => expect(provisionOllama).toHaveBeenCalled());
    expect(saveSetupState).toHaveBeenCalledWith({
      LLM_PROVIDER: "ollama",
      LOCAL_LLM_MODEL: "mistral:7b-instruct-v0.3-q4_K_M",
      OLLAMA_BASE_URL: "http://127.0.0.1:11434",
    });
    await waitFor(() => expect(continueBtn().disabled).toBe(false));
    fireEvent.click(continueBtn());

    await screen.findByText(/you're ready/i);
    click(/launch/i);
    await waitFor(() => expect(startEngine).toHaveBeenCalled());
    expect(onComplete).toHaveBeenCalled();
  });

  it("rejects bad Alpaca keys, stays on the step, writes no secret", async () => {
    validateAlpaca.mockResolvedValue({ ok: false, status: 401 });
    render(<SetupWizard onComplete={vi.fn()} />);
    passRequirements();
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
    passRequirements();
    type("name", "G");
    click(/^continue$/i);
    await screen.findByText(/connect your broker/i);
    type("alpaca-key-id", "pk");
    type("alpaca-secret", "sk");
    click(/validate & continue/i);
    await screen.findByText(/choose your ai model/i);
    click(/download & use mistral/i);
    await waitFor(() => expect(continueBtn().disabled).toBe(false));
    fireEvent.click(continueBtn());
    await screen.findByText(/you're ready/i);
    click(/launch/i);
    await waitFor(() => expect(screen.getByText(/couldn't start the engine/i)).toBeTruthy());
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("cloud (Gemini) path saves the key to the keychain + records the provider", async () => {
    render(<SetupWizard onComplete={vi.fn()} />);
    passRequirements();
    type("name", "G");
    click(/^continue$/i);
    await screen.findByText(/connect your broker/i);
    type("alpaca-key-id", "pk");
    type("alpaca-secret", "sk");
    click(/validate & continue/i);
    await screen.findByText(/choose your ai model/i);
    click(/google gemini/i);
    type("gemini-key", "g-key");
    click(/save & use google gemini/i);
    await waitFor(() => expect(saveSecret).toHaveBeenCalledWith("GEMINI_API_KEY", "g-key"));
    expect(saveSetupState).toHaveBeenCalledWith({ LLM_PROVIDER: "gemini" });
    await waitFor(() => expect(continueBtn().disabled).toBe(false));
  });

  it("cloud (Anthropic) path is vendor-independent — stores the ANTHROPIC slot", async () => {
    render(<SetupWizard onComplete={vi.fn()} />);
    passRequirements();
    type("name", "G");
    click(/^continue$/i);
    await screen.findByText(/connect your broker/i);
    type("alpaca-key-id", "pk");
    type("alpaca-secret", "sk");
    click(/validate & continue/i);
    await screen.findByText(/choose your ai model/i);
    click(/claude · anthropic/i);
    type("anthropic-key", "sk-ant-1");
    click(/save & use claude/i);
    await waitFor(() => expect(saveSecret).toHaveBeenCalledWith("ANTHROPIC_API_KEY", "sk-ant-1"));
    expect(saveSetupState).toHaveBeenCalledWith({ LLM_PROVIDER: "anthropic" });
  });

  it("Ollama that can't auto-install surfaces manual guidance, keeps Continue locked", async () => {
    provisionOllama.mockResolvedValue({
      ok: false,
      needsManual: true,
      error: "Install Ollama from ollama.com, then retry.",
    });
    render(<SetupWizard onComplete={vi.fn()} />);
    passRequirements();
    type("name", "G");
    click(/^continue$/i);
    await screen.findByText(/connect your broker/i);
    type("alpaca-key-id", "pk");
    type("alpaca-secret", "sk");
    click(/validate & continue/i);
    await screen.findByText(/choose your ai model/i);
    click(/download & use mistral/i);
    await waitFor(() => expect(screen.getByText(/install ollama from ollama\.com/i)).toBeTruthy());
    expect(saveSetupState).not.toHaveBeenCalledWith(expect.objectContaining({ LLM_PROVIDER: "ollama" }));
    expect(continueBtn().disabled).toBe(true); // still gated on the LLM step
  });

  it("G5-3b: the requirements step offers a 'Skip for now' demo path that calls onSkip", () => {
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
