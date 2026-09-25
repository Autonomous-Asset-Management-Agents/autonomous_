/**
 * Mock desktop bridge (UX E2E, #1050).
 *
 * `window.aaagents` is the single seam between the React console and the
 * Electron shell (engine lifecycle, keychain, Alpaca validation, Ollama
 * provisioning). The journey suites drive complete user flows by installing a
 * configurable fake of that seam — the real components, stores, adapters and
 * polling hooks all run unchanged; only the shell IPC is faked.
 *
 * `makeBridge()` returns the bridge plus emitters so a test can push live
 * engine status / log lines exactly as the shell streams them over IPC.
 */
import { vi } from "vitest";
import type { EngineStatus } from "@/lib/desktopBridge";
import * as fx from "./consoleFixtures";

export interface MakeBridgeOptions {
  /** Does the OS keychain already hold secrets? false → first-run wizard. */
  hasKeychain?: boolean;
  /**
   * Non-secret setup.json as the shell would return it (B9). The gate needs BOTH a filled keychain
   * and the wizard's done-mark (`setup_completed`, or `LLM_PROVIDER` for older installs) to open
   * the console. Default: a completed setup when `hasKeychain`, else `{}` (first run).
   * `saveSetupState` merges into it, so a later `getSetupState` sees what the wizard wrote.
   */
  setupState?: Record<string, unknown>;
  /** Result of the live Alpaca key check. */
  alpaca?: { ok: boolean; status: number };
  /** Result of Ollama provisioning. */
  ollama?: { ok: boolean; model?: string; baseUrl?: string; needsManual?: boolean; error?: string };
  /** Whether keychain writes succeed. */
  saveSecretOk?: boolean;
  /** Engine status reported by the first getEngineStatus() probe. */
  engineStatus?: EngineStatus;
  /** Seed log lines replayed by getEngineLogs(). */
  logs?: string[];
}

export interface MockBridge {
  bridge: Record<string, unknown>;
  /** Push a live engine status update (as the shell does over IPC). */
  emitStatus: (s: { status: EngineStatus; detail?: string | null }) => void;
  /** Push a live engine log line. */
  emitLog: (line: string) => void;
}

export function makeBridge(opts: MakeBridgeOptions = {}): MockBridge {
  const {
    hasKeychain = true,
    setupState = hasKeychain ? fx.setupStateComplete : {},
    alpaca = fx.alpacaValid,
    ollama = fx.ollamaSuccess,
    saveSecretOk = true,
    engineStatus = "running",
    logs = fx.engineLogs,
  } = opts;
  // setup.json as the shell keeps it: a copy, so a test's fixture object is never mutated.
  let setupJson: Record<string, unknown> = { ...setupState };

  const statusCbs = new Set<(p: { status: EngineStatus; detail?: string | null }) => void>();
  const logCbs = new Set<(line: string) => void>();
  const ollamaCbs = new Set<(p: { status: string; percent: number | null }) => void>();

  const bridge: Record<string, unknown> = {
    isDesktop: true,

    // Engine connection (desktop API key + port)
    getEngineConnection: vi.fn().mockResolvedValue({ port: 8001, apiKey: "k-test" }),

    // Window chrome
    minimizeWindow: vi.fn(),
    toggleMaximizeWindow: vi.fn(),
    closeWindow: vi.fn(),

    // Audit chain reader
    readAuditChain: vi.fn().mockResolvedValue([]),

    // ── Keychain + onboarding ──
    hasKeychain: vi.fn().mockResolvedValue(hasKeychain),
    saveSecret: vi.fn().mockResolvedValue({ ok: saveSecretOk, error: saveSecretOk ? null : "keychain write failed" }),
    validateAlpaca: vi.fn().mockResolvedValue(alpaca),
    // Non-secret setup.json (B9): writes merge like setup-manager.cjs, reads return the merged state.
    saveSetupState: vi.fn().mockImplementation(async (partial: Record<string, unknown>) => {
      setupJson = { ...setupJson, ...(partial ?? {}) };
      return setupJson;
    }),
    getSetupState: vi.fn().mockImplementation(async () => ({ ...setupJson })),
    onOllamaProgress: (cb: (p: { status: string; percent: number | null }) => void) => {
      ollamaCbs.add(cb);
      return () => { ollamaCbs.delete(cb); };
    },
    provisionOllama: vi.fn().mockImplementation(async () => {
      // Stream a couple of progress ticks just like the real pull, then settle.
      ollamaCbs.forEach((cb) => cb({ status: "Pulling model…", percent: 40 }));
      ollamaCbs.forEach((cb) => cb({ status: "Pulling model…", percent: 100 }));
      return ollama;
    }),

    // ── Engine lifecycle ──
    startEngine: vi.fn().mockImplementation(async () => { statusCbs.forEach((cb) => cb({ status: "running" })); }),
    stopEngine: vi.fn().mockImplementation(async () => { statusCbs.forEach((cb) => cb({ status: "stopped" })); }),
    getEngineStatus: vi.fn().mockResolvedValue({ status: engineStatus }),
    getEngineLogs: vi.fn().mockResolvedValue(logs),
    onEngineStatus: (cb: (p: { status: EngineStatus; detail?: string | null }) => void) => {
      statusCbs.add(cb);
      return () => { statusCbs.delete(cb); };
    },
    onEngineLog: (cb: (line: string) => void) => {
      logCbs.add(cb);
      return () => { logCbs.delete(cb); };
    },
  };

  return {
    bridge,
    emitStatus: (s) => statusCbs.forEach((cb) => cb(s)),
    emitLog: (line) => logCbs.forEach((cb) => cb(line)),
  };
}

/** Install a bridge as `window.aaagents` (desktop build). */
export function installBridge(bridge: Record<string, unknown> | undefined): void {
  (window as unknown as { aaagents?: unknown }).aaagents = bridge;
}

/** Remove the bridge (cloud build) and clear the cached engine connection. */
export function resetBridge(): void {
  (window as unknown as { aaagents?: unknown }).aaagents = undefined;
}
