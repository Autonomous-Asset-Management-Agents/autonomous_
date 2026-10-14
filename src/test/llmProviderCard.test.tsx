import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
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

  // #2706: REWRITTEN, not deleted. This test used to assert the Mistral tile passes `undefined`,
  // which pinned the defect: main.cjs resolves `model || resolveDefaultModel()`, so on a machine
  // below the low-RAM threshold the tile labelled "Mistral 7B" silently installed phi3.5. A tile
  // must never install a model it does not name — it now passes its explicit vetted tag, and the
  // RAM-aware steering happens through the recommendation badge instead of a silent substitution.
  it("local: Mistral passes its EXPLICIT vetted tag — a tile never installs a model it does not name", async () => {
    const { deps, provisionOllama, saveSetupState } = makeDeps();
    const onApplied = vi.fn();
    render(<LlmProviderCard deps={deps} onApplied={onApplied} />);
    // Mistral is selected by default.
    click(/download & activate mistral/i);
    await waitFor(() =>
      expect(provisionOllama).toHaveBeenCalledWith(expect.any(Function), "mistral:7b-instruct-v0.3-q4_K_M"),
    );
    expect(saveSetupState).toHaveBeenCalledWith({
      LLM_PROVIDER: "ollama",
      LOCAL_LLM_MODEL: "mistral:7b-instruct-v0.3-q4_K_M",
      OLLAMA_BASE_URL: "http://127.0.0.1:11434",
    });
    expect(onApplied).toHaveBeenCalledWith("mistral");
  });

  // B11: the shell restarts a RUNNING engine itself when the pulled model differs from the one the
  // engine booted with (main.cjs ollama:provision → llmRestartNeeded) and reports `restarted:true`.
  // The card must then not offer a second "Restart engine to apply".
  it("local: when the shell already restarted the engine, the card says so instead of offering a restart", async () => {
    const { deps, stopEngine, startEngine } = makeDeps({
      provisionOllama: vi.fn().mockResolvedValue({
        ok: true,
        model: "mistral:7b-instruct-v0.3-q4_K_M",
        baseUrl: "http://127.0.0.1:11434",
        restarted: true,
      }),
    });
    render(<LlmProviderCard deps={deps} />);
    click(/download & activate mistral/i);
    await screen.findByText(/engine restarted with the new model/i);
    expect(screen.queryByRole("button", { name: /restart engine to apply/i })).toBeNull();
    expect(stopEngine).not.toHaveBeenCalled();
    expect(startEngine).not.toHaveBeenCalled();
  });

  it("local: without a shell restart the card still offers 'Restart engine to apply'", async () => {
    const { deps } = makeDeps();
    render(<LlmProviderCard deps={deps} />);
    click(/download & activate mistral/i);
    await screen.findByRole("button", { name: /restart engine to apply/i });
    expect(screen.queryByText(/engine restarted with the new model/i)).toBeNull();
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
    // Mistral pulled -> neutral "Installed" pill; Llama + Phi absent -> "Not installed".
    // #3177 (state→dot): "Installed" is a category, so no green "Installed · active" chip exists.
    expect(await screen.findByText("Installed")).toBeTruthy();
    expect((await screen.findAllByText("Not installed")).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("Installed · active")).toBeNull();
  });

  it("offers the lightweight phi3.5 option for RAM-constrained machines", () => {
    render(<LlmProviderCard deps={makeDeps().deps} />);
    expect(screen.getByText("Phi-3.5 (light)")).toBeTruthy();
  });

  // Operator-reported bug: switching to another model left the previous one showing an active
  // badge. #3177 (state→dot, category→pill): the ACTIVE state now lives ONLY in the title-row
  // StatusDot ("in use"); every pulled model carries the same neutral "Installed" category pill.
  it("badge: pulled models show the neutral 'Installed' pill; the active state lives in the StatusDot", async () => {
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
    expect((await screen.findAllByText("Installed")).length).toBe(2); // mistral (active) + phi
    expect(screen.queryByText("Installed · active")).toBeNull(); // state never rides a pill (#3177)
    // mistral is active (and selected by default) → its tile's dot reads "in use"
    expect(screen.getByTestId("tile-mistral").textContent).toMatch(/in use/);
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

  // ── #2706: RAM-aware recommendation ──────────────────────────────────────────────────────
  //
  // Measured on the affected 16 GB machine: llama-server held ~7 GB with mistral:7b, on top of
  // ~4 GB OS/browser, ~1.3-1.6 GB Electron main (measured) and ~0.6-1.5 GB Python engine — no
  // reserve. The card's Llama blurb said "recommended" for EVERY machine, so the operator of a
  // 16 GB box was steered into the collapse. The threshold rule lives ONCE, in
  // ollama-manager.resolveDefaultModel(); the card only renders the decision it is handed.

  const GiB = 1024 ** 3;

  it("low-RAM machine: the light model carries the recommendation badge, and only it", async () => {
    const { deps } = makeDeps({
      getRecommendedModel: vi.fn().mockResolvedValue({ model: "phi3.5", totalBytes: 16 * GiB, lowRam: true }),
    });
    render(<LlmProviderCard deps={deps} />);
    const badges = await screen.findAllByText(/^Recommended$/);
    expect(badges).toHaveLength(1);
    expect(screen.getByTestId("tile-phi").textContent).toMatch(/Recommended/);
  });

  it("large machine: the full-quality model carries the badge instead", async () => {
    const { deps } = makeDeps({
      getRecommendedModel: vi
        .fn()
        .mockResolvedValue({ model: "mistral:7b-instruct-v0.3-q4_K_M", totalBytes: 32 * GiB, lowRam: false }),
    });
    render(<LlmProviderCard deps={deps} />);
    const badges = await screen.findAllByText(/^Recommended$/);
    expect(badges).toHaveLength(1);
    expect(screen.getByTestId("tile-mistral").textContent).toMatch(/Recommended/);
  });

  it("no bridge for the recommendation: NO badge, card behaves as before (fail-safe)", async () => {
    const { deps } = makeDeps({ getRecommendedModel: undefined });
    render(<LlmProviderCard deps={deps} />);
    expect(await screen.findByText("Mistral 7B")).toBeTruthy(); // card rendered
    expect(screen.queryByText(/^Recommended$/)).toBeNull();
  });

  it("a failing recommendation probe never breaks the card", async () => {
    const { deps } = makeDeps({
      getRecommendedModel: vi.fn().mockRejectedValue(new Error("ipc down")),
    });
    render(<LlmProviderCard deps={deps} />);
    expect(await screen.findByText("Mistral 7B")).toBeTruthy();
    expect(screen.queryByText(/^Recommended$/)).toBeNull();
  });

  it("oversized pick on a low-RAM machine: warns with the measured RAM but never blocks", async () => {
    const { deps } = makeDeps({
      getRecommendedModel: vi.fn().mockResolvedValue({ model: "phi3.5", totalBytes: 16 * GiB, lowRam: true }),
    });
    render(<LlmProviderCard deps={deps} />);
    await screen.findAllByText(/^Recommended$/);
    click(/Llama 3\.1 8B/i);
    const warn = await screen.findByTestId("ram-mismatch-warning");
    expect(warn.textContent).toMatch(/16 GB/i);
    // The operator stays in charge — Art. 14 posture: inform, do not override.
    const apply = screen.getByRole("button", { name: /Download & Activate Llama 3\.1 8B/i });
    expect((apply as HTMLButtonElement).disabled).toBe(false);
  });

  it("picking the recommended model on a low-RAM machine shows no warning", async () => {
    const { deps } = makeDeps({
      getRecommendedModel: vi.fn().mockResolvedValue({ model: "phi3.5", totalBytes: 16 * GiB, lowRam: true }),
    });
    render(<LlmProviderCard deps={deps} />);
    await screen.findAllByText(/^Recommended$/);
    click(/Phi-3\.5/i);
    expect(screen.queryByTestId("ram-mismatch-warning")).toBeNull();
  });

  it("every LOCAL tile passes a defined explicit tag (no tile may resolve elsewhere)", async () => {
    for (const [tile, tag] of [
      [/download & activate mistral/i, "mistral:7b-instruct-v0.3-q4_K_M"],
      [/Download & Activate Llama 3\.1 8B/i, "llama3.1:8b"],
      [/Download & Activate Phi-3\.5/i, "phi3.5"],
    ] as const) {
      const { deps, provisionOllama } = makeDeps();
      const { unmount } = render(<LlmProviderCard deps={deps} />);
      if (!/mistral/i.test(String(tile))) {
        click(tag === "llama3.1:8b" ? /Llama 3\.1 8B/i : /Phi-3\.5/i);
      }
      click(tile);
      await waitFor(() => expect(provisionOllama).toHaveBeenCalledWith(expect.any(Function), tag));
      unmount();
    }
  });

  // ── #2715: switching to a cloud provider whose key is ALREADY stored ─────────────────────
  //
  // The field's own placeholder reads "•••••••••••••• — stored (leave blank to keep)", but the
  // Apply button was `disabled={busy || !key.trim()}`. An operator following that instruction was
  // left with a permanently greyed-out button and no explanation — reported live on 0.3.17 as
  // "I tried to switch to Gemini and the app would not accept it".
  //
  // It blocks more than it looks: applyCloud does TWO things — store the key AND switch the active
  // provider (saveSetupState). The second needs no key at all, yet both hung on the same predicate,
  // so a cloud provider with a stored key could not be selected from anywhere (Settings and the
  // setup wizard render this same card).
  //
  // TEST NOTE: the card's load effect asynchronously resets `selected` to the persisted tile (or
  // "mistral"). Awaiting anything AFTER clicking a tile lets that reset land and silently undoes the
  // selection. So every test below first waits for the load to settle — the Mistral apply button
  // only exists once the effect has run — and only then selects a tile, synchronously.
  const settleLoad = () => screen.findByRole("button", { name: /download & activate mistral/i });

  it("stored key + blank field: Apply is ENABLED and switches the provider WITHOUT rewriting the key", async () => {
    const { deps, saveSecret, saveSetupState } = makeDeps({
      getKeychainStatus: vi.fn().mockResolvedValue({ GEMINI_API_KEY: true }),
    });
    const onApplied = vi.fn();
    render(<LlmProviderCard deps={deps} onApplied={onApplied} />);
    await settleLoad();
    click(/Google Gemini/i);
    const btn = screen.getByRole("button", { name: /save & use google gemini/i }) as HTMLButtonElement;
    await waitFor(() => expect(btn.disabled).toBe(false)); // keychain probe has landed
    fireEvent.click(btn);
    await waitFor(() => expect(saveSetupState).toHaveBeenCalledWith({ LLM_PROVIDER: "gemini" }));
    expect(saveSecret).not.toHaveBeenCalled(); // the stored key is kept, never rewritten
    expect(onApplied).toHaveBeenCalledWith("gemini");
  });

  it("no stored key + blank field: Apply stays disabled AND says why (never a silent dead button)", async () => {
    const { deps, saveSetupState } = makeDeps({ getKeychainStatus: vi.fn().mockResolvedValue({}) });
    render(<LlmProviderCard deps={deps} />);
    await settleLoad();
    click(/Google Gemini/i);
    const btn = screen.getByRole("button", { name: /save & use google gemini/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    // A disabled control must not be the only feedback — CODING_POLICY §5.6 (never fail silently).
    expect(await screen.findByTestId("cloud-key-required")).toBeTruthy();
    expect(saveSetupState).not.toHaveBeenCalled();
  });

  it("stored key + a NEWLY typed key still replaces it (the existing path is unchanged)", async () => {
    const { deps, saveSecret, saveSetupState } = makeDeps({
      getKeychainStatus: vi.fn().mockResolvedValue({ GEMINI_API_KEY: true }),
    });
    render(<LlmProviderCard deps={deps} />);
    await settleLoad();
    click(/Google Gemini/i);
    fireEvent.change(screen.getByLabelText("gemini-key"), { target: { value: "AIza-new" } });
    click(/save & use google gemini/i);
    await waitFor(() => expect(saveSecret).toHaveBeenCalledWith("GEMINI_API_KEY", "AIza-new"));
    expect(saveSetupState).toHaveBeenCalledWith({ LLM_PROVIDER: "gemini" });
  });

  it("stored key + blank field: no keychain failure can be reported, because none is attempted", async () => {
    // Guards the branch itself: with nothing to write there is no saveSecret call and therefore no
    // "saving to the keychain failed" path — the provider switch must not inherit a failure mode
    // from an operation it skipped.
    const { deps, saveSetupState } = makeDeps({
      getKeychainStatus: vi.fn().mockResolvedValue({ GEMINI_API_KEY: true }),
      saveSecret: vi.fn().mockResolvedValue({ ok: false, error: "keychain locked" }),
    });
    render(<LlmProviderCard deps={deps} />);
    await settleLoad();
    click(/Google Gemini/i);
    const btn = screen.getByRole("button", { name: /save & use google gemini/i }) as HTMLButtonElement;
    await waitFor(() => expect(btn.disabled).toBe(false));
    fireEvent.click(btn);
    await waitFor(() => expect(saveSetupState).toHaveBeenCalledWith({ LLM_PROVIDER: "gemini" }));
    expect(screen.queryByText(/saving to the keychain failed/i)).toBeNull();
  });

  // ── Design language: no gold/amber, sanctioned pill primitives only ──────────────────────
  //
  // Operator feedback 2026-08-10: "du verwendest shadow colours, außerdem gelb. das machen wir
  // nicht." The rule was already recorded in HitlPolicyCard ("no gold/amber; red for the
  // high-autonomy state") and I violated it anyway with a hand-rolled `bg-[#f0b429]/[0.06]`
  // warning box plus `#ffcc66` text. A comment did not prevent that, so these tests pin it.
  //
  // They assert on class attributes rather than computed styles on purpose: jsdom does not apply
  // the console.css sheet, so the class NAME is the only observable contract — and it is exactly
  // the contract that was broken (hand-rolled hex tints instead of `pill` / `pill-bull`).

  const AMBER = /#f0b429|#ffcc66|#fbbf24|#eab308|amber|yellow|pill-warn/i;

  it("renders no gold/amber anywhere — including the not-installed and RAM-mismatch notices", async () => {
    // Drive BOTH notice paths into view at once: a low-RAM machine with an oversized pick (RAM
    // mismatch) and a configured-but-unpulled model (not-installed) — the two boxes that were amber.
    const { deps } = makeDeps({
      getSetupState: vi.fn().mockResolvedValue({
        LLM_PROVIDER: "ollama",
        LOCAL_LLM_MODEL: "mistral:7b-instruct-v0.3-q4_K_M",
      }),
      listOllamaModels: vi.fn().mockResolvedValue({ reachable: true, models: [] }),
      getRecommendedModel: vi
        .fn()
        .mockResolvedValue({ model: "phi3.5", totalBytes: 16 * 1024 ** 3, lowRam: true }),
    });
    const { container } = render(<LlmProviderCard deps={deps} />);
    await screen.findByText(/isn.?t installed yet/i); // the not-installed notice is up
    click(/Llama 3\.1 8B/i);
    await screen.findByTestId("ram-mismatch-warning"); // the mismatch notice is up
    for (const el of Array.from(container.querySelectorAll("*"))) {
      const cls = el.getAttribute("class") ?? "";
      expect(AMBER.test(cls), `amber token in class: ${cls}`).toBe(false);
      const style = el.getAttribute("style") ?? "";
      expect(AMBER.test(style), `amber token in style: ${style}`).toBe(false);
    }
  });

  it("state badges use the sanctioned pill primitives, never hand-rolled tints", async () => {
    const { deps } = makeDeps({
      listOllamaModels: vi.fn().mockResolvedValue({ reachable: true, models: ["phi3.5"] }),
      getRecommendedModel: vi
        .fn()
        .mockResolvedValue({ model: "phi3.5", totalBytes: 16 * 1024 ** 3, lowRam: true }),
    });
    render(<LlmProviderCard deps={deps} />);
    const rec = await screen.findByText(/^Recommended$/);
    expect(rec.className.split(/\s+/)).toContain("pill"); // neutral — advice, not a status
    const installed = await screen.findByText("Installed");
    expect(installed.className.split(/\s+/)).toContain("pill");
    // #3177: a green-tinted chip must not carry state — "Installed" is a neutral category.
    expect(installed.className).not.toMatch(/pill-bull/);
    const notInstalled = (await screen.findAllByText("Not installed"))[0];
    expect(notInstalled.className.split(/\s+/)).toContain("pill");
    // "Not installed" is a state, not a fault — it must NOT borrow the error colour either.
    expect(notInstalled.className).not.toMatch(/pill-bear/);
  });

  it("the machine's RAM is stated ONCE in the section heading, not repeated per tile", async () => {
    // Why: "Recommended for your machine" wrapped onto two lines inside a third-width tile and
    // pushed the titles out of alignment. The qualifier belongs to the section; the pill stays short.
    const { deps } = makeDeps({
      getRecommendedModel: vi
        .fn()
        .mockResolvedValue({ model: "phi3.5", totalBytes: 16 * 1024 ** 3, lowRam: true }),
    });
    render(<LlmProviderCard deps={deps} />);
    expect(await screen.findByText(/recommendation for 16 GB RAM/i)).toBeTruthy();
    const pill = await screen.findByText(/^Recommended$/);
    expect(pill.textContent).toBe("Recommended"); // short, no sentence in the tile
  });

  it("no RAM reading: neither the heading qualifier nor any badge appears", async () => {
    const { deps } = makeDeps({ getRecommendedModel: undefined });
    render(<LlmProviderCard deps={deps} />);
    expect(await screen.findByText("Mistral 7B")).toBeTruthy();
    expect(screen.queryByText(/recommendation for/i)).toBeNull();
    expect(screen.queryByText(/^Recommended$/)).toBeNull();
  });

  // ── B10 (0.5.1 release check, mac): Ollama missing → the failure surface ─────────────────
  //
  // Seen on a fresh Mac without Ollama: the shell's own message ("Ollama isn't installed. Download
  // it from ollama.com/download/mac, install it, then retry.") was rendered, and directly under it
  // the card's generic fallback ("Install it from ollama.com/download, then retry.") — the same
  // instruction twice, the second one pointing at the generic page. And the progress bar stayed at
  // "Downloading… 0% (first run only)" although nothing was downloading any more. The shell's text
  // already carries the platform-correct page (ollama-manager.cjs: /download/mac on darwin), so the
  // card linkifies THAT and shows the generic line only when the shell gave no download hint.
  // The link is a plain target="_blank" anchor: main.cjs setWindowOpenHandler routes every external
  // http(s) window.open to shell.openExternal — there is no openExternal bridge call, and none is
  // needed (UpdateCard uses the same idiom).
  const MAC_ERR = "Ollama isn't installed. Download it from ollama.com/download/mac, install it, then retry.";

  it("Ollama missing on mac: the shell text is shown ONCE, linkified to /download/mac, and the stalled bar is gone", async () => {
    const { deps } = makeDeps({
      provisionOllama: vi.fn().mockResolvedValue({
        ok: false,
        needsManual: true,
        downloadUrl: "https://ollama.com/download/mac",
        error: MAC_ERR,
      }),
    });
    render(<LlmProviderCard deps={deps} />);
    await settleLoad();
    click(/download & activate mistral/i);
    await screen.findByText(/Ollama isn.?t installed/i);
    // no second, generic copy of the same instruction under the shell's own text
    expect(screen.queryByText(/Install it from/i)).toBeNull();
    expect(screen.getAllByText(/then retry/i)).toHaveLength(1);
    // the page named by the shell is a real link, on the platform-correct URL
    const link = screen.getByRole("link", { name: /ollama\.com\/download\/mac/i }) as HTMLAnchorElement;
    expect(link.href).toBe("https://ollama.com/download/mac");
    expect(link.target).toBe("_blank");
    // the progress bar must not linger at 0 % once the flow has failed
    expect(screen.queryByText(/Downloading/i)).toBeNull();
    // and the operator can retry
    expect((screen.getByRole("button", { name: /download & activate mistral/i }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("Ollama missing, shell text names NO download page: the generic hint with a link is shown, once", async () => {
    const { deps } = makeDeps({
      provisionOllama: vi.fn().mockResolvedValue({ ok: false, needsManual: true, error: "Ollama isn't installed." }),
    });
    render(<LlmProviderCard deps={deps} />);
    await settleLoad();
    click(/download & activate mistral/i);
    await screen.findByText(/Ollama isn.?t installed\./i);
    expect(screen.getAllByText(/then retry/i)).toHaveLength(1); // the generic line, nothing else
    const link = screen.getByRole("link", { name: /ollama\.com\/download/i }) as HTMLAnchorElement;
    expect(link.href).toBe("https://ollama.com/download");
    expect(screen.queryByText(/Downloading/i)).toBeNull();
  });

  it("Ollama missing, shell gives no text at all: the card's own fallback plus the generic link", async () => {
    const { deps } = makeDeps({
      provisionOllama: vi.fn().mockResolvedValue({ ok: false, needsManual: true }),
    });
    render(<LlmProviderCard deps={deps} />);
    await settleLoad();
    click(/download & activate mistral/i);
    await screen.findByText(/Couldn.?t auto-install the local AI runtime/i);
    expect(screen.getAllByText(/then retry/i)).toHaveLength(2); // fallback sentence + generic link line
    expect(screen.getByRole("link", { name: /ollama\.com\/download/i })).toBeTruthy();
    expect(screen.queryByText(/Downloading/i)).toBeNull();
  });

  it("Windows shell text (generic /download page) is linkified to that page, no duplicate line", async () => {
    const { deps } = makeDeps({
      provisionOllama: vi.fn().mockResolvedValue({
        ok: false,
        needsManual: true,
        downloadUrl: "https://ollama.com/download/windows",
        error: "Ollama isn't installed and the automatic install failed. Download it from ollama.com/download, then retry.",
      }),
    });
    render(<LlmProviderCard deps={deps} />);
    await settleLoad();
    click(/download & activate mistral/i);
    await screen.findByText(/automatic install failed/i);
    expect(screen.queryByText(/Install it from/i)).toBeNull();
    expect(screen.getAllByText(/then retry/i)).toHaveLength(1);
    const link = screen.getByRole("link", { name: /ollama\.com\/download/i }) as HTMLAnchorElement;
    expect(link.href).toBe("https://ollama.com/download/windows"); // the shell's platform URL wins
  });

  it("a plain provisioning failure (not needsManual) also clears the progress bar", async () => {
    const { deps } = makeDeps({
      provisionOllama: vi.fn().mockResolvedValue({ ok: false, error: "pull failed: disk full" }),
    });
    render(<LlmProviderCard deps={deps} />);
    await settleLoad();
    click(/download & activate mistral/i);
    await screen.findByText(/pull failed: disk full/i);
    expect(screen.queryByText(/Downloading/i)).toBeNull();
    expect(screen.queryByRole("link", { name: /ollama\.com\/download/i })).toBeNull(); // no install hint here
  });

  it("a successful run still shows the bar and the ready line (the fix must not hide progress on success)", async () => {
    const { deps } = makeDeps();
    render(<LlmProviderCard deps={deps} />);
    await settleLoad();
    click(/download & activate mistral/i);
    expect(await screen.findByText(/Ready — running locally\./i)).toBeTruthy();
  });

  // ── darwin auto-install of Ollama itself (0.5.2): the shell streams a second phase ──────────
  //
  // On a fresh Mac the shell now downloads + unpacks the official Ollama package before it pulls
  // the model (ollama-manager.cjs installDarwin). It streams that as
  // { stage: "install-ollama", status, percent } over the SAME progress channel. The card must
  // label that phase "Installing Ollama… N%" and switch back to "Downloading… N%" once the model
  // pull starts (no stage). Windows never sends the stage, so its label is unchanged.
  type Progress = Parameters<Parameters<LlmDeps["provisionOllama"]>[0]>[0];

  /** A provisionOllama mock whose resolution the test controls; exposes the progress callback. */
  function controlledProvision() {
    let onProgress: ((p: Progress) => void) | null = null;
    let finish: (r: { ok: boolean; model?: string; baseUrl?: string; error?: string; needsManual?: boolean }) => void = () => {};
    const provisionOllama = vi.fn((cb: (p: Progress) => void) => {
      onProgress = cb;
      return new Promise<{ ok: boolean }>((resolve) => {
        finish = resolve;
      });
    });
    return {
      provisionOllama: provisionOllama as unknown as LlmDeps["provisionOllama"],
      emit: (p: Progress) => onProgress?.(p),
      finish: (r: Parameters<typeof finish>[0]) => finish(r),
    };
  }

  it("Ollama auto-install phase (stage install-ollama) renders 'Installing Ollama… N%' before the model download", async () => {
    const ctl = controlledProvision();
    const { deps } = makeDeps({ provisionOllama: ctl.provisionOllama });
    render(<LlmProviderCard deps={deps} />);
    await settleLoad();
    click(/download & activate mistral/i);
    // the shell's first line is a status without a stage: the old label, as on Windows
    expect(await screen.findByText(/Downloading… 0% \(first run only\)/i)).toBeTruthy();
    await act(async () => ctl.emit({ stage: "install-ollama", status: "Downloading Ollama…", percent: 37 }));
    expect(screen.getByText(/Installing Ollama… 37%/i)).toBeTruthy();
    expect(screen.queryByText(/Downloading… 37%/i)).toBeNull();
    await act(async () => ctl.emit({ stage: "install-ollama", status: "Unpacking Ollama…", percent: 100 }));
    expect(screen.getByText(/Installing Ollama… 100%/i)).toBeTruthy();
    // the model pull that follows has no stage: back to the model label, its own percent
    await act(async () => ctl.emit({ status: "downloading", percent: 12 }));
    expect(screen.getByText(/Downloading… 12% \(first run only\)/i)).toBeTruthy();
    expect(screen.queryByText(/Installing Ollama/i)).toBeNull();
    await act(async () => ctl.finish({ ok: true, model: "mistral:7b-instruct-v0.3-q4_K_M", baseUrl: "http://127.0.0.1:11434" }));
    expect(await screen.findByText(/Ready — running locally\./i)).toBeTruthy();
    expect(screen.queryByText(/Installing Ollama/i)).toBeNull();
  });

  it("Ollama auto-install failure after progress: the install label is gone, the manual hint is linkified", async () => {
    const ctl = controlledProvision();
    const { deps } = makeDeps({ provisionOllama: ctl.provisionOllama });
    render(<LlmProviderCard deps={deps} />);
    await settleLoad();
    click(/download & activate mistral/i);
    await screen.findByText(/Downloading… 0%/i);
    await act(async () => ctl.emit({ stage: "install-ollama", status: "Downloading Ollama…", percent: 61 }));
    expect(screen.getByText(/Installing Ollama… 61%/i)).toBeTruthy();
    await act(async () =>
      ctl.finish({
        ok: false,
        needsManual: true,
        error:
          "Automatic Ollama install failed: download stalled. Download it from ollama.com/download/mac, install it, then retry.",
      }),
    );
    await screen.findByText(/Automatic Ollama install failed/i);
    expect(screen.queryByText(/Installing Ollama/i)).toBeNull();
    expect(screen.queryByText(/Downloading/i)).toBeNull();
    const link = screen.getByRole("link", { name: /ollama\.com\/download\/mac/i }) as HTMLAnchorElement;
    expect(link.href).toBe("https://ollama.com/download/mac");
    expect((screen.getByRole("button", { name: /download & activate mistral/i }) as HTMLButtonElement).disabled).toBe(false);
  });
});
