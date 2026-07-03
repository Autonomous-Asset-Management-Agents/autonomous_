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
  fetchHealth: vi.fn().mockResolvedValue(null),
  fetchPortfolioSummary: vi.fn().mockResolvedValue({
    status: "success",
    equity: 100000,
    positions: [],
  }),
  fetchBenchmarkEquity: vi.fn().mockResolvedValue({ points: [], spy_points: [] }),
  fetchRoundTableDecisions: vi.fn().mockResolvedValue(null),
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
    installBridge(makeBridge({ hasKeychain: true }).bridge);
    render(<ConsoleApp />);
    await waitFor(() => expect(screen.getByRole("button", { name: /overview/i })).toBeTruthy(), { timeout: 3000 });
    expect(screen.queryByText(/set up autonomous_/i)).toBeNull();
  });

  it("happy path: first run → wizard → Ollama → launch → console", async () => {
    installBridge(makeBridge({ hasKeychain: false, ollama: fx.ollamaSuccess }).bridge);
    render(<ConsoleApp />);

    // Welcome
    await waitFor(() => expect(screen.getByText(/set up autonomous_/i)).toBeTruthy());
    typeInto("name", fx.operator.name);
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    // Alpaca — live validation succeeds, keys stored in the keychain
    await waitFor(() => expect(screen.getByText(/connect your broker/i)).toBeTruthy());
    typeInto("alpaca-key-id", fx.sampleKeys.alpacaKeyId);
    typeInto("alpaca-secret", fx.sampleKeys.alpacaSecret);
    fireEvent.click(screen.getByRole("button", { name: /validate & continue/i }));

    // LLM — pick local Ollama; provisioning streams progress then settles
    await waitFor(() => expect(screen.getByText(/choose your llm/i)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /local \(ollama\)/i }));
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    // Finish — launch the engine
    await waitFor(() => expect(screen.getByText(/you're ready/i)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /launch autonomous_/i }));

    // The operator console replaces the wizard
    await waitFor(() => expect(screen.getByRole("button", { name: /overview/i })).toBeTruthy(), { timeout: 3000 });
    expect(screen.queryByText(/you're ready/i)).toBeNull();
  });

  it("happy path: Gemini cloud provider stores the key and reaches launch", async () => {
    installBridge(makeBridge({ hasKeychain: false }).bridge);
    render(<ConsoleApp />);

    await waitFor(() => expect(screen.getByText(/set up autonomous_/i)).toBeTruthy());
    typeInto("name", fx.operator.name);
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(screen.getByText(/connect your broker/i)).toBeTruthy());
    typeInto("alpaca-key-id", fx.sampleKeys.alpacaKeyId);
    typeInto("alpaca-secret", fx.sampleKeys.alpacaSecret);
    fireEvent.click(screen.getByRole("button", { name: /validate & continue/i }));

    await waitFor(() => expect(screen.getByText(/choose your llm/i)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /cloud \(gemini\)/i }));
    typeInto("gemini-key", fx.sampleKeys.geminiKey);
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    await waitFor(() => expect(screen.getByText(/you're ready/i)).toBeTruthy());
  });

  it("guard: Continue stays disabled until the required field is filled", async () => {
    installBridge(makeBridge({ hasKeychain: false }).bridge);
    render(<ConsoleApp />);
    await waitFor(() => expect(screen.getByText(/set up autonomous_/i)).toBeTruthy());
    expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();
    typeInto("name", fx.operator.name);
    expect(screen.getByRole("button", { name: /continue/i })).not.toBeDisabled();
  });

  it("failure: Alpaca rejects the keys → error shown, user stays on the Alpaca step", async () => {
    installBridge(makeBridge({ hasKeychain: false, alpaca: fx.alpacaRejected }).bridge);
    render(<ConsoleApp />);

    await waitFor(() => expect(screen.getByText(/set up autonomous_/i)).toBeTruthy());
    typeInto("name", fx.operator.name);
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(screen.getByText(/connect your broker/i)).toBeTruthy());
    typeInto("alpaca-key-id", "bad");
    typeInto("alpaca-secret", "bad");
    fireEvent.click(screen.getByRole("button", { name: /validate & continue/i }));

    await waitFor(() => expect(screen.getByText(/alpaca rejected these keys/i)).toBeTruthy());
    expect(screen.getByText(/connect your broker/i)).toBeTruthy(); // still on the step
    expect(screen.queryByText(/choose your llm/i)).toBeNull();
  });

  it("failure: Alpaca unreachable → connection hint, no advance", async () => {
    installBridge(makeBridge({ hasKeychain: false, alpaca: fx.alpacaUnreachable }).bridge);
    render(<ConsoleApp />);

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

    await waitFor(() => expect(screen.getByText(/set up autonomous_/i)).toBeTruthy());
    typeInto("name", fx.operator.name);
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() => expect(screen.getByText(/connect your broker/i)).toBeTruthy());
    typeInto("alpaca-key-id", fx.sampleKeys.alpacaKeyId);
    typeInto("alpaca-secret", fx.sampleKeys.alpacaSecret);
    fireEvent.click(screen.getByRole("button", { name: /validate & continue/i }));

    await waitFor(() => expect(screen.getByText(/choose your llm/i)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /local \(ollama\)/i }));
    fireEvent.click(screen.getByRole("button", { name: /^continue$/i }));

    await waitFor(() => expect(screen.getByText(/install ollama from ollama\.com/i)).toBeTruthy());
    expect(screen.queryByText(/you're ready/i)).toBeNull();
  });
});
