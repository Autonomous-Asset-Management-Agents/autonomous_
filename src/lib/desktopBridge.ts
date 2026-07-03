/**
 * Desktop (Electron) bridge access for the AAAgents console (G3, #1050).
 *
 * "One frontend for all editions": this same console runs in the browser
 * (cloud) and inside the Electron shell (desktop). The shell exposes a minimal
 * `window.aaagents` surface (G2 preload). The only thing engine calls need from
 * it is the per-session connection — the loopback port the shell spawned the
 * engine on, and the X-Engine-Key the engine requires (`require_engine_key` is
 * 503-fail-closed). Both arrive via the `engine:get-connection` IPC.
 *
 * The connection is cached once at app start (`initDesktopBridge`) so the hot
 * fetch path stays synchronous; the cloud path is untouched (every getter
 * returns null in the browser).
 */

interface EngineConnection {
  port: number;
  apiKey: string;
}

/** Lifecycle state of the engine subprocess (mirrors native-engine-manager). */
export type EngineStatus = "stopped" | "starting" | "running" | "stopping" | "error" | "unavailable";

export interface EngineStatusPayload {
  status: EngineStatus;
  detail?: string | null;
}

/** The subset of the preload surface the console reads (see desktop/electron/preload.cjs). */
interface AAAgentsBridge {
  isDesktop?: boolean;
  getEngineConnection?: () => Promise<unknown>;
  minimizeWindow?: () => void;
  toggleMaximizeWindow?: () => void;
  closeWindow?: () => void;
  readAuditChain?: (maxLines?: number) => Promise<unknown[]>;
  exportTelemetry?: () => Promise<unknown>;
  startEngine?: () => Promise<void>;
  stopEngine?: () => Promise<void>;
  getEngineStatus?: () => Promise<{ status: string }>;
  getEngineLogs?: () => Promise<string[]>;
  onEngineStatus?: (cb: (payload: EngineStatusPayload) => void) => () => void;
  onEngineLog?: (cb: (line: string) => void) => () => void;
  hasKeychain?: () => Promise<boolean>;
  saveSecret?: (key: string, value: string) => Promise<{ ok: boolean; error: string | null }>;
  validateAlpaca?: (keyId: string, secret: string, live?: boolean) => Promise<{ ok: boolean; status: number }>;
  saveSetupState?: (partial: Record<string, unknown>) => Promise<unknown>;
  getSetupState?: () => Promise<Record<string, unknown>>;
  provisionOllama?: () => Promise<OllamaProvisionResult>;
  onOllamaProgress?: (cb: (p: OllamaProgress) => void) => () => void;
}

/** Result of a keychain write via the bridge. */
export interface SaveSecretResult {
  ok: boolean;
  error: string | null;
}

/** Result of the live Alpaca key check (status only). */
export interface AlpacaValidateResult {
  ok: boolean;
  status: number;
}

/** Streaming progress while Ollama pulls a model. */
export interface OllamaProgress {
  status: string;
  percent: number | null;
}

/** Result of the Ollama provisioning flow (install → serve → pull → health). */
export interface OllamaProvisionResult {
  ok: boolean;
  model?: string;
  baseUrl?: string;
  needsManual?: boolean;
  error?: string;
}

// Type `window.aaagents` globally so every consumer is type-safe and NOBODY
// has to cast `window` — all Electron IPC goes through this module's helpers.
declare global {
  interface Window {
    aaagents?: AAAgentsBridge;
  }
}

function bridge(): AAAgentsBridge | undefined {
  if (typeof window === "undefined") return undefined;
  return window.aaagents;
}

let _conn: EngineConnection | null = null;

/** True when running inside the Electron desktop shell. */
export function isDesktop(): boolean {
  return !!bridge()?.isDesktop;
}

/**
 * Resolve and cache the engine connection from the shell. No-op (and never
 * throws) in the browser or if the IPC fails — the renderer must still load;
 * engine calls then behave as today (no X-Engine-Key) until re-init.
 */
export async function initDesktopBridge(): Promise<void> {
  const b = bridge();
  if (!b?.isDesktop || typeof b.getEngineConnection !== "function") return;
  try {
    const raw = await b.getEngineConnection();
    if (
      raw &&
      typeof (raw as EngineConnection).port === "number" &&
      typeof (raw as EngineConnection).apiKey === "string"
    ) {
      _conn = { port: (raw as EngineConnection).port, apiKey: (raw as EngineConnection).apiKey };
    }
  } catch (err) {
    console.warn("desktopBridge: getEngineConnection failed — engine calls will lack the key:", err);
  }
}

/** Cached engine API key (X-Engine-Key), or null outside the desktop app. */
export function getEngineKey(): string | null {
  return _conn?.apiKey ?? null;
}

/** Cached engine loopback port, or null outside the desktop app. */
export function getEnginePort(): number | null {
  return _conn?.port ?? null;
}

/** Frameless-window controls — the single sanctioned path for these IPC calls.
 *  No-op in the browser (the bridge is absent). */
export function minimizeWindow(): void {
  bridge()?.minimizeWindow?.();
}
export function toggleMaximizeWindow(): void {
  bridge()?.toggleMaximizeWindow?.();
}
export function closeWindow(): void {
  bridge()?.closeWindow?.();
}

/**
 * Read the engine's hash-linked audit log (last `maxLines` senate decisions).
 * Desktop-only: returns the raw entry objects from the local audit_log file.
 * In the browser the bridge is absent → resolves to [] (cloud has no local
 * file; that page falls back to a desktop-only note).
 */
export async function readAuditChain(maxLines?: number): Promise<unknown[]> {
  const raw = await bridge()?.readAuditChain?.(maxLines);
  return Array.isArray(raw) ? raw : [];
}

/** Result of the user-initiated diagnostics export (INF-13 a, #1372). */
export interface TelemetryExportResult {
  ok: boolean;
  path?: string;
  records?: number;
  files?: number;
  empty?: boolean;
  canceled?: boolean;
  error?: string;
}

/** Export the local, already-scrubbed telemetry store to a user-picked file
 *  (desktop-only). The user explicitly chooses where to save — there is NO
 *  background egress. Resolves `{ ok:false, error:"desktop-only" }` in the browser. */
export async function exportTelemetry(): Promise<TelemetryExportResult> {
  const r = await bridge()?.exportTelemetry?.();
  return (r as TelemetryExportResult) ?? { ok: false, error: "desktop-only" };
}

/** Engine lifecycle — the single sanctioned path for these IPC calls. Each is a
 *  no-op / unavailable in the browser (the bridge is absent). */
export async function startEngine(): Promise<void> {
  await bridge()?.startEngine?.();
}
export async function stopEngine(): Promise<void> {
  await bridge()?.stopEngine?.();
}
export async function getEngineStatus(): Promise<EngineStatus> {
  const r = await bridge()?.getEngineStatus?.();
  return (r?.status as EngineStatus) ?? "unavailable";
}
export async function getEngineLogs(): Promise<string[]> {
  return (await bridge()?.getEngineLogs?.()) ?? [];
}
/** Subscribe to engine status pushes; returns an unsubscribe fn (no-op in browser). */
export function onEngineStatus(cb: (payload: EngineStatusPayload) => void): () => void {
  return bridge()?.onEngineStatus?.(cb) ?? (() => {});
}
/** Subscribe to engine log lines; returns an unsubscribe fn (no-op in browser). */
export function onEngineLog(cb: (line: string) => void): () => void {
  return bridge()?.onEngineLog?.(cb) ?? (() => {});
}

/** First-run keychain check for the setup-wizard gate (G4-1). Desktop-only:
 *  in the browser the bridge is absent → false (the cloud build skips the
 *  wizard entirely; see ConsoleApp). */
export async function hasKeychain(): Promise<boolean> {
  return (await bridge()?.hasKeychain?.()) ?? false;
}

/** Write one OS-keychain secret via the bridge (G4-1). Desktop-only; resolves
 *  { ok:false } in the browser (no bridge). */
export async function saveSecret(key: string, value: string): Promise<SaveSecretResult> {
  const r = await bridge()?.saveSecret?.(key, value);
  return r ?? { ok: false, error: "desktop-only" };
}

/** Live Alpaca key check via the main process (G4-2). Desktop-only; resolves
 *  `{ ok:false, status:0 }` in the browser (no bridge). */
export async function validateAlpaca(
  keyId: string,
  secret: string,
  live = false,
): Promise<AlpacaValidateResult> {
  // `live` selects the Alpaca endpoint the shell validates against: api.alpaca.markets (live) vs
  // paper-api.alpaca.markets (paper) — a live key only validates on the live API (#1425).
  const r = await bridge()?.validateAlpaca?.(keyId, secret, live);
  return r ?? { ok: false, status: 0 };
}

/** Persist the wizard's non-secret state to setup.json (G4-2). No-op in the
 *  browser. */
export async function saveSetupState(partial: Record<string, unknown>): Promise<void> {
  await bridge()?.saveSetupState?.(partial);
}

/** Read the non-secret setup.json (name + LLM choice). Returns {} in the browser
 *  or when nothing has been saved yet. Used by the dashboard to greet by name. */
export async function getSetupState(): Promise<Record<string, unknown>> {
  return (await bridge()?.getSetupState?.()) ?? {};
}

/** Provision local Ollama (install → serve → pull → health), streaming progress
 *  to `onProgress` (G4-3). Desktop-only; resolves `{ ok:false }` in the browser.
 *  Unsubscribes the progress listener when the flow settles. */
export async function provisionOllama(
  onProgress: (p: OllamaProgress) => void,
): Promise<OllamaProvisionResult> {
  const b = bridge();
  if (!b?.provisionOllama) return { ok: false, error: "desktop-only" };
  const unsub = b.onOllamaProgress?.(onProgress) ?? (() => {});
  try {
    return await b.provisionOllama();
  } finally {
    unsub();
  }
}

/** Test hook — clear the cached connection. */
export function __resetDesktopBridge(): void {
  _conn = null;
}
