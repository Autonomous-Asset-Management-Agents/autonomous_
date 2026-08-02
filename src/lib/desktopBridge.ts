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

/** Lifecycle state of the engine subprocess (mirrors native-engine-manager).
 *  "restarting" (LSR R8 #2261) is a BENIGN, expected transient — a deliberate
 *  disable/Paper → restart() window — rendered neutrally, never as an error. */
export type EngineStatus =
  | "stopped"
  | "starting"
  | "running"
  | "stopping"
  | "restarting"
  | "error"
  | "unavailable";

export interface EngineStatusPayload {
  status: EngineStatus;
  detail?: string | null;
}

/** The subset of the preload surface the console reads (see desktop/electron/preload.cjs). */
interface AAAgentsBridge {
  isDesktop?: boolean;
  getVersion?: () => Promise<string>;
  getUpdateStatus?: () => Promise<UpdateStatus>;
  checkForUpdatesNow?: () => Promise<UpdateStatus>;
  getEngineConnection?: () => Promise<unknown>;
  minimizeWindow?: () => void;
  toggleMaximizeWindow?: () => void;
  closeWindow?: () => void;
  readAuditChain?: (maxLines?: number) => Promise<unknown[]>;
  exportTelemetry?: () => Promise<unknown>;
  startEngine?: () => Promise<void>;
  stopEngine?: () => Promise<void>;
  restartEngine?: (switchId?: string, opts?: { goLive?: boolean }) => Promise<void>;
  recoverToPaper?: () => Promise<{ ok: boolean }>;
  getEngineStatus?: () => Promise<{ status: string }>;
  getEngineLogs?: () => Promise<string[]>;
  onEngineStatus?: (cb: (payload: EngineStatusPayload) => void) => () => void;
  onEngineLog?: (cb: (line: string) => void) => () => void;
  hasKeychain?: () => Promise<boolean>;
  getKeychainStatus?: () => Promise<Record<string, boolean>>;
  saveSecret?: (key: string, value: string) => Promise<{ ok: boolean; error: string | null }>;
  validateAlpaca?: (keyId: string, secret: string, live?: boolean) => Promise<{ ok: boolean; status: number; equity?: number | null }>;
  saveSetupState?: (partial: Record<string, unknown>) => Promise<unknown>;
  getSetupState?: () => Promise<Record<string, unknown>>;
  provisionOllama?: (model?: string) => Promise<OllamaProvisionResult>;
  onOllamaProgress?: (cb: (p: OllamaProgress) => void) => () => void;
  listOllamaModels?: () => Promise<OllamaModelsResult>;
  claimBeta?: () => Promise<unknown>;
}

/** Result of the free-beta Senior unlock (ADR-GTM-1b). `claimed` → the shell copied
 *  the bundled offline license into license.json (tier now Senior); `error` → any
 *  failure/unavailable (incl. the browser build, where there is no shell). The offline
 *  unlock has no cap, but `cap-reached` stays in the union for forward compatibility:
 *  the CTA still handles it (flips to the paid price) if a future issuer ever returns it. */
export interface ClaimBetaResult {
  status: "claimed" | "cap-reached" | "error";
  tier?: string;
  error?: string;
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
  /** #2454: the account equity on success (non-secret), for the 10%-of-equity HITL default. null otherwise. */
  equity?: number | null;
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

/** Installed local models the Ollama daemon reports (GET /api/tags). `reachable=false` means the
 *  daemon didn't answer (not running / not installed) → treat every local model as not installed. */
export interface OllamaModelsResult {
  reachable: boolean;
  models: string[];
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
  const isTest = typeof process !== "undefined" && (process.env.NODE_ENV === "test" || !!process.env.VITEST);
  const isOss = !isTest && typeof window !== "undefined" && (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1" || window.location.hostname.startsWith("192.168."));
  return !!bridge()?.isDesktop || isOss;
}

/** The desktop app version (package.json via Electron app.getVersion()), or null
 *  in the browser where the bridge is absent (the caller hides the version then). */
export async function getAppVersion(): Promise<string | null> {
  return (await bridge()?.getVersion?.()) ?? null;
}

/** #2077 notify-only update status from the shell. `updateAvailable` is false in the browser
 *  (no bridge) and whenever the check hasn't run or failed — fully fail-safe. */
export interface UpdateStatus {
  updateAvailable: boolean;
  latestVersion: string | null;
  downloadUrl: string | null;
  changelog: string | null;
  checkedAt: string | null;
}

const NO_UPDATE: UpdateStatus = {
  updateAvailable: false,
  latestVersion: null,
  downloadUrl: null,
  changelog: null,
  checkedAt: null,
};

/** Last update status the shell evaluated (start + every 6h). Null-safe in the browser. */
export async function getUpdateStatus(): Promise<UpdateStatus> {
  return (await bridge()?.getUpdateStatus?.()) ?? NO_UPDATE;
}

/** Force an update re-check (Settings → System "Check for updates"). Null-safe in the browser. */
export async function checkForUpdatesNow(): Promise<UpdateStatus> {
  return (await bridge()?.checkForUpdatesNow?.()) ?? NO_UPDATE;
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
/** INC-1b (#2215): atomic engine restart (stop → wait-for-port-free → start) in ONE
 *  IPC call, so callers no longer race stopEngine()+startEngine() into the [Errno 10048]
 *  bind window. Falls back to stop+start where the bridge predates engine:restart. */
export async function restartEngine(switchId?: string, opts?: { goLive?: boolean }): Promise<void> {
  const b = bridge();
  if (b?.restartEngine) {
    // #2277 INC-1: forward the per-switch correlation id (the console's nonce) into the engine spawn.
    // #2465: opts.goLive carries the one-shot consent directive so the fresh boot MAY go live (still
    // AND-gated with the WORM chain in the shell). Omitted on every non-consent restart → Paper.
    await b.restartEngine(switchId, opts);
    return;
  }
  await b?.stopEngine?.();
  await b?.startEngine?.();
}
/** INC-3 (#2213): recover a dead/failed LIVE boot onto paper. Forces the NEXT engine
 *  boot to PAPER_TRADING (one-shot shell flag) and atomically restarts — it NEVER writes
 *  the WORM chain (Python owns the disable record, issued once the engine is healthy on
 *  paper). No-op in the browser (the bridge is absent). */
export async function recoverToPaper(): Promise<void> {
  await bridge()?.recoverToPaper?.();
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

/** Per-slot OS-keychain presence for the broker-keys badge (INC-5/F5). Returns a
 *  `{ MANAGED_KEY: boolean }` map (presence only — NEVER any secret value). Fail-safe:
 *  no bridge / not desktop / any error → `{}`, so the caller shows no badge and never
 *  throws. Backed by `python -m core.keychain_cli status --json` via the shell. */
export async function getKeychainStatus(): Promise<Record<string, boolean>> {
  try {
    const r = await bridge()?.getKeychainStatus?.();
    return r && typeof r === "object" ? r : {};
  } catch {
    return {};
  }
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
  return r ?? { ok: false, status: 0, equity: null };
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
  model?: string,
): Promise<OllamaProvisionResult> {
  const b = bridge();
  if (!b?.provisionOllama) return { ok: false, error: "desktop-only" };
  const unsub = b.onOllamaProgress?.(onProgress) ?? (() => {});
  try {
    // `model` selects which Ollama tag to pull (Mistral default / Llama lightweight); the main
    // process falls back to its vetted DEFAULT_MODEL when it's undefined.
    return await b.provisionOllama(model);
  } finally {
    unsub();
  }
}

/** List the local models the Ollama daemon has pulled (GET /api/tags via the shell). Fail-safe:
 *  no bridge / not desktop / any error → `{ reachable:false, models:[] }`, so callers treat every
 *  local model as not installed and never throw. Used by the LLM card for real install status. */
export async function listOllamaModels(): Promise<OllamaModelsResult> {
  const b = bridge();
  if (!b?.listOllamaModels) return { reachable: false, models: [] };
  try {
    const r = await b.listOllamaModels();
    return {
      reachable: !!r?.reachable,
      models: Array.isArray(r?.models) ? r.models.map((m) => String(m)) : [],
    };
  } catch {
    return { reachable: false, models: [] };
  }
}

/**
 * Claim the free-beta Senior unlock (ADR-GTM-1b). Desktop-only and fully OFFLINE:
 * the shell copies the bundled Ed25519-signed license into license.json (no cloud
 * call, no login), so the local engine resolves Senior on the next entitlement poll.
 * Resolves `{ status:"error" }` in the browser (no bridge).
 */
export async function claimBeta(): Promise<ClaimBetaResult> {
  const r = await bridge()?.claimBeta?.();
  return (r as ClaimBetaResult) ?? { status: "error", error: "desktop-only" };
}

/** Test hook — clear the cached connection. */
export function __resetDesktopBridge(): void {
  _conn = null;
}
