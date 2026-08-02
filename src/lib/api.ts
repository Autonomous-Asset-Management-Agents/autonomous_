/**
 * ai_trading_bot Engine API client.
 * Base URL: http://localhost:8001 (engine must be running).
 * Override with ?engine_port=8002 in the page URL if the engine started on another port.
 * In public view-only mode (e.g. aaagents.de), uses VITE_PUBLIC_API_URL when set.
 */

import { isPublicViewOnly, getPublicApiBase } from "./publicMode";
import { auth } from "./firebase";
import { isDesktop, getEngineKey, getEnginePort, initDesktopBridge } from "./desktopBridge";

const DEFAULT_ENGINE_PORT = 8001;

/** Default port for the read-only public API proxy when testing public build locally. */
const PUBLIC_PROXY_LOCAL_PORT = 8002;

/** Default public API URL for aaagents.de (Cloudflare Tunnel to local engine). */
const DEFAULT_PUBLIC_API_URL = "https://api.aaagents.de";

/** Resolve engine base URL: public API when in public mode; on localhost use local proxy so portfolio data loads. */
export function getApiBase(): string {
  if (typeof window === "undefined") return `http://localhost:${DEFAULT_ENGINE_PORT}`;
  // Desktop (Electron) edition: talk to the engine the shell spawned, on its
  // loopback port (delivered via the engine:get-connection IPC, cached at app
  // start). Takes precedence over the cloud resolution below.
  if (isDesktop()) {
    const port = getEnginePort();
    if (port) return `http://127.0.0.1:${port}`;
  }
  if (isPublicViewOnly()) {
    const host = window.location.hostname.toLowerCase();
    const isLocal = host === "localhost" || host === "127.0.0.1";
    // On localhost (testing public build), use local proxy so current holdings etc. are shown
    if (isLocal) return `http://localhost:${PUBLIC_PROXY_LOCAL_PORT}`;
    // Use configured public API URL, or default to api.aaagents.de
    const publicUrl = getPublicApiBase();
    return publicUrl || DEFAULT_PUBLIC_API_URL;
  }
  const params = new URLSearchParams(window.location.search);
  const port = params.get("engine_port");
  if (port) return `http://localhost:${port}`;

  // Use explicit VITE_API_BASE_URL if provided (e.g. localhost:8081 build)
  const configuredUrl = import.meta.env?.VITE_API_BASE_URL;
  if (configuredUrl && typeof configuredUrl === "string" && configuredUrl.trim()) {
    return configuredUrl.trim().replace(/\/$/, "");
  }

  // Fallback for production console — use direct Cloud Run URL
  // (api.aaagents.de will be the primary once Cloudflare is configured)
  if (window.location.host === "localhost:8081") {
    const url = "https://aaa-api-public-lwkxsmb7dq-ey.a.run.app";
    console.log("[API] Production Console Mode Detected. Base URL:", url);
    return url;
  }

  // Option B: Wenn wir im Vite Dev Server laufen (import.meta.env.DEV),
  // nutzen wir IMMER den relativen Pfad ("/api"), unabhängig von der IP/Hostname.
  // Das erlaubt LAN-Zugriffe (z.B. 192.168.x.x:8082) via Vite Proxy ohne CORS-Probleme.
  // Option B: Wenn wir im Vite Dev Server laufen (import.meta.env.DEV) ODER im
  // Docker-Container (wo Nginx /api auflöst), nutzen wir IMMER den relativen Pfad ("/api").
  // Das erlaubt LAN-Zugriffe ohne CORS-Probleme und leitet den Traffic durch den Auth-Proxy.
  return "/api";
}

export const API_BASE = getApiBase();
console.log("[API] API_BASE initialized as:", API_BASE);

export interface Position {
  symbol: string;
  qty: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  total_score?: number;
  momentum_score?: number;
  conviction_score?: number;
  days_held?: number;
}

export interface PortfolioSummaryResponse {
  status: "success" | "error";
  /** PR-B: real account currency (Alpaca account.currency, e.g. "USD"). */
  currency?: string;
  /** Bug B (0.3.5): which account this snapshot belongs to — the engine's PAPER_TRADING
   *  mode (true=paper, false=live), the SAME source /health reports. Lets the mode-switch
   *  overlay reject a stale cross-account poll. Absent on the error path / pre-0.3.5 engines. */
  paper_trading?: boolean;
  summary?: string | null;
  message?: string;
  positions?: Position[];
  equity?: number;
  last_equity?: number;
  recent_debates?: unknown[];
  rebalance_recommendations?: unknown[];
  agent_statuses?: {
    name: string;
    agent_name: string;
    score: number;
    weight: number;
    reasoning: string;
    vetoed: boolean;
    signal: "BUY" | "SELL" | "HOLD" | string;
  }[];
  total_unrealized_pnl?: number;
}

/** News article from /recent-news */
export interface NewsArticle {
  title: string;
  ticker?: string;
  sentiment?: string;
  score?: number;
  published?: string;
  url?: string;
}

/** GET /recent-news response */
export interface RecentNewsResponse {
  status: string;
  articles: NewsArticle[];
}

export interface StrategyResponse {
  strategy: "RLAgent" | "LSTMDynamic";
}

/** Range for stock history: 1d | 1w | 1m | 1y | max */
export type StockHistoryRange = "1d" | "1w" | "1m" | "1y" | "max";

export interface StockHistoryPoint {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface StockHistoryResponse {
  status: "success" | "error";
  symbol?: string;
  range?: string;
  data?: StockHistoryPoint[];
  message?: string;
  intraday?: boolean;
}

/** GET /stock-history?symbol=...&period=1d|1w|1m|1y|max */
export async function fetchStockHistory(
  symbol: string,
  period: StockHistoryRange = "1m"
): Promise<StockHistoryResponse> {
  try {
    const data = await fetchJson<StockHistoryResponse>(
      `/stock-history?symbol=${encodeURIComponent(symbol)}&period=${period}`
    );
    return data;
  } catch {
    return { status: "error", data: [] };
  }
}

/**
 * RC2-1: a non-2xx engine response becomes a typed error carrying the HTTP status, so callers can
 * distinguish an auth failure (401/403) from a hung/refused connection. Every fail-soft wrapper
 * catches it and returns its existing null/empty fallback exactly as before — a 403 degrades
 * gracefully instead of poisoning the store with an error body cast to the happy-path type.
 */
export class HttpError extends Error {
  readonly status: number;
  constructor(status: number, statusText: string, path: string) {
    super(`Engine request failed: HTTP ${status} ${statusText || ""} (${path})`.trim());
    this.name = "HttpError";
    this.status = status;
  }
}

/**
 * RC2-1: a GENEROUS request ceiling. Sits safely above the observed ~39s legitimate slow-but-
 * successful engine response, so a request that truly hangs aborts and errors (fail-soft callers
 * then recover) while a slow success well under the cap is never cut off.
 */
const FETCH_TIMEOUT_MS = 45_000;

async function fetchJson<T>(
  path: string,
  options?: RequestInit,
  _retried = false,
  timeoutMs: number = FETCH_TIMEOUT_MS,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  // Inject Firebase JWT if user is logged in
  if (auth.currentUser) {
    try {
      const token = await auth.currentUser.getIdToken();
      headers["Authorization"] = `Bearer ${token}`;
    } catch (error: unknown) {
      console.warn("Failed to get Firebase token:", error);
    }
  } else {
    // OSS Mode Fallback: LocalMockAuth strictly requires structurally valid Bearer tokens.
    // In Enterprise Mode, FirebaseAuth will cryptographically reject this dummy token (401),
    // which is gracefully handled by the UI as an "Offline" state without redirect loops.
    headers["Authorization"] = "Bearer oss-mode-bypass";
  }

  // Desktop edition: the engine requires X-Engine-Key (require_engine_key is
  // 503-fail-closed). The shell owns the per-session key; null in the browser.
  if (isDesktop()) {
    const key = getEngineKey();
    if (key) headers["X-Engine-Key"] = key;
  }

  // RC2-1: bound the request with an AbortController timeout so a truly hung engine errors
  // (and fail-soft callers recover) instead of leaving the UI frozen forever — while a slow-but-
  // legitimate success well under the cap is never aborted. `timeoutMs` defaults to the generous
  // FETCH_TIMEOUT_MS; a fast poll loop (e.g. the switch reconcile) passes a SHORT cap so a single
  // stalled call retries quickly instead of hanging the whole loop for 45 s.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${getApiBase()}${path}`, {
      ...options,
      headers: { ...headers, ...options?.headers },
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }

  // RC2-1: desktop key self-heal. A renderer reload / restarted engine rotates the per-session
  // X-Engine-Key, so a cached stale key 401/403s. Re-resolve the connection from the shell
  // (engine:get-connection) and retry the request ONCE — guarded by `_retried` so it never loops.
  if ((res.status === 401 || res.status === 403) && isDesktop() && !_retried) {
    await initDesktopBridge();
    return fetchJson<T>(path, options, true, timeoutMs);
  }

  // RC2-1: a non-2xx is an error, not silently-parsed "data" — throw so fail-soft callers degrade.
  if (!res.ok) {
    throw new HttpError(res.status, res.statusText, path);
  }

  return res.json() as Promise<T>;
}

/** GET /strategy - check if engine is running and get current strategy */
export async function fetchStrategy(): Promise<StrategyResponse | null> {
  try {
    const data = await fetchJson<StrategyResponse>("/strategy");
    return data;
  } catch {
    return null;
  }
}

/** GET /health - lightweight engine health incl. strategy_running + paper_trading (#1425).
 *  LSR R2 (#2253): live_enable_failed_reason + degraded_live surface WHY a live arming booted
 *  PAPER (I-4) so the console can show an explicit reason instead of silently sitting on paper.
 *  LSR R4 (#2251): `status` ("starting" | "healthy") lets the account reconcile tell a still-booting
 *  engine from a serving one, so it settles the toggle only from an AUTHORITATIVE /health. */
export async function fetchHealth(timeoutMs?: number): Promise<{
  status?: string;
  strategy_running?: boolean;
  paper_trading?: boolean;
  system_halted?: boolean;
  live_enable_failed_reason?: string | null;
  degraded_live?: boolean;
} | null> {
  try {
    // `timeoutMs` lets a fast poll loop (the live⇄paper switch reconcile) cap each /health call
    // short so a stalled engine can't hang it for the 45 s generic FETCH_TIMEOUT_MS. Undefined →
    // the generic cap (background poll / one-shot reads are unchanged).
    return await fetchJson<{
      status?: string;
      strategy_running?: boolean;
      paper_trading?: boolean;
      system_halted?: boolean;
      live_enable_failed_reason?: string | null;
      degraded_live?: boolean;
    }>("/health", undefined, false, timeoutMs);
  } catch {
    return null;
  }
}

/** GTM-1 #1915 — resolved tier for the console, from GET /api/entitlement/status.
 *  Read-only, runs on the LOCAL engine (offline, reads license.json). `can_upgrade`
 *  is true only for Junior (BASIC) desktops → drives the sidebar Upgrade CTA. */
export interface EntitlementStatus {
  tier: string;
  allow_live: boolean;
  can_upgrade: boolean;
  /** Whether the desktop Simulation/backtest page is exposed for this edition.
   *  Central switch on the engine (core/entitlement/tier.py) — currently false for
   *  every tier while the backtest runtime is hardened. Absent on older engines. */
  simulation_enabled?: boolean;
}

/** GET /api/entitlement/status — the local engine's resolved tier. Returns null on
 *  any error (engine down / cloud browser build → CTA simply stays hidden). */
export async function fetchEntitlementStatus(): Promise<EntitlementStatus | null> {
  try {
    return await fetchJson<EntitlementStatus>("/api/entitlement/status");
  } catch {
    return null;
  }
}

/** Engine deep-health view (DASH-1 T5, #1473): market-open state + broker/component status. */
export interface DeepHealth {
  status: string;
  is_market_open: boolean;
  strategy_running?: boolean;
  components: {
    alpaca: { status: string; details?: Record<string, unknown> };
    cloud_sql?: { status: string };
    models?: Record<string, string>;
  };
}

/** GET /health/deep - market-open + broker/component status. Returns null on any
 *  failure (engine down, or the intentional degraded-500) so callers render an
 *  honest "—" rather than a fabricated value. */
export async function fetchDeepHealth(): Promise<DeepHealth | null> {
  try {
    return await fetchJson<DeepHealth>("/health/deep");
  } catch {
    return null;
  }
}

/** POST /api/live/enable — record the operator's deliberate Art-14 live-trading enablement on the
 *  tamper-evident WORM chain (#1425). `nonce` is a client-generated replay-distinct value. The
 *  engine must then be restarted for the shell to re-read the chain and boot live. Throws on error. */
export async function liveEnable(acknowledgment: string, nonce: string): Promise<void> {
  await fetchJson("/api/live/enable", {
    method: "POST",
    body: JSON.stringify({ acknowledgment, nonce }),
  });
}

/** POST /api/live/disable — revoke live-trading enablement (#1425); a restart returns to paper. */
export async function liveDisable(acknowledgment: string, nonce: string): Promise<void> {
  await fetchJson("/api/live/disable", {
    method: "POST",
    body: JSON.stringify({ acknowledgment, nonce }),
  });
}

/** #2277 INC-3 (§4b R4): report the post-switch reconcile outcome so the engine emits a
 *  `health_reconcile` OTEL span → the diagnose-export can reconstruct a failed Live⇄Paper reconcile.
 *  FAIL-OPEN by contract: a telemetry post must NEVER block or fail the switch, so all errors are
 *  swallowed. Returns true iff the post succeeded (for tests/observability). */
export async function postSwitchReconcile(payload: {
  switch_id?: string;
  attempts: number;
  settled_status?: string | null;
  paper_trading?: boolean | null;
}): Promise<boolean> {
  try {
    await fetchJson("/api/telemetry/switch-reconcile", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    return true;
  } catch {
    return false;
  }
}

/** GET /portfolio-summary - get portfolio and positions */
export async function fetchPortfolioSummary(): Promise<PortfolioSummaryResponse | null> {
  try {
    const data = await fetchJson<PortfolioSummaryResponse>("/portfolio-summary");
    return data;
  } catch {
    return null;
  }
}

/**
 * One agent's vote within a Round-Table decision. This is the engine's REAL
 * shape: `/round-table-decisions` returns `asdict(SenateSession)` verbatim
 * (core/round_table/senate_log.py + recent_decisions.py), so a vote is a
 * serialized VoteResult — verified against the round-trip contract test
 * tests/unit/test_g1b_console_routes.py::test_seeded_store_round_trips.
 */
export interface RoundTableVote {
  name?: string;
  agent_name?: string;
  score?: number;
  weight?: number;
  reasoning?: string;
  vetoed?: boolean;
  signal?: "BUY" | "SELL" | "HOLD" | string;
}

/** A Round-Table decision row = asdict(SenateSession). No pre-aggregated tally. */
export interface RoundTableDecision {
  symbol: string;
  signal_action?: "BUY" | "SELL" | "HOLD" | string | null;
  consensus_score?: number;
  gatekeeper_approved?: boolean;
  gatekeeper_reason?: string;
  timestamp?: string;
  session_id?: string;
  votes?: RoundTableVote[];
  /** RQ-1 (#1516): final execution-gate outcome joined server-side (Iron-Dome /
   *  risk / kill-switch). "executed" | "blocked:*" | "hitl_held" | "pending";
   *  null for a HOLD (no order was ever attempted). */
  execution_outcome?: string | null;
  execution_outcome_reason?: string;
}

export interface RoundTableDecisionsResponse {
  status?: string;
  total?: number;
  decisions?: RoundTableDecision[];
}

/** GET /round-table-decisions — latest decision per symbol (G1b). null on error. */
export async function fetchRoundTableDecisions(): Promise<RoundTableDecisionsResponse | null> {
  try {
    return await fetchJson<RoundTableDecisionsResponse>("/round-table-decisions");
  } catch {
    return null;
  }
}

/** POST /chat — ask the engine an open-ended question. Returns null on any error. */
export async function sendChat(message: string): Promise<string | null> {
  try {
    const data = await fetchJson<{ reply?: string; message?: string }>("/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    return data.reply ?? data.message ?? null;
  } catch {
    return null;
  }
}

/** POST /start-live */
export async function startLive(): Promise<{ status: string }> {
  return fetchJson("/start-live", { method: "POST" });
}

/** POST /stop */
export async function stop(): Promise<{ status: string }> {
  return fetchJson("/stop", { method: "POST" });
}

/** POST /panic-sell */
export async function panicSell(): Promise<{ status: string; message?: string }> {
  return fetchJson("/panic-sell", { method: "POST" });
}

/** POST /reset-kill-switch — clear a tripped risk kill switch (system_halted, set by the
 *  Risk Manager on a portfolio-stop / drawdown halt). The engine does NOT auto-resume —
 *  call startLive() afterwards to restart the trading loop. */
export async function resetKillSwitch(): Promise<{ status: string; message?: string }> {
  return fetchJson("/reset-kill-switch", { method: "POST" });
}

// ── HITL autonomy policy (LIVE-1 T2, #1425) ──────────────────────────────────
/** The full HITL policy (GET /api/hitl/policy). `HITL_ENABLED` is read-only (env-only, C2). */
export interface HitlPolicy {
  HITL_ENABLED: boolean;
  HITL_MAX_VALUE_PER_TRADE: number;
  HITL_MAX_VALUE_PER_DAY: number;
  HITL_AUTONOMOUS_UNLIMITED: boolean;
  HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: boolean;
  HITL_EXPIRY_SECONDS: number;
}

/** The runtime-adjustable subset (POST body). `HITL_ENABLED` is intentionally absent — enabling
 *  HITL is the env+redeploy step (C2), never an API toggle; the engine rejects it with HTTP 422. */
export type HitlPolicyUpdate = Omit<HitlPolicy, "HITL_ENABLED">;

/** GET /api/hitl/policy — the live engine's real human-in-the-loop autonomy policy. */
export async function getHitlPolicy(): Promise<HitlPolicy> {
  return fetchJson<HitlPolicy>("/api/hitl/policy");
}

/** POST /api/hitl/policy — persist the runtime-adjustable limits to the real engine. */
export async function updateHitlPolicy(body: HitlPolicyUpdate): Promise<HitlPolicy> {
  return fetchJson<HitlPolicy>("/api/hitl/policy", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** POST /set-strategy */
export async function setStrategy(strategy: "RLAgent" | "LSTMDynamic"): Promise<{ status: string; strategy?: string; message?: string }> {
  return fetchJson("/set-strategy", {
    method: "POST",
    body: JSON.stringify({ strategy }),
  });
}

/** POST /run-simulation */
export async function runSimulation(params: {
  start_date: string;
  end_date: string;
  initial_capital: number;
  symbol_sample_mode: "full_market" | "sp500";
}): Promise<{ status: string; message?: string }> {
  return fetchJson("/run-simulation", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

/** A point on a backtest equity curve (SIM-1 T1, #1484). */
export interface SimEquityPoint {
  date: string;
  equity: number;
}

/** GET /simulation-result response — the last backtest result (reload-safe poll target). */
export interface SimulationResult {
  status: "idle" | "running" | "complete";
  start_date?: string;
  end_date?: string;
  initial_capital?: number;
  strategy_equity?: SimEquityPoint[];
  spy_equity?: SimEquityPoint[];
  final_equity?: number;
  total_return?: number;
  trades_count?: number;
  /** SIM-1 T2 (#1485): true iff the backtest used point-in-time S&P 500 membership (no
   * survivorship bias). false ⇒ the current index was used for past dates — the UI flags it. */
  survivorship_adjusted?: boolean;
  /** SIM honest-metrics: risk-adjusted figures the backtest already computes (compute_performance_metrics),
   *  surfaced so the Console shows more than raw return. */
  metrics?: {
    sharpe_ratio_annual?: number | null;
    sortino_ratio_annual?: number | null;
    max_drawdown_pct?: number;
    calmar_ratio_annual?: number | null;
  };
  spy_return?: number; // benchmark (S&P 500) total return % over the same period
  outperformance?: number; // strategy_return − spy_return (alpha), in percentage points
}

/** GET /simulation-result */
export async function getSimulationResult(): Promise<SimulationResult> {
  return fetchJson("/simulation-result");
}

/** POST /run-learning */
export async function runLearning(params: {
  start_date: string;
  end_date: string;
  initial_capital: number;
}): Promise<{ status: string; message?: string }> {
  return fetchJson("/run-learning", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

/** Benchmark equity curve point */
export interface BenchmarkEquityPoint {
  date: string;
  equity: number;
}

/** GET /benchmark-equity response */
export interface BenchmarkEquityResponse {
  points: BenchmarkEquityPoint[];
  spy_points: BenchmarkEquityPoint[];
  start_date?: string;
  end_date?: string;
  strategy?: string;
  initial_capital?: number;
  final_equity?: number;
  message?: string;
}

/** GET /benchmark-equity - portfolio vs S&P equity curves */
export async function fetchBenchmarkEquity(): Promise<BenchmarkEquityResponse> {
  try {
    return await fetchJson<BenchmarkEquityResponse>("/benchmark-equity");
  } catch (err) {
    // #2588: never a SILENT empty — the error contract carries a message so the
    // poll can keep the last good curve instead of wiping it with this stub.
    console.warn("fetchBenchmarkEquity failed:", err);
    return { points: [], spy_points: [], message: "fetch_failed" };
  }
}

/** GET /portfolio-intraday response (#2124) — intraday account-equity curve. */
export interface PortfolioIntradayResponse {
  points: BenchmarkEquityPoint[];
}

/**
 * GET /portfolio-intraday — real intraday equity curve (Alpaca 5Min/1H) for the
 * Overview's 1D/1W ranges. `range` is "1D" | "1W". Fail-soft to an empty curve.
 */
export async function fetchPortfolioIntraday(range: string): Promise<PortfolioIntradayResponse> {
  try {
    return await fetchJson<PortfolioIntradayResponse>(`/portfolio-intraday?range=${encodeURIComponent(range)}`);
  } catch {
    return { points: [] };
  }
}

/** Trade from /recent-trades */
export interface RecentTrade {
  id: string;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price: number;
  filled_at: string | null;
}

export interface RecentTradesResponse {
  status: string;
  trades: RecentTrade[];
  message?: string;
}

/** GET /recent-trades - last N filled orders from Alpaca */
export async function fetchRecentTrades(limit = 20): Promise<RecentTradesResponse> {
  try {
    return await fetchJson<RecentTradesResponse>(`/recent-trades?limit=${limit}`);
  } catch {
    return { status: "error", trades: [] };
  }
}

/** Response of /activities - the FULL paginated filled-order history */
export interface ActivitiesResponse {
  status: string;
  trades: RecentTrade[];
  count?: number;
  truncated?: boolean;
  message?: string;
}

/** GET /activities - the FULL filled-order history from Alpaca (paginated past 500) */
export async function fetchActivities(): Promise<ActivitiesResponse> {
  try {
    return await fetchJson<ActivitiesResponse>("/activities");
  } catch {
    return { status: "error", trades: [], truncated: false };
  }
}

/** One OPEN / working order from /open-orders (#2137, display-only). */
export interface OpenOrder {
  id?: string;
  symbol?: string;
  side?: string;
  qty?: number;
  type?: string;
  limit_price?: number | null;
  stop_price?: number | null;
  status?: string;
  submitted_at?: string | null;
}

/** Response of /open-orders - the operator's currently OPEN/working orders. */
export interface OpenOrdersResponse {
  status: string;
  orders: OpenOrder[];
  count?: number;
  message?: string;
}

/** GET /open-orders - the operator's currently OPEN/working orders (#2137). Fail-soft. */
export async function fetchOpenOrders(): Promise<OpenOrdersResponse> {
  try {
    return await fetchJson<OpenOrdersResponse>("/open-orders");
  } catch {
    return { status: "error", orders: [] };
  }
}

/** POST /cancel-order - HITL cancel of one OPEN order (#2137). Art. 14 human-oversight control:
 *  always available regardless of autonomous mode. Fail-soft → { status:"error" } on any failure. */
export async function cancelOrder(orderId: string): Promise<{ status: string }> {
  try {
    return await fetchJson<{ status: string }>("/cancel-order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order_id: orderId }),
    });
  } catch {
    return { status: "error" };
  }
}

/** GET /recent-news - news articles for held symbols */
export async function fetchRecentNews(): Promise<RecentNewsResponse> {
  try {
    return await fetchJson<RecentNewsResponse>("/recent-news");
  } catch {
    return { status: "error", articles: [] };
  }
}

/** GET /auth/alpaca/login — DISABLED in OSS Edition (returns HTTP 400).
 * Kept for API surface compatibility. Do not call from UI components.
 * Enterprise edition uses GCP Secret Manager + Firebase OAuth instead.
 * @deprecated OSS: use ALPACA_API_KEY in .env.oss
 */
export async function getAlpacaAuthUrl(): Promise<{ auth_url?: string; error?: string }> {
  try {
    const res = await fetchJson<{ url?: string; detail?: string; error?: string }>("/auth/alpaca/login");

    // FastAPI returns HTTP errors in the 'detail' field
    if (res.detail) {
      return { error: res.detail };
    }
    if (res.error) {
      return { error: res.error };
    }
    if (!res.url) {
      return { error: "No authentication URL returned from the server." };
    }

    return { auth_url: res.url };
  } catch (error: unknown) {
    return { error: error instanceof Error ? error.message : "Failed to initiate login flow" };
  }
}

/** POST /settings/alpaca-keys — DISABLED in OSS Edition (returns HTTP 400).
 * Kept for API surface compatibility. Do not call from UI components.
 * Enterprise edition manages credentials via GCP Secret Manager.
 * @deprecated OSS: use ALPACA_API_KEY + ALPACA_SECRET_KEY in .env.oss
 */
export async function saveAlpacaKeys(api_key: string, secret_key: string): Promise<{ status: string; message?: string }> {
  try {
    return await fetchJson<{ status: string; message?: string }>("/settings/alpaca-keys", {
      method: "POST",
      body: JSON.stringify({ api_key, secret_key }),
    });
  } catch {
    return { status: "error", message: "Network error" };
  }
}

export interface RiskLimits {
  status: string;
  bot_status?: string;
  risk_limits?: {
    max_daily_drawdown_pct?: number;
    max_position_size_pct?: number;
  };
  message?: string;
}

export async function fetchRiskLimits(): Promise<RiskLimits | null> {
  try {
    return await fetchJson<RiskLimits>("/settings/risk-limits");
  } catch {
    return null;
  }
}

export async function updateRiskLimits(limits: Record<string, number>): Promise<{ status: string }> {
  try {
    return await fetchJson<{ status: string }>("/settings/risk-limits", {
      method: "POST",
      body: JSON.stringify({ risk_limits: limits }),
    });
  } catch {
    return { status: "error" };
  }
}

export async function updateBotStatus(status: "active" | "inactive"): Promise<{ status: string; bot_status?: string }> {
  try {
    return await fetchJson<{ status: string; bot_status: string }>("/bot/status", {
      method: "POST",
      body: JSON.stringify({ status }),
    });
  } catch {
    return { status: "error" };
  }
}

// ─── Specialist-Reports endpoint (G1b′ / RPAR-#1284) ─────────────────────────

/**
 * A single specialist report as returned by GET /specialist-reports.
 *
 * Mirrors the engine's `_serialize_specialist_report` (api_routes.py) EXACT
 * snake_case key-set. All fields are optional-safe (the engine may add new
 * fields on any deploy, and emits empty defaults for insight-quality features
 * that are still dormant). The adapter (live/specialist.ts) maps this to the
 * camelCase `SpecialistReport` view-model.
 */
export interface SpecialistReportDTO {
  symbol: string;
  sentiment_score?: number;          // 0..100 engine scale (default 50.0)
  recommendation?: string;           // lowercase "buy" | "hold" | "sell" (default "hold")
  confidence?: number;               // 0..1
  escalate?: boolean;
  escalate_reason?: string;
  reasons?: string[];
  about?: string;
  company_summary?: string;
  edge_signals?: string[];
  investment_thesis?: string;
  bull_case?: string;
  bear_case?: string;
  news_summary?: string;
  headlines?: string[];
  alternative_signals?: string;
  insider_trades_count?: number;
  // #2587: null = the SOURCE was unavailable this cycle (fetch failed/blocked),
  // an int = really checked (0 = checked, nothing found). The card renders "—".
  political_trades_count?: number | null;
  material_events_count?: number;
  reddit_mentions?: number | null;
  wiki_spike?: boolean;
  short_interest_pct?: number | null;
  updated_at?: string | null;        // ISO timestamp
  ml_direction?: string;             // "up" | "down" | "neutral" | "unavailable" (default "unavailable")
  ml_confidence?: number | null;
  ml_base_return_pct?: number | null;
  ml_bear_return_pct?: number | null;
  ml_bull_return_pct?: number | null;
  signal_quality?: string;           // "ml_plus_llm" | "llm_only" | … (default "llm_only")
  report_quality?: number;           // #1490: 0-100 deterministic quality score (flag-gated; absent when OFF)
  report_quality_label?: string;     // #1490: "Strong" | "Fair" | "Thin"
  report_markdown?: string | null;   // RPT-GOLD (#1998): full auditable Markdown note (flag-gated; absent when OFF)
  report_audit_ok?: boolean | null;  // RPT-GOLD: mechanical AuditResult.integrity_ok
  report_audit_summary?: string | null; // RPT-GOLD: e.g. "61 claims · 0 flags"
  // The note's OWN cross-sectional verdict ("buy"/"hold"/"sell"), or null when it
  // ABSTAINS (no rank). The reader badge reads this, NOT `recommendation` above.
  // Flag-gated: absent (undefined) when the report generator is OFF. See specialist.ts.
  report_recommendation?: string | null;
  walkforward_ic?: number | null;
  walkforward_sharpe?: number | null;
  ml_attention_features?: string[];
  pros?: { text: string; value: string }[];
  cons?: { text: string; value: string }[];
  summary?: string;
  data_quality?: number | null;
  degraded?: boolean;
  // rsi_14 / macd_signal are declared once below (card block) with the types the
  // backend actually emits (report.py: macd_signal is a string verdict).
  // Rich-synthesis card (Direction A) — present ONLY when
  // SPECIALIST_SYNTHESIS_CARD_ENABLED (absent/undefined when OFF). Each citation
  // binds a displayed bull/bear/thesis claim to a real headline; dropped records
  // rejected (ungrounded/fabricated) claims. The vol band is a non-directional
  // HAR-RV spread (base is always 0.0 — no drift claim).
  synthesis_citations?: {
    kind: string;
    claim: string;
    headline_index: number;
    headline_text: string;
  }[];
  synthesis_dropped?: { kind: string; claim: string; reason: string }[];
  grounding_ok?: boolean | null;
  vol_band_low_pct?: number | null;
  vol_band_base_pct?: number | null;
  vol_band_high_pct?: number | null;
  vol_band_sigma_pct?: number | null;
  scenario_basis?: string;
  // LSTM cross-section standing (single rank definition, board-vote parity)
  lstm_xsec_percentile?: number | null;
  lstm_xsec_rank?: number | null;
  lstm_xsec_universe?: number | null;
  last_close?: number | null;
  price_band_low?: number | null;
  price_band_high?: number | null;
  chg_20d_pct?: number | null;
  dist_52w_high_pct?: number | null;
  rsi_14?: number | null;
  macd_signal?: string | null;
}

/** GET /specialist-reports response. */
export interface SpecialistReportsResponse {
  status: "ok" | "unavailable" | "error" | string;
  total?: number;
  escalations?: number;
  registry_status?: Record<string, unknown>;
  reports: SpecialistReportDTO[];
  message?: string;
}

/**
 * GET /specialist-reports — per-symbol specialist cards (auth: require_engine_key).
 * Returns `{status:"error", reports:[]}` on any network/parse error so callers
 * never have to null-check; the registry-off case is the engine's own
 * `{status:"unavailable", reports:[]}` contract.
 */
export async function fetchSpecialistReports(): Promise<SpecialistReportsResponse> {
  try {
    return await fetchJson<SpecialistReportsResponse>("/specialist-reports");
  } catch {
    return { status: "error", reports: [] };
  }
}
