/**
 * STW-4c (#4873) — strategy editor (UX-Konzept #4628, Ablauf 1, part 2: create, duplicate,
 * edit). One `it` per check step of the concept (A1-P2, A1-P3, A1-P4, A1-P7) plus the
 * plan's edge cases. The editor saves the FULL value set of the advanced field grid through
 * the sealed `POST /api/strategies`; the fixed limits are shown read-only and never sent.
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { HttpError } from "@/lib/api";
import { ADVANCED_KEYS } from "@/console/desktop/advancedTradingFields";
import { DEFAULT_SETTINGS, payload } from "./advancedTradingFixtures";

vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));

const getTradingSettings = vi.fn();
const saveStrategy = vi.fn();
const listStrategyVersions = vi.fn();
const fetchLlmTrichter = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getTradingSettings: (...a: unknown[]) => getTradingSettings(...a),
    saveStrategy: (...a: unknown[]) => saveStrategy(...a),
    listStrategyVersions: (...a: unknown[]) => listStrategyVersions(...a),
    fetchLlmTrichter: () => fetchLlmTrichter(),
  };
});

import { StrategyEditor } from "@/console/desktop/strategyWorkshop/StrategyEditor";

const FIXED_KEYS = [
  "HARD_STOP_LOSS_PCT", "HITL_ENABLED", "RISK_FORBID_LEVERAGE",
  "TRAILING_STOP_PCT", "GATEKEEPER_PDT_CROSSDAY_EXEMPTION",
];

const CURRENT = payload({ STOP_LOSS_PCT: "5.5" });
const STRATEGIES = [
  { name: "A", werte: { STOP_LOSS_PCT: "6.0", SIGNAL_BUY_THRESHOLD: "0.70", LLM_PROMPT_SUFFIX: "x" } },
  { name: "B", werte: { STOP_LOSS_PCT: "4.0" } },
];

const saved = (name: string, werte: Record<string, string>, aktion = "create") => ({
  success: true, aktion, name, werte, detail: "strategy recorded on the WORM chain",
});

const VERSIONS = {
  name: "A",
  versionen: [
    { hash: "ffff", hash_kuerzel: "ffff1111", zeitpunkt: "2026-10-28T10:05:00Z", aktion: "update", felder: ["STOP_LOSS_PCT"] },
    { hash: "aaaa", hash_kuerzel: "aaaa2222", zeitpunkt: "2026-10-27T09:00:00Z", aktion: "create", felder: [] },
  ],
};

type Props = Parameters<typeof StrategyEditor>[0];

async function renderEditor(props: Partial<Props> = {}) {
  const onSaved = vi.fn(), onCancel = vi.fn();
  render(<StrategyEditor mode="new" strategies={STRATEGIES} onSaved={onSaved} onCancel={onCancel} {...props} />);
  await screen.findByLabelText("Strategy name");
  await screen.findByLabelText("Buy threshold");
  return { onSaved, onCancel };
}

const nameField = () => screen.getByLabelText("Strategy name") as HTMLInputElement;
const saveButton = () => screen.getByRole("button", { name: "Save strategy" }) as HTMLButtonElement;
const sliderValue = (label: string) =>
  (screen.getAllByLabelText(label).find((e) => e.tagName === "INPUT") as HTMLInputElement).value;

describe("StrategyEditor (STW-4c #4873)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getTradingSettings.mockResolvedValue(CURRENT);
    saveStrategy.mockImplementation((name: string, werte: Record<string, string>) =>
      Promise.resolve(saved(name, werte)),
    );
    listStrategyVersions.mockResolvedValue(VERSIONS);
    fetchLlmTrichter.mockResolvedValue(null);
  });

  it("A1-P2: name field empty, no placeholder, save disabled until a name is typed", async () => {
    await renderEditor();
    expect(nameField().value).toBe("");
    expect(nameField().getAttribute("placeholder")).toBeNull();
    expect(saveButton().disabled).toBe(true);
    fireEvent.change(nameField(), { target: { value: "   " } });
    expect(saveButton().disabled).toBe(true);
    fireEvent.change(nameField(), { target: { value: "Wider stops" } });
    expect(saveButton().disabled).toBe(false);
  });

  it("A1-P3: from current settings posts the current ADVANCED_KEYS values", async () => {
    const { onSaved } = await renderEditor();
    expect((screen.getByLabelText("Source") as HTMLSelectElement).value).toBe("");
    expect(screen.getByRole("option", { name: "From current settings" })).toBeTruthy();
    fireEvent.change(nameField(), { target: { value: "Wider stops" } });
    fireEvent.click(saveButton());

    await waitFor(() => expect(saveStrategy).toHaveBeenCalledTimes(1));
    const [name, werte, ack, nonce] = saveStrategy.mock.calls[0];
    expect(name).toBe("Wider stops");
    const expected = Object.fromEntries(ADVANCED_KEYS.map((k) => [k, CURRENT.settings[k]]));
    expect(werte).toEqual(expected);
    expect(werte.STOP_LOSS_PCT).toBe("5.5");
    expect(ack).toMatch(/saving this strategy/i);
    expect(typeof nonce).toBe("string");
    expect((nonce as string).length).toBeGreaterThan(0);
    expect(await screen.findByText("Strategy saved. The change is recorded in the audit chain.")).toBeTruthy();
    expect(onSaved).toHaveBeenCalledWith(saved("Wider stops", expected));
  });

  it("A1-P4: duplicate takes the source values, not its name", async () => {
    await renderEditor({ source: "A" });
    expect((screen.getByLabelText("Source") as HTMLSelectElement).value).toBe("A");
    expect(nameField().value).toBe("");
    expect(sliderValue("Broker stop distance")).toBe("6");
    expect(sliderValue("Buy threshold")).toBe("0.7");

    fireEvent.change(nameField(), { target: { value: "A copy" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveStrategy).toHaveBeenCalledTimes(1));
    const [name, werte] = saveStrategy.mock.calls[0];
    expect(name).toBe("A copy");
    expect(werte.STOP_LOSS_PCT).toBe("6.0");
    expect(werte.SIGNAL_BUY_THRESHOLD).toBe("0.70");
    expect(werte.SIGNAL_SELL_THRESHOLD).toBe(DEFAULT_SETTINGS.SIGNAL_SELL_THRESHOLD);
    expect(werte.LLM_PROMPT_SUFFIX).toBe("x");
  });

  it("#4987: the shared table locks in the editor too, following the edited method", async () => {
    await renderEditor();
    const coverage = () => screen.getByLabelText("Coverage sizing strength") as HTMLInputElement;
    expect((screen.getByLabelText("Minimum position size") as HTMLInputElement).disabled).toBe(true);
    expect(coverage().disabled).toBe(false);
    fireEvent.change(screen.getByLabelText("Sizing method"), { target: { value: "a" } });
    expect(coverage().disabled).toBe(true);
    expect(document.querySelector('[data-key="COVERAGE_SIZING_STRENGTH"] [data-copy="inert-hint"]')).not.toBeNull();
  });

  it("#4987: a locked minimum above the slot does not block saving", async () => {
    getTradingSettings.mockResolvedValue({
      ...payload({ CLEAN_WEIGHT_SIZING: "b", MIN_POSITION_PERCENT: "0.08" }),
      effective_positions: 20,
    });
    await renderEditor();
    fireEvent.change(nameField(), { target: { value: "Plain" } });
    await new Promise((r) => setTimeout(r));
    expect(saveButton().disabled).toBe(false);
  });

  it("#5007: the slot rule uses the engine's count, not the former fixed 10", async () => {
    getTradingSettings.mockResolvedValue({
      ...payload({ CLEAN_WEIGHT_SIZING: "off", MIN_POSITION_PERCENT: "0.08" }),
      effective_positions: 20,
    });
    await renderEditor();
    expect(screen.getByRole("alert").textContent).toContain("1/20");
  });

  it("#5007: without an engine count the slot rules drop out, with a visible note", async () => {
    getTradingSettings.mockResolvedValue({
      ...payload({ CLEAN_WEIGHT_SIZING: "off", ROUND_TABLE_TOP_K_EVAL: "5" }),
      effective_positions: undefined,
    });
    await renderEditor();
    expect(screen.getByTestId("slot-check-skipped")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("#4987: a saved strategy carries the chosen method", async () => {
    await renderEditor();
    fireEvent.change(screen.getByLabelText("Sizing method"), { target: { value: "a" } });
    fireEvent.change(nameField(), { target: { value: "Plain" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveStrategy).toHaveBeenCalledTimes(1));
    expect(saveStrategy.mock.calls[0][1].CLEAN_WEIGHT_SIZING).toBe("a");
  });

  it("A1-P4: switching the source in the editor takes that strategy's values", async () => {
    await renderEditor();
    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "B" } });
    expect(sliderValue("Broker stop distance")).toBe("4");
    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "" } });
    expect(sliderValue("Broker stop distance")).toBe("5.5");
  });

  it('A1-P7: fixed limits are shown with "Fixed for all strategies" and have no input', async () => {
    await renderEditor();
    const block = screen.getByTestId("fixed-limits");
    expect(within(block).getByText("Fixed for all strategies")).toBeTruthy();
    expect(within(block).getByText("Hard stop loss")).toBeTruthy();
    expect(within(block).getByText("-8.0")).toBeTruthy();
    expect(within(block).getByText("Human approval of orders")).toBeTruthy();
    expect(within(block).getByText("No leverage")).toBeTruthy();
    expect(within(block).getByText("Pattern day trading rule")).toBeTruthy();
    expect(block.querySelectorAll("input, select, textarea, button")).toHaveLength(0);
  });

  it("fixed limit keys are never sent", async () => {
    await renderEditor({ source: "A" });
    fireEvent.change(nameField(), { target: { value: "C" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveStrategy).toHaveBeenCalledTimes(1));
    const werte = saveStrategy.mock.calls[0][1] as Record<string, string>;
    for (const k of FIXED_KEYS) expect(Object.keys(werte)).not.toContain(k);
  });

  it("422 shows the detail and calls no onSaved", async () => {
    saveStrategy.mockRejectedValue(
      new HttpError(422, "Unprocessable Entity", "/api/strategies", {
        detail: "STOP_LOSS_PCT: value outside the allowed range",
        code: "value_out_of_range",
      }),
    );
    const { onSaved } = await renderEditor();
    fireEvent.change(nameField(), { target: { value: "X" } });
    fireEvent.click(saveButton());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("STOP_LOSS_PCT: value outside the allowed range");
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.queryByText("Strategy saved. The change is recorded in the audit chain.")).toBeNull();
    expect(nameField().value).toBe("X");
    expect(sliderValue("Broker stop distance")).toBe("5.5");
  });

  it("a client-side cross-check violation blocks saving", async () => {
    await renderEditor();
    fireEvent.change(nameField(), { target: { value: "X" } });
    const max = screen.getAllByLabelText("Maximum position size").find((e) => e.tagName === "INPUT")!;
    fireEvent.change(max, { target: { value: "0.05" } });
    expect(screen.getByRole("alert").textContent).toMatch(/minimum position size must stay below the maximum position size/i);
    expect(saveButton().disabled).toBe(true);
  });

  it("cancel calls no API", async () => {
    const { onCancel } = await renderEditor();
    fireEvent.change(nameField(), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(saveStrategy).not.toHaveBeenCalled();
    expect(listStrategyVersions).not.toHaveBeenCalled();
  });

  it("#4999: the editor shares the row: values in their unit, entries stored as the engine's value", async () => {
    saveStrategy.mockImplementation((name: string, werte: Record<string, string>) =>
      Promise.resolve(saved(name, werte, "create")),
    );
    await renderEditor();
    const shown = (key: string) => document.querySelector(`[data-key="${key}"] [data-copy="value"]`)?.textContent;
    expect(shown("MAX_POSITION_PERCENT")).toBe("25%");
    expect(shown("COMPLIANCE_MAX_ORDER_VALUE")).toBe("$10,000");
    const entry = screen.getByLabelText("Maximum position size value");
    fireEvent.change(entry, { target: { value: "20" } });
    fireEvent.blur(entry);
    expect(shown("MAX_POSITION_PERCENT")).toBe("20%");
    fireEvent.change(nameField(), { target: { value: "Units" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveStrategy).toHaveBeenCalledTimes(1));
    expect(saveStrategy.mock.calls[0][1].MAX_POSITION_PERCENT).toBe("0.2");
  });

  it("#5001: the editor shares mark, default and reset; the reset value is what gets saved", async () => {
    await renderEditor({ source: "A" });
    const row = document.querySelector('[data-key="STOP_LOSS_PCT"]') as HTMLElement;
    expect(within(row).getByText("Custom")).toBeTruthy();
    expect(row.querySelector('[data-copy="default"]')?.textContent).toBe("Default: -7%");
    fireEvent.click(within(row).getByRole("button", { name: "Reset Broker stop distance to default" }));
    expect(sliderValue("Broker stop distance")).toBe("7");
    expect(within(row).queryByText("Custom")).toBeNull();
    fireEvent.change(nameField(), { target: { value: "Back" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveStrategy).toHaveBeenCalledTimes(1));
    expect(saveStrategy.mock.calls[0][1].STOP_LOSS_PCT).toBe(DEFAULT_SETTINGS.STOP_LOSS_PCT);
  });

  it("#5001: an empty default reads as empty, not as a blank", async () => {
    const strategy ={ name: "A", werte: { LLM_RANKING_QUELLE: "llm", LLM_RANKING_PROMPT: "mine" } };
    await renderEditor({ mode: "edit", initial: strategy, strategies: [strategy] });
    const row = document.querySelector('[data-key="LLM_RANKING_PROMPT"]') as HTMLElement;
    expect(row.querySelector('[data-copy="default"]')?.textContent).toBe("Default: empty");
  });

  it("#5001: a cross-check message stands at its field, both messages of the minimum stay", async () => {
    getTradingSettings.mockResolvedValue({
      ...payload({ CLEAN_WEIGHT_SIZING: "off", MIN_POSITION_PERCENT: "0.08", MAX_POSITION_PERCENT: "0.06" }),
      effective_positions: 20,
    });
    await renderEditor();
    fireEvent.change(nameField(), { target: { value: "X" } });
    const row = document.querySelector('[data-key="MIN_POSITION_PERCENT"]') as HTMLElement;
    const shown = within(row).getAllByRole("alert").map((a) => a.textContent).join(" ");
    expect(screen.getAllByRole("alert")).toHaveLength(2);
    expect(shown).toContain("1/20");
    expect(shown).toMatch(/below the maximum/i);
    expect(saveButton().disabled).toBe(true);
  });

  it("edit opens with stored values and shows the version list after save", async () => {
    saveStrategy.mockImplementation((name: string, werte: Record<string, string>) =>
      Promise.resolve(saved(name, werte, "update")),
    );
    const { onSaved } = await renderEditor({ mode: "edit", initial: STRATEGIES[0] });
    expect(nameField().value).toBe("A");
    expect(nameField().readOnly).toBe(true);
    expect(screen.queryByLabelText("Source")).toBeNull();
    expect(sliderValue("Broker stop distance")).toBe("6");

    const stop = screen.getAllByLabelText("Broker stop distance").find((e) => e.tagName === "INPUT")!;
    fireEvent.change(stop, { target: { value: "6.5" } });
    fireEvent.click(saveButton());

    await waitFor(() => expect(saveStrategy).toHaveBeenCalledTimes(1));
    const [name, werte] = saveStrategy.mock.calls[0];
    expect(name).toBe("A");
    expect(werte.STOP_LOSS_PCT).toBe("6.5");
    expect(werte.LLM_PROMPT_SUFFIX).toBe("x");
    expect(onSaved).toHaveBeenCalledTimes(1);

    const list = await screen.findByTestId("strategy-versions");
    expect(listStrategyVersions).toHaveBeenCalledWith("A");
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("ffff1111");
    expect(items[0].textContent).toContain("2026-10-28");
    expect(items[1].textContent).toContain("aaaa2222");
  });

  it("children render below the field grid", async () => {
    render(
      <StrategyEditor mode="new" strategies={STRATEGIES} onSaved={vi.fn()} onCancel={vi.fn()}>
        <div data-testid="seam">Candidate pre-selection</div>
      </StrategyEditor>,
    );
    const seam = await screen.findByTestId("seam");
    const lastGridRow = (await screen.findAllByText("Round-table candidates"))[0];
    expect(lastGridRow.compareDocumentPosition(seam) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(seam.compareDocumentPosition(saveButton()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  // ── #4660: candidate source, cadence, prompt and own list ──────────────────

  const sourceSelect = () => screen.getByLabelText("Candidates from") as HTMLSelectElement;
  const promptField = () => screen.getByLabelText("Prompt") as HTMLTextAreaElement;

  it("#4660: the prompt field is empty for a new strategy even when the engine runs a prompt", async () => {
    getTradingSettings.mockResolvedValue(
      payload({ LLM_RANKING_QUELLE: "llm", LLM_RANKING_PROMPT: "engine prompt", LLM_RANKING_RHYTHMUS: "woechentlich" }),
    );
    await renderEditor();
    expect(sourceSelect().value).toBe("lstm");
    expect(screen.queryByLabelText("Prompt")).toBeNull();
    fireEvent.change(sourceSelect(), { target: { value: "llm" } });
    expect(promptField().value).toBe("");
    expect(promptField().getAttribute("placeholder")).toBeNull();
    expect(promptField().maxLength).toBe(2000);
    expect((screen.getByLabelText("Refresh cadence") as HTMLSelectElement).value).toBe("je_refresh");
  });

  it("#4660: saving sends source, cadence and prompt in werte", async () => {
    await renderEditor();
    expect(screen.getByRole("option", { name: "Your prompt" })).toBeTruthy();
    fireEvent.change(sourceSelect(), { target: { value: "llm" } });
    fireEvent.change(screen.getByLabelText("Refresh cadence"), { target: { value: "taeglich_vor_oeffnung" } });
    fireEvent.change(promptField(), { target: { value: "Large caps with rising margins" } });
    fireEvent.change(nameField(), { target: { value: "P" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveStrategy).toHaveBeenCalledTimes(1));
    const werte = saveStrategy.mock.calls[0][1] as Record<string, string>;
    expect(werte.LLM_RANKING_QUELLE).toBe("llm");
    expect(werte.LLM_RANKING_RHYTHMUS).toBe("taeglich_vor_oeffnung");
    expect(werte.LLM_RANKING_PROMPT).toBe("Large caps with rising margins");
  });

  it("#4660: own list hides prompt and cadence, shows Symbols, and keeps the hidden prompt", async () => {
    await renderEditor();
    fireEvent.change(sourceSelect(), { target: { value: "llm" } });
    fireEvent.change(promptField(), { target: { value: "kept" } });
    fireEvent.change(sourceSelect(), { target: { value: "liste" } });
    expect(screen.queryByLabelText("Prompt")).toBeNull();
    expect(screen.queryByLabelText("Refresh cadence")).toBeNull();
    const symbols = screen.getByLabelText("Symbols") as HTMLTextAreaElement;
    fireEvent.change(symbols, { target: { value: "AAPL, MSFT, XYZ1" } });
    fireEvent.change(nameField(), { target: { value: "L" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveStrategy).toHaveBeenCalledTimes(1));
    const werte = saveStrategy.mock.calls[0][1] as Record<string, string>;
    expect(werte.LLM_RANKING_LISTE).toBe("AAPL, MSFT, XYZ1");
    expect(werte.LLM_RANKING_PROMPT).toBe("kept");
  });

  it("#4660: lists every discarded symbol with grundText after the save", async () => {
    saveStrategy.mockImplementation((name: string, werte: Record<string, string>) =>
      Promise.resolve({ ...saved(name, werte), liste_verworfen: { XYZ1: "nicht_im_universum" } }),
    );
    await renderEditor();
    fireEvent.change(sourceSelect(), { target: { value: "liste" } });
    fireEvent.change(screen.getByLabelText("Symbols"), { target: { value: "AAPL, MSFT, XYZ1" } });
    fireEvent.change(nameField(), { target: { value: "L" } });
    fireEvent.click(saveButton());
    const box = await screen.findByTestId("liste-verworfen");
    expect(box.getAttribute("role")).toBe("status");
    expect(box.textContent).toContain("XYZ1");
    expect(box.textContent).toContain("nicht im Universum");
    expect(await screen.findByTestId("strategy-versions")).toBeTruthy();
  });

  it("#4660: ranking_prompt_missing shows the catalogue text with role=alert", async () => {
    saveStrategy.mockRejectedValue(
      new HttpError(422, "Unprocessable Entity", "/api/strategies", {
        detail: "LLM_RANKING_QUELLE='llm' requires an LLM_RANKING_PROMPT",
        code: "ranking_prompt_missing",
      }),
    );
    await renderEditor();
    fireEvent.change(sourceSelect(), { target: { value: "llm" } });
    fireEvent.change(nameField(), { target: { value: "X" } });
    fireEvent.click(saveButton());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("The selected candidate source needs a prompt.");
  });

  it("#4660: a prompt change reloads the version list and shows the new hash", async () => {
    const prompted = { name: "A", werte: { LLM_RANKING_QUELLE: "llm", LLM_RANKING_PROMPT: "old" } };
    saveStrategy.mockImplementation((name: string, werte: Record<string, string>) =>
      Promise.resolve(saved(name, werte, "update")),
    );
    await renderEditor({ mode: "edit", initial: prompted, strategies: [prompted] });
    expect(promptField().value).toBe("old");
    fireEvent.change(promptField(), { target: { value: "new" } });
    fireEvent.click(saveButton());
    await screen.findByTestId("strategy-versions");
    expect(saveStrategy.mock.calls[0][1].LLM_RANKING_PROMPT).toBe("new");

    listStrategyVersions.mockResolvedValue({
      name: "A",
      versionen: [
        { hash: "bbbb", hash_kuerzel: "bbbb3333", zeitpunkt: "2026-10-29T08:00:00Z", aktion: "update", felder: ["LLM_RANKING_PROMPT"] },
        ...VERSIONS.versionen,
      ],
    });
    fireEvent.change(promptField(), { target: { value: "newer" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(listStrategyVersions).toHaveBeenCalledTimes(2));
    const list = await screen.findByText("bbbb3333");
    expect(list).toBeTruthy();
  });

  // ── #4940: provider of the prompt run, new cadences, hints ─────────────────

  const LOCAL_HINT = "Local model: no web search. Answers are based on the model's training data and may be out of date.";
  const COST_HINT = "Web search requests may be billed by this provider above its free quota.";
  const PROVIDERS = ["system", "gemini", "openai", "anthropic"];
  const CADENCES = ["je_refresh", "stuendlich", "alle_4_stunden", "taeglich_vor_oeffnung", "woechentlich"];

  const withChoices = (choices: Record<string, string[]>, over: Record<string, string> = {}) => {
    const p = payload(over);
    return { ...p, meta: p.meta.map((m) => (m.key in choices ? { ...m, kind: "enum", choices: choices[m.key] } : m)) };
  };
  const providerField = () => screen.getByLabelText("Provider for the prompt") as HTMLSelectElement;
  const cadenceField = () => screen.getByLabelText("Refresh cadence") as HTMLSelectElement;
  const optionValues = (s: HTMLSelectElement) => Array.from(s.options).map((o) => o.value);
  const optionText = (s: HTMLSelectElement, value: string) =>
    Array.from(s.options).find((o) => o.value === value)?.textContent;

  async function renderPromptEditor(run: { anbieter: string | null; websuche: boolean | null } | null = null, over: Record<string, string> = {}) {
    getTradingSettings.mockResolvedValue(withChoices({ LLM_RANKING_ANBIETER: PROVIDERS, LLM_RANKING_RHYTHMUS: CADENCES }, over));
    fetchLlmTrichter.mockResolvedValue(run);
    await renderEditor();
    fireEvent.change(sourceSelect(), { target: { value: "llm" } });
    await waitFor(() => expect(fetchLlmTrichter).toHaveBeenCalled());
  }

  it("#4940: the provider field offers the registry choices", async () => {
    await renderPromptEditor();
    expect(optionValues(providerField())).toEqual(PROVIDERS);
    expect(providerField().value).toBe("system");
  });

  it("#4940: the registry choices win over the fixed fallback, an unknown value shows raw", async () => {
    getTradingSettings.mockResolvedValue(withChoices({ LLM_RANKING_RHYTHMUS: ["je_refresh", "alle_2_tage"] }));
    await renderEditor();
    fireEvent.change(sourceSelect(), { target: { value: "llm" } });
    expect(optionValues(cadenceField())).toEqual(["je_refresh", "alle_2_tage"]);
    expect(optionText(cadenceField(), "alle_2_tage")).toBe("alle_2_tage");
  });

  it("#4940: system carries the provider the engine resolved it to", async () => {
    await renderPromptEditor({ anbieter: "ollama", websuche: false });
    await waitFor(() => expect(optionText(providerField(), "system")).toBe("System (ollama)"));
  });

  it("#4940: system stays plain without a run", async () => {
    await renderPromptEditor();
    expect(optionText(providerField(), "system")).toBe("System");
  });

  it("#4940: a run on a chosen provider does not label system", async () => {
    await renderPromptEditor({ anbieter: "anthropic", websuche: true }, { LLM_RANKING_ANBIETER: "anthropic" });
    expect(optionText(providerField(), "system")).toBe("System");
    expect(screen.queryByText(LOCAL_HINT)).toBeNull();
  });

  it("#4940: the provider field is hidden with the source lstm", async () => {
    await renderEditor();
    expect(sourceSelect().value).toBe("lstm");
    expect(screen.queryByLabelText("Provider for the prompt")).toBeNull();
  });

  it("#4940: a local model shows the hint with role=status and no cost hint", async () => {
    await renderPromptEditor({ anbieter: "ollama", websuche: false });
    expect((await screen.findByText(LOCAL_HINT)).getAttribute("role")).toBe("status");
    expect(screen.queryByText(COST_HINT)).toBeNull();
  });

  it("#4940: a chosen cloud provider shows the cost hint instead of the local hint", async () => {
    await renderPromptEditor({ anbieter: "ollama", websuche: false });
    fireEvent.change(providerField(), { target: { value: "anthropic" } });
    expect(screen.getByText(COST_HINT)).toBeTruthy();
    expect(screen.queryByText(LOCAL_HINT)).toBeNull();
  });

  it("#4940: system shows no cost hint, even when it resolves to a cloud provider", async () => {
    await renderPromptEditor({ anbieter: "gemini", websuche: false });
    await waitFor(() => expect(optionText(providerField(), "system")).toBe("System (gemini)"));
    expect(screen.queryByText(COST_HINT)).toBeNull();
  });

  it("#4940: the hints leave with the field when the source no longer uses a prompt", async () => {
    await renderPromptEditor({ anbieter: "ollama", websuche: false });
    await screen.findByText(LOCAL_HINT);
    fireEvent.change(sourceSelect(), { target: { value: "lstm" } });
    expect(screen.queryByText(LOCAL_HINT)).toBeNull();
  });

  it("#4940: the cadence field offers the new cadences with their captions", async () => {
    await renderPromptEditor();
    expect(optionText(cadenceField(), "stuendlich")).toBe("Hourly");
    expect(optionText(cadenceField(), "alle_4_stunden")).toBe("Every 4 hours");
  });

  it("#4940: a new strategy starts at the registry default, not at the engine's provider", async () => {
    await renderPromptEditor(null, { LLM_RANKING_ANBIETER: "gemini" });
    expect(providerField().value).toBe("system");
  });

  it("#4940: saving sends the provider in werte", async () => {
    await renderPromptEditor();
    fireEvent.change(providerField(), { target: { value: "gemini" } });
    fireEvent.change(nameField(), { target: { value: "P" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveStrategy).toHaveBeenCalledTimes(1));
    expect(saveStrategy.mock.calls[0][1].LLM_RANKING_ANBIETER).toBe("gemini");
  });

  it("#4940: an unreadable funnel leaves the editor usable with plain System", async () => {
    getTradingSettings.mockResolvedValue(withChoices({ LLM_RANKING_ANBIETER: PROVIDERS }));
    fetchLlmTrichter.mockRejectedValue(new Error("offline"));
    await renderEditor();
    fireEvent.change(sourceSelect(), { target: { value: "llm" } });
    expect(optionText(providerField(), "system")).toBe("System");
  });

  it("trading settings unavailable: the editor shows the error state and no grid", async () => {
    getTradingSettings.mockRejectedValue(new HttpError(503, "Service Unavailable", "/api/trading-settings"));
    render(<StrategyEditor mode="new" strategies={STRATEGIES} onSaved={vi.fn()} onCancel={vi.fn()} />);
    expect(await screen.findByText("Could not load the data.")).toBeTruthy();
    expect(screen.queryByLabelText("Buy threshold")).toBeNull();
  });
});

describe("saveStrategy client (STW-4c #4873)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends POST /api/strategies with name, werte, acknowledgment and nonce", async () => {
    const { saveStrategy: realSave } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    const body = saved("A", { STOP_LOSS_PCT: "6.0" });
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true, status: 201, statusText: "Created", json: vi.fn().mockResolvedValue(body),
    });
    expect(await realSave("A", { STOP_LOSS_PCT: "6.0" }, "ack", "n-1")).toEqual(body);
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0];
    expect(String(url)).toMatch(/\/api\/strategies$/);
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      name: "A", werte: { STOP_LOSS_PCT: "6.0" }, acknowledgment: "ack", nonce: "n-1",
    });
  });
});
