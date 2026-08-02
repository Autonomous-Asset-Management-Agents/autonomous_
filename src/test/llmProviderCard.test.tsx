import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LlmProviderCard, type LlmDeps } from "../console/desktop/LlmProviderCard";

/**
 * Vendor-independent LLM provider card (#1705). Side effects are injected, so these tests drive
 * the real component with a mock bridge — no Electron. Covers: desktop gate, local provisioning
 * (Mistral default tag vs Llama explicit tag), per-vendor cloud keychain slots
 * (Gemini/OpenAI/Anthropic), the onApplied gate signal, and reflecting the persisted provider.
 */
function makeDeps(over: Partial<LlmDeps> = {}) {
  const fns = {
    getSetupState: vi.fn().mockResolvedValue({}),
    saveSetupState: vi.fn().mockResolvedValue(undefined),
    saveSecret: vi.fn().mockResolvedValue({ ok: true, error: null }),
    provisionOllama: vi
      .fn()
      .mockResolvedValue({ ok: true, model: "mistral:7b-instruct-v0.3-q4_K_M", baseUrl: "http://127.0.0.1:11434" }),
    startEngine: vi.fn().mockResolvedValue(undefined),
    stopEngine: vi.fn().mockResolvedValue(undefined),
    listOllamaModels: vi.fn().mockResolvedValue({ reachable: true, models: [] }),
    getKeychainStatus: vi.fn().mockResolvedValue({}),
  };
  const deps: LlmDeps = { isDesktop: () => true, ...fns, ...over };
  // Spread the EFFECTIVE deps (overrides applied) so a destructured mock is the one the card calls.
  return { deps, ...deps };
}

const click = (name: RegExp) => fireEvent.click(screen.getByRole("button", { name }));

describe("LlmProviderCard (#1705)", () => {
  it("renders local + cloud providers (vendor-independent)", () => {
    render(<LlmProviderCard deps={makeDeps().deps} />);
    for (const name of ["Mistral 7B", "Llama 3.1 8B", "Google Gemini", "ChatGPT · OpenAI", "Claude · Anthropic"]) {
      expect(screen.getByText(name)).toBeTruthy();
    }
  });

  it("renders nothing outside the desktop shell", () => {
    const { container } = render(<LlmProviderCard deps={makeDeps({ isDesktop: () => false }).deps} />);
    expect(container.firstChild).toBeNull();
  });

  it("cloud: OpenAI key goes to the OPENAI_API_KEY slot + records the provider + fires onApplied", async () => {
    const { deps, saveSecret, saveSetupState } = makeDeps();
    const onApplied = vi.fn();
    render(<LlmProviderCard deps={deps} onApplied={onApplied} />);
    click(/ChatGPT/i);
    fireEvent.change(screen.getByLabelText("openai-key"), { target: { value: "sk-openai" } });
    click(/save & use chatgpt/i);
    await waitFor(() => expect(saveSecret).toHaveBeenCalledWith("OPENAI_API_KEY", "sk-openai"));
    expect(saveSetupState).toHaveBeenCalledWith({ LLM_PROVIDER: "openai" });
    expect(onApplied).toHaveBeenCalledWith("openai");
  });

  it("cloud: Anthropic key goes to the ANTHROPIC_API_KEY slot", async () => {
    const { deps, saveSecret, saveSetupState } = makeDeps();
    render(<LlmProviderCard deps={deps} />);
    click(/Claude · Anthropic/i);
    fireEvent.change(screen.getByLabelText("anthropic-key"), { target: { value: "sk-ant-x" } });
    click(/save & use claude/i);
    await waitFor(() => expect(saveSecret).toHaveBeenCalledWith("ANTHROPIC_API_KEY", "sk-ant-x"));
    expect(saveSetupState).toHaveBeenCalledWith({ LLM_PROVIDER: "anthropic" });
  });

  it("local: Mistral (recommended) provisions with the vetted default tag (no explicit model)", async () => {
    const { deps, provisionOllama, saveSetupState } = makeDeps();
    const onApplied = vi.fn();
    render(<LlmProviderCard deps={deps} onApplied={onApplied} />);
    // Mistral is selected by default.
    click(/download & activate mistral/i);
    await waitFor(() => expect(provisionOllama).toHaveBeenCalledWith(expect.any(Function), undefined));
    expect(saveSetupState).toHaveBeenCalledWith({
      LLM_PROVIDER: "ollama",
      LOCAL_LLM_MODEL: "mistral:7b-instruct-v0.3-q4_K_M",
      OLLAMA_BASE_URL: "http://127.0.0.1:11434",
    });
    expect(onApplied).toHaveBeenCalledWith("mistral");
  });

  it("local: Llama pulls the explicit llama3.1:8b tag", async () => {
    const { deps, provisionOllama } = makeDeps({
      provisionOllama: vi.fn().mockResolvedValue({ ok: true, model: "llama3.1:8b", baseUrl: "http://127.0.0.1:11434" }),
    });
    render(<LlmProviderCard deps={deps} />);
    click(/Llama 3\.1 8B/i);
    click(/Download & Activate Llama 3\.1 8B/i);
    await waitFor(() => expect(provisionOllama).toHaveBeenCalledWith(expect.any(Function), "llama3.1:8b"));
  });

  it("reflects the persisted provider (Gemini in use)", async () => {
    const { deps } = makeDeps({ getSetupState: vi.fn().mockResolvedValue({ LLM_PROVIDER: "gemini" }) });
    render(<LlmProviderCard deps={deps} />);
    expect(await screen.findByText(/Current: Google Gemini/i)).toBeTruthy();
  });

  // E (#2130): the card showed the CONFIGURED provider ("Mistral in use") but never checked whether
  // the local model is ACTUALLY installed/pulled in Ollama — so a selected-but-not-downloaded model
  // read as ready. Surface a clear warning + per-tile install status from the Ollama tag list.
  it("warns when the selected LOCAL model is not installed in Ollama (the reported gap)", async () => {
    const { deps } = makeDeps({
      getSetupState: vi.fn().mockResolvedValue({
        LLM_PROVIDER: "ollama",
        LOCAL_LLM_MODEL: "mistral:7b-instruct-v0.3-q4_K_M",
      }),
      listOllamaModels: vi.fn().mockResolvedValue({ reachable: true, models: [] }),
    });
    render(<LlmProviderCard deps={deps} />);
    expect(await screen.findByText(/isn.?t installed yet/i)).toBeTruthy();
  });

  it("shows NO not-installed warning when the selected LOCAL model IS present in the tag list", async () => {
    const { deps } = makeDeps({
      getSetupState: vi.fn().mockResolvedValue({
        LLM_PROVIDER: "ollama",
        LOCAL_LLM_MODEL: "mistral:7b-instruct-v0.3-q4_K_M",
      }),
      listOllamaModels: vi
        .fn()
        .mockResolvedValue({ reachable: true, models: ["mistral:7b-instruct-v0.3-q4_K_M"] }),
    });
    render(<LlmProviderCard deps={deps} />);
    expect(await screen.findByText(/Current: Mistral 7B/i)).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(/isn.?t installed yet/i)).toBeNull());
  });

  it("presence marker: cloud key input shows a masked 'stored' placeholder when the slot is present — never the value", async () => {
    const { deps } = makeDeps({
      getSetupState: vi.fn().mockResolvedValue({ LLM_PROVIDER: "gemini" }),
      getKeychainStatus: vi.fn().mockResolvedValue({ GEMINI_API_KEY: true }),
    });
    render(<LlmProviderCard deps={deps} />);
    const input = (await screen.findByLabelText("gemini-key")) as HTMLInputElement;
    await waitFor(() => expect(input.placeholder).toMatch(/stored \(leave blank to keep\)/i));
    expect(input.placeholder).toContain("•");
    // purely visual — the field stays empty so an empty submit keeps the stored key
    expect(input.value).toBe("");
  });

  it("presence marker: cloud key input keeps its key-format hint when the slot is empty", async () => {
    const { deps } = makeDeps({
      getSetupState: vi.fn().mockResolvedValue({ LLM_PROVIDER: "gemini" }),
      getKeychainStatus: vi.fn().mockResolvedValue({ GEMINI_API_KEY: false }),
    });
    render(<LlmProviderCard deps={deps} />);
    const input = (await screen.findByLabelText("gemini-key")) as HTMLInputElement;
    await waitFor(() => expect(input.placeholder).toBe("AIza…"));
    expect(input.placeholder).not.toMatch(/stored/i);
  });

  it("marks each local tile Installed / Not installed from the Ollama tag list", async () => {
    const { deps } = makeDeps({
      listOllamaModels: vi
        .fn()
        .mockResolvedValue({ reachable: true, models: ["mistral:7b-instruct-v0.3-q4_K_M"] }),
    });
    render(<LlmProviderCard deps={deps} />);
    // Mistral pulled but NOT the active provider (default state) -> "Installed" (not "& Active");
    // Llama + Phi absent -> Not Installed. Nothing is active, so no "Installed & Active" anywhere.
    expect(await screen.findByText("Installed")).toBeTruthy();
    expect((await screen.findAllByText("Not Installed")).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("Installed & Active")).toBeNull();
  });

  it("offers the lightweight phi3.5 option for RAM-constrained machines", () => {
    render(<LlmProviderCard deps={makeDeps().deps} />);
    expect(screen.getByText("Phi-3.5 (light)")).toBeTruthy();
  });

  // Operator-reported bug: switching to another model left the previous one showing
  // "Installed & Active". Only the ACTIVE model may say "& Active"; other pulled models say "Installed".
  it("badge: only the ACTIVE model shows 'Installed & Active'; other pulled models show 'Installed'", async () => {
    const { deps } = makeDeps({
      getSetupState: vi.fn().mockResolvedValue({
        LLM_PROVIDER: "ollama",
        LOCAL_LLM_MODEL: "mistral:7b-instruct-v0.3-q4_K_M", // mistral is the ACTIVE model
      }),
      listOllamaModels: vi.fn().mockResolvedValue({
        reachable: true,
        models: ["mistral:7b-instruct-v0.3-q4_K_M", "phi3.5"], // both pulled
      }),
    });
    render(<LlmProviderCard deps={deps} />);
    expect(await screen.findByText("Installed & Active")).toBeTruthy(); // mistral (active)
    expect(await screen.findByText("Installed")).toBeTruthy(); // phi (pulled, not active)
  });

  // #2587 (GUI quickie 2): the "How it works" info box claimed "the model never
  // trades on its own" — false in Full-autonomous mode, where orders execute
  // without per-trade approval (hitlAutonomy.ts). The copy must defer to the
  // Autonomy setting instead of promising universal per-trade approval.
  it("copy honesty: 'How it works' defers to the Autonomy setting, never claims the model never trades on its own", () => {
    render(<LlmProviderCard deps={makeDeps().deps} />);
    expect(screen.queryByText(/never trades on its own/i)).toBeNull();
    expect(screen.getByText(/according to your Autonomy setting/i)).toBeTruthy();
  });
});
