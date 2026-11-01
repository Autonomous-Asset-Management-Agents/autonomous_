/**
 * Journey: Onboarding (first-run setup) — UX E2E #1050.
 *
 * Drives the real `ConsoleApp` first-run gate end-to-end through the setup
 * wizard with a faked desktop shell (`window.aaagents`): welcome → Alpaca
 * (live-validated) → LLM (Ollama provision / Gemini key) → launch → the
 * operator console. Covers the happy path, every guarded transition, and the
 * three failure modes (Alpaca rejected, Alpaca unreachable, Ollama manual).
 *
 * See src/test/journeys/README.md → "J1 Onboarding".
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ConsoleApp from "@/console/ConsoleApp";
import { makeBridge, installBridge, resetBridge } from "../fixtures/mockBridge";
import * as fx from "../fixtures/consoleFixtures";

vi.mock("@/lib/api", () => ({
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }),
  fetchHealth: vi.fn().mockResolvedValue(null),
  fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
  fetchPortfolioSummary: vi.fn().mockResolvedValue({
    status: "success",
    equity: 100000,
    positions: [],
  }),
  fetchBenchmarkEquity: vi.fn().mockResolvedValue({ points: [], spy_points: [] }),
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
  fetchPortfolioIntraday: vi.fn().mockResolvedValue([]),
  getHitlPolicy: vi.fn().mockResolvedValue({ HITL_AUTONOMOUS_UNLIMITED: true }),
}));


const typeInto = (label: RegExp | string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

describe("Journey · Onboarding (first-run setup)", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = (() => {}) as never;
    window.localStorage.clear();
  });
  afterEach(() => resetBridge());

  it("gate: an already-configured desktop skips the wizard and opens the console", async () => {
    installBridge(makeBridge({ hasKeychain: true, setupState: fx.setupStateComplete }).bridge);
    render(<ConsoleApp />);
    await waitFor(() => expect(screen.getByRole("button", { name: /overview/i })).toBeTruthy(), { timeout: 3000 });
    expect(screen.queryByText(/set up autonomous_/i)).toBeNull();
  });

  // B9: the customer entered the Alpaca keys, abandoned the model step and restarted. The keychain
  // is filled, setup.json holds only the name. The gate must NOT open the console; the wizard
  // resumes on the model step and the rest of the journey still reaches the console.
  it("gate B9: keys present but the model step never finished → wizard resumes on the model step → launch → console", async () => {
    const { bridge } = makeBridge({ hasKeychain: true, setupState: fx.setupStateAbandoned, ollama: fx.ollamaSuccess });
    installBridge(bridge);
    render(<ConsoleApp />);

    await waitFor(() => expect(screen.getByText(/choose your ai model/i)).toBeTruthy(), { timeout: 3000 });
    expect(screen.queryByRole("button", { name: /overview/i })).toBeNull();
    expect(screen.queryByText(/what you'll need/i)).toBeNull();
    expect(screen.queryByText(/connect your broker/i)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /download & activate mistral/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /^continue$/i })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    await waitFor(() => expect(screen.getByText(/you're ready/i)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /launch autonomous_/i }));

    await waitFor(() => expect(screen.getByRole("button", { name: /overview/i })).toBeTruthy(), { timeout: 3000 });
    expect(bridge.saveSetupState).toHaveBeenCalledWith({ setup_completed: true });
  });

  it("gate B9 migration: an install from before the done-mark (LLM_PROVIDER, no setup_completed) opens the console", async () => {
    installBridge(makeBridge({ hasKeychain: true, setupState: fx.setupStateLegacy }).bridge);
    render(<ConsoleApp />);
    await waitFor(() => expect(screen.getByRole("button", { name: /overview/i })).toBeTruthy(), { timeout: 3000 });
    expect(screen.queryByText(/choose your ai model/i)).toBeNull();
  });

  it("gate B9 + G5-3b: the resumed wizard still offers demo mode, with the way back to setup", async () => {
    installBridge(makeBridge({ hasKeychain: true, setupState: fx.setupStateAbandoned }).bridge);
    render(<ConsoleApp />);
    await waitFor(() => expect(screen.getByText(/choose your ai model/i)).toBeTruthy(), { timeout: 3000 });
    fireEvent.click(screen.getByRole("button", { name: /skip for now/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /overview/i })).toBeTruthy(), { timeout: 3000 });
    expect(screen.getByText(/finish setup/i)).toBeTruthy();
  });

  it("happy path: first run → wizard → Ollama → launch → console", async () => {
    const { bridge } = makeBridge({ hasKeychain: false, ollama: fx.ollamaSuccess });
    installBridge(bridge);
    render(<ConsoleApp />);

    // Welcome
    await waitFor(() => expect(screen.getByRole("button", { name: /get started/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /get started/i })); // #1977 preflight → name
    await waitFor(() => expect(screen.getByText(/set up autonomous_/i)).toBeTruthy());
    typeInto("name", fx.operator.name);
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    // Alpaca — live validation succeeds, keys stored in the keychain
    await waitFor(() => expect(screen.getByText(/connect your broker/i)).toBeTruthy());
    typeInto("alpaca-key-id", fx.sampleKeys.alpacaKeyId);
    typeInto("alpaca-secret", fx.sampleKeys.alpacaSecret);
    fireEvent.click(screen.getByRole("button", { name: /validate & continue/i }));

    // LLM — Mistral is the default local pick; provisioning streams progress then unlocks Continue
    await waitFor(() => expect(screen.getByText(/choose your ai model/i)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /download & activate mistral/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /^continue$/i })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    // Finish — launch the engine
    await waitFor(() => expect(screen.getByText(/you're ready/i)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /launch autonomous_/i }));

    // The operator console replaces the wizard
    await waitFor(() => expect(screen.getByRole("button", { name: /overview/i })).toBeTruthy(), { timeout: 3000 });
    expect(screen.queryByText(/you're ready/i)).toBeNull();
    // B9: the wizard marked itself done, so the next start opens the console without the keychain
    // alone having to vouch for it.
    expect(bridge.saveSetupState).toHaveBeenCalledWith({ setup_completed: true });
  });

  it("happy path: Gemini cloud provider stores the key and reaches launch", async () => {
    installBridge(makeBridge({ hasKeychain: false }).bridge);
    render(<ConsoleApp />);

    await waitFor(() => expect(screen.getByRole("button", { name: /get started/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /get started/i })); // #1977 preflight → name
    await waitFor(() => expect(screen.getByText(/set up autonomous_/i)).toBeTruthy());
    typeInto("name", fx.operator.name);
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(screen.getByText(/connect your broker/i)).toBeTruthy());
    typeInto("alpaca-key-id", fx.sampleKeys.alpacaKeyId);
    typeInto("alpaca-secret", fx.sampleKeys.alpacaSecret);
    fireEvent.click(screen.getByRole("button", { name: /validate & continue/i }));

    await waitFor(() => expect(screen.getByText(/choose your ai model/i)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /google gemini/i }));
    typeInto("gemini-key", fx.sampleKeys.geminiKey);
    fireEvent.click(screen.getByRole("button", { name: /save & use google gemini/i }));
    await waitFor(() => expect(screen.getByRole("button", { name: /^continue$/i })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    await waitFor(() => expect(screen.getByText(/you're ready/i)).toBeTruthy());
  });

  it("guard: Continue stays disabled until the required field is filled", async () => {
    installBridge(makeBridge({ hasKeychain: false }).bridge);
    render(<ConsoleApp />);
    await waitFor(() => expect(screen.getByRole("button", { name: /get started/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /get started/i })); // #1977 preflight → name
    await waitFor(() => expect(screen.getByText(/set up autonomous_/i)).toBeTruthy());
    expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();
    typeInto("name", fx.operator.name);
    expect(screen.getByRole("button", { name: /continue/i })).not.toBeDisabled();
  });

  it("failure: Alpaca rejects the keys → error shown, user stays on the Alpaca step", async () => {
    installBridge(makeBridge({ hasKeychain: false, alpaca: fx.alpacaRejected }).bridge);
    render(<ConsoleApp />);

    await waitFor(() => expect(screen.getByRole("button", { name: /get started/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /get started/i })); // #1977 preflight → name
    await waitFor(() => expect(screen.getByText(/set up autonomous_/i)).toBeTruthy());
    typeInto("name", fx.operator.name);
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(screen.getByText(/connect your broker/i)).toBeTruthy());
    typeInto("alpaca-key-id", "bad");
    typeInto("alpaca-secret", "bad");
    fireEvent.click(screen.getByRole("button", { name: /validate & continue/i }));

    await waitFor(() => expect(screen.getByText(/alpaca rejected these keys/i)).toBeTruthy());
    expect(screen.getByText(/connect your broker/i)).toBeTruthy(); // still on the step
    expect(screen.queryByText(/choose your ai model/i)).toBeNull();
  });

  it("failure: Alpaca unreachable → connection hint, no advance", async () => {
    installBridge(makeBridge({ hasKeychain: false, alpaca: fx.alpacaUnreachable }).bridge);
    render(<ConsoleApp />);

    await waitFor(() => expect(screen.getByRole("button", { name: /get started/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /get started/i })); // #1977 preflight → name
    await waitFor(() => expect(screen.getByText(/set up autonomous_/i)).toBeTruthy());
    typeInto("name", fx.operator.name);
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(screen.getByText(/connect your broker/i)).toBeTruthy());
    typeInto("alpaca-key-id", fx.sampleKeys.alpacaKeyId);
    typeInto("alpaca-secret", fx.sampleKeys.alpacaSecret);
    fireEvent.click(screen.getByRole("button", { name: /validate & continue/i }));

    await waitFor(() => expect(screen.getByText(/couldn't reach alpaca/i)).toBeTruthy());
  });

  it("failure: Ollama needs a manual install → actionable error, no advance", async () => {
    installBridge(makeBridge({ hasKeychain: false, ollama: fx.ollamaNeedsManual }).bridge);
    render(<ConsoleApp />);

    await waitFor(() => expect(screen.getByRole("button", { name: /get started/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /get started/i })); // #1977 preflight → name
    await waitFor(() => expect(screen.getByText(/set up autonomous_/i)).toBeTruthy());
    typeInto("name", fx.operator.name);
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(screen.getByText(/connect your broker/i)).toBeTruthy());
    typeInto("alpaca-key-id", fx.sampleKeys.alpacaKeyId);
    typeInto("alpaca-secret", fx.sampleKeys.alpacaSecret);
    fireEvent.click(screen.getByRole("button", { name: /validate & continue/i }));

    await waitFor(() => expect(screen.getByText(/choose your ai model/i)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /download & activate mistral/i }));

    await waitFor(() => expect(screen.getByText(/install ollama from ollama\.com/i)).toBeTruthy());
    expect(screen.queryByText(/you're ready/i)).toBeNull();
  });
});
