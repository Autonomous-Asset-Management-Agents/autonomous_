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
  };
  const deps: LlmDeps = { isDesktop: () => true, ...fns, ...over };
  // Spread the EFFECTIVE deps (overrides applied) so a destructured mock is the one the card calls.
  return { deps, ...deps };
}

const click = (name: RegExp) => fireEvent.click(screen.getByRole("button", { name }));

describe("LlmProviderCard (#1705)", () => {
  it("renders local + cloud providers (vendor-independent)", () => {
    render(<LlmProviderCard deps={makeDeps().deps} />);
    for (const name of ["Mistral 7B", "Llama 3.2", "Google Gemini", "ChatGPT · OpenAI", "Claude · Anthropic"]) {
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
    click(/download & use mistral/i);
    await waitFor(() => expect(provisionOllama).toHaveBeenCalledWith(expect.any(Function), undefined));
    expect(saveSetupState).toHaveBeenCalledWith({
      LLM_PROVIDER: "ollama",
      LOCAL_LLM_MODEL: "mistral:7b-instruct-v0.3-q4_K_M",
      OLLAMA_BASE_URL: "http://127.0.0.1:11434",
    });
    expect(onApplied).toHaveBeenCalledWith("mistral");
  });

  it("local: Llama pulls the explicit llama3.2 tag", async () => {
    const { deps, provisionOllama } = makeDeps({
      provisionOllama: vi.fn().mockResolvedValue({ ok: true, model: "llama3.2", baseUrl: "http://127.0.0.1:11434" }),
    });
    render(<LlmProviderCard deps={deps} />);
    click(/Llama 3.2/i);
    click(/download & use llama/i);
    await waitFor(() => expect(provisionOllama).toHaveBeenCalledWith(expect.any(Function), "llama3.2"));
  });

  it("reflects the persisted provider (Gemini in use)", async () => {
    const { deps } = makeDeps({ getSetupState: vi.fn().mockResolvedValue({ LLM_PROVIDER: "gemini" }) });
    render(<LlmProviderCard deps={deps} />);
    expect(await screen.findByText(/Current: Google Gemini/i)).toBeTruthy();
  });
});
