/**
 * Console E2E harness (UX E2E, #1050) — Playwright browser layer.
 *
 * Makes the desktop operator console testable in a real Chromium against the
 * Vite dev build by faking its two external seams:
 *   1. the Electron shell  → an injected `window.aaagents` (via addInitScript,
 *      so `isDesktop()` is true and the keychain/engine IPC resolve);
 *   2. the engine HTTP      → `page.route` interception that fulfils the
 *      `/portfolio-summary`, `/round-table-decisions`, `/benchmark-equity` and
 *      `/chat` endpoints from the shared fixtures.
 *
 * Not a spec (no `.spec` suffix) — imported by the `*.e2e.spec.ts` files.
 *
 * NOTE: everything lives under `/console`, which is auth-gated by `PrivateRoute`
 * in the cloud build. The desktop bypass (`fix/desktop-console-login-wall`) lets
 * the injected bridge satisfy that gate; until it is on the branch under test,
 * `openConsole()` returns false and the spec skips itself rather than failing.
 */
import type { Page } from "@playwright/test";
import * as fx from "../fixtures/consoleFixtures";

export interface HarnessOptions {
  hasKeychain?: boolean;
  alpacaOk?: boolean;
  alpacaStatus?: number;
  ollamaOk?: boolean;
  engineStatus?: string;
  emptyBook?: boolean;
}

/** Inject the fake Electron bridge before any app script runs. */
export async function installDesktopBridge(page: Page, opts: HarnessOptions = {}): Promise<void> {
  const cfg = {
    hasKeychain: opts.hasKeychain ?? true,
    alpacaOk: opts.alpacaOk ?? true,
    alpacaStatus: opts.alpacaStatus ?? 200,
    ollamaOk: opts.ollamaOk ?? true,
    engineStatus: opts.engineStatus ?? "running",
    ollama: fx.ollamaSuccess,
    logs: fx.engineLogs,
  };
  await page.addInitScript((c) => {
    const noop = () => {};
    const unsub = () => noop;
    (window as unknown as { aaagents: Record<string, unknown> }).aaagents = {
      isDesktop: true,
      getEngineConnection: async () => ({ port: 8001, apiKey: "k-test" }),
      minimizeWindow: noop,
      toggleMaximizeWindow: noop,
      closeWindow: noop,
      readAuditChain: async () => [],
      hasKeychain: async () => c.hasKeychain,
      saveSecret: async () => ({ ok: true, error: null }),
      validateAlpaca: async () => ({ ok: c.alpacaOk, status: c.alpacaStatus }),
      saveSetupState: async () => undefined,
      onOllamaProgress: unsub,
      provisionOllama: async () => (c.ollamaOk ? c.ollama : { ok: false, needsManual: true, error: "Install Ollama from ollama.com, then retry." }),
      startEngine: async () => undefined,
      stopEngine: async () => undefined,
      getEngineStatus: async () => ({ status: c.engineStatus }),
      getEngineLogs: async () => c.logs,
      onEngineStatus: unsub,
      onEngineLog: unsub,
    };
  }, cfg);
}

/** Fulfil the engine HTTP endpoints from the shared fixtures. */
export async function routeEngine(page: Page, opts: HarnessOptions = {}): Promise<void> {
  const portfolio = opts.emptyBook ? fx.portfolioEmpty : fx.portfolioSummary;
  const roundTable = opts.emptyBook ? fx.roundTableEmpty : fx.roundTableDecisions;
  await page.route("**/portfolio-summary", (r) => r.fulfill({ json: portfolio }));
  await page.route("**/round-table-decisions", (r) => r.fulfill({ json: roundTable }));
  await page.route("**/benchmark-equity", (r) => r.fulfill({ json: fx.benchmarkEquity }));
  await page.route("**/chat", (r) => r.fulfill({ json: { reply: fx.chat.reply } }));
}

/**
 * Open the console. Returns false if the auth gate bounced us to /login (the
 * desktop bypass is not on this branch) so the caller can `test.skip()`.
 */
export async function openConsole(page: Page): Promise<boolean> {
  await page.goto("/console", { waitUntil: "domcontentloaded" });
  // Give the SPA a beat to either render the console or redirect to /login.
  await page.waitForTimeout(800);
  return !/\/login/.test(new URL(page.url()).pathname);
}
