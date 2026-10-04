/**
 * LSR R6b (#2264) — FULL-STACK e2e for the Desktop Live⇄Paper switch.
 *
 * WHY THIS EXISTS (Audit #06/#07, docs/reviews/live-switch-safety-audit-2026-07-18.md:23-24):
 * The real user-blocker (Issue #2182, fixed by PR #2184) was that `liveEnable`/`liveDisable`
 * called `fetchJson(path, { body })` WITHOUT `method:"POST"` → fetchJson spreads options into
 * fetch() → default GET → the Fetch API throws `TypeError: Request with GET/HEAD method cannot
 * have body` CLIENT-SIDE → the request never reached the engine. Every existing test structurally
 * misses this seam:
 *   - liveTradingSwitchReconcile.test.tsx / the console card tests MOCK `@/lib/api` → the real
 *     liveEnable→fetchJson→fetch path never runs.
 *   - liveEnableMethod.test.ts calls the real wrapper but pins method/URL/body in ISOLATION.
 *   - test_live_paper_switch_e2e.py (PR #2250) is the BACKEND endpoint via TestClient — not the JS.
 *
 * THE DEFINING PROPERTY OF THIS TEST: `@/lib/api` AND `@/lib/desktopBridge` are REAL (unmocked).
 * Only the NETWORK boundary (`global.fetch`) is stubbed, acting as a stubbed engine, plus the
 * Electron shell surface (`window.aaagents`, which the real preload provides) and the Firebase
 * auth header (non-seam). A GET-vs-POST regression — or a missing restart/reconcile — turns this
 * test RED. This is the UI→wrapper→network→reconcile chain; it complements (does not duplicate)
 * liveEnableMethod.test.ts and the #2250 backend e2e.
 */
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useStore } from "@/console/store/useStore";
import { initDesktopBridge, __resetDesktopBridge } from "@/lib/desktopBridge";

// Firebase auth is NOT the seam under test (method/URL/body is). Stub the module so fetchJson's
// `auth.currentUser` read is deterministic (null → the OSS `Bearer oss-mode-bypass` branch), exactly
// as liveEnableMethod.test.ts:10 does. @/lib/api and @/lib/desktopBridge are intentionally REAL.
vi.mock("@/lib/firebase", () => ({ auth: { currentUser: null } }));

import { LiveTradingSwitchCard } from "@/console/desktop/LiveTradingSwitchCard";
import { LiveConsentModal } from "@/console/desktop/LiveConsentModal";
// #2465: switch logic moved to the global LiveConsentModal — render both (tile → signal → modal).
function Harness() {
  return (
    <>
      <LiveTradingSwitchCard />
      <LiveConsentModal />
    </>
  );
}

const ENGINE_PORT = 8001;
const ENGINE_KEY = "test-engine-key";

/** A captured live-arming request (what the REAL fetchJson built and handed to fetch). */
interface CapturedReq {
  url: string;
  method: string;
  body: string | undefined;
  headers: Record<string, string>;
}

/** Stateful stubbed engine — mirrors the real causal chain: enable/disable write the WORM intent,
 *  the restart IPC re-reads it and boots the target account, /health then reflects it. */
interface StubEngine {
  bootedPaper: boolean;
  nextBootLive: boolean;
  phase: "starting" | "running";
  enableReq: CapturedReq | null;
  disableReq: CapturedReq | null;
}

let engine: StubEngine;
let restartSpy: ReturnType<typeof vi.fn>;

function newEngine(bootedPaper: boolean): StubEngine {
  return {
    bootedPaper,
    nextBootLive: !bootedPaper,
    phase: "running",
    enableReq: null,
    disableReq: null,
  };
}

function jsonResponse(data: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
  } as unknown as Response;
}

/** The network boundary. Serves the stubbed engine AND emulates the real Fetch API's refusal to
 *  carry a body on GET/HEAD — the EXACT client-side failure #2182 produced — so a GET-vs-POST
 *  regression reproduces the real-world break instead of silently passing. */
function installFetchStub() {
  const impl = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body != null ? String(init.body) : undefined;
    const headers = (init?.headers ?? {}) as Record<string, string>;

    // Real Fetch semantics: a body on GET/HEAD throws client-side (this is #2182).
    if (body !== undefined && (method === "GET" || method === "HEAD")) {
      throw new TypeError("Request with GET/HEAD method cannot have body");
    }

    if (url.includes("/api/live/enable")) {
      expect(method).toBe("POST"); // explicit belt-and-suspenders pin of the transport method
      engine.enableReq = { url, method, body, headers };
      engine.nextBootLive = true; // WORM intent recorded
      return jsonResponse({}, 200);
    }
    if (url.includes("/api/live/disable")) {
      expect(method).toBe("POST");
      engine.disableReq = { url, method, body, headers };
      engine.nextBootLive = false; // WORM intent revoked
      return jsonResponse({}, 200);
    }
    if (url.includes("/health")) {
      // "starting" window omits paper_trading (audit #08 contract); the atomic restart IPC waits
      // for READY, so the reconcile read below always lands on "running".
      if (engine.phase === "starting") return jsonResponse({ status: "starting" }, 200);
      return jsonResponse({ strategy_running: true, paper_trading: engine.bootedPaper }, 200);
    }
    // Defensive: any other endpoint a child card might poll → benign empty ok.
    return jsonResponse({}, 200);
  };
  global.fetch = vi.fn(impl) as unknown as typeof global.fetch;
}

/** Drive the confirm modal all the way to "Enable live trading" (both consents). */
async function armLive() {
  fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
  fireEvent.click(screen.getByLabelText("ack-live"));
  fireEvent.click(screen.getByLabelText("ack-advance-approval"));
  fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
}

describe("LSR R6b — full-stack Live⇄Paper switch through the REAL transport (#2264, #06/#07)", () => {
  beforeEach(async () => {
    engine = newEngine(true); // default: booted paper

    // The Electron shell surface the real desktopBridge reads. restartEngine is the shell IPC
    // (no real counterpart in a test) — it re-reads the WORM intent and boots the target account.
    restartSpy = vi.fn(async () => {
      engine.phase = "starting";
      engine.bootedPaper = !engine.nextBootLive; // restart re-reads the WORM intent
      engine.phase = "running";
    });
    (window as unknown as { aaagents: unknown }).aaagents = {
      isDesktop: true,
      getEngineConnection: async () => ({ port: ENGINE_PORT, apiKey: ENGINE_KEY }),
      restartEngine: restartSpy,
      getKeychainStatus: async () => ({ ALPACA_LIVE_API_KEY: true, ALPACA_LIVE_SECRET_KEY: true }),
    };
    __resetDesktopBridge();
    await initDesktopBridge(); // cache port + X-Engine-Key so the REAL desktop transport branch runs

    installFetchStub();
    useStore.setState({ allowLive: true, paperTrading: null, requestLiveConsent: false, requestSwitchToPaper: false, switchReconciling: false, switchConfirmFailed: false, switchError: null, modeSwitch: null });
  });

  afterEach(() => {
    cleanup();
    __resetDesktopBridge();
    delete (window as unknown as { aaagents?: unknown }).aaagents;
    vi.restoreAllMocks();
  });

  it("Enable live: REAL liveEnable→fetchJson→fetch issues POST /api/live/enable, then the label settles to Live", async () => {
    engine = newEngine(true); // booted paper
    useStore.setState({ allowLive: true, paperTrading: true });

    render(<Harness />);
    await armLive();

    // The state line settles to the account the (stubbed) engine actually booted after restart.
    await waitFor(() =>
      expect(screen.getByText(/orders execute with real money/i)).toBeTruthy(),
    );

    // The REAL wrapper drove a REAL fetch to the enable endpoint.
    expect(engine.enableReq).not.toBeNull();
    const req = engine.enableReq as CapturedReq;
    // (1) method MUST be POST — a GET-vs-POST regression (#2182) makes this whole flow fail.
    expect(req.method).toBe("POST");
    // (2) correct URL, built by the REAL desktop transport branch (http://127.0.0.1:<port>).
    expect(req.url).toContain(`http://127.0.0.1:${ENGINE_PORT}`);
    expect(req.url).toContain("/api/live/enable");
    // (3) the REAL fetchJson desktop path injected the engine key + JSON content-type.
    expect(req.headers["X-Engine-Key"]).toBe(ENGINE_KEY);
    expect(req.headers["Content-Type"]).toBe("application/json");
    // (4) the body carries BOTH consents + a nonce.
    const payload = JSON.parse(req.body as string) as { acknowledgment: string; nonce: string };
    expect(payload.acknowledgment).toContain("EU AI Act Art. 14"); // Art-14 live-arming ACK
    expect(payload.acknowledgment).toContain("Advance-Approval"); // MiFID-style advance-approval waiver
    expect(payload.nonce).toMatch(/[0-9a-f-]{36}/i); // client-generated replay-distinct nonce (UUID)

    // (5) the restart IPC ran (WORM intent re-read → live boot); reconcile reflected it.
    expect(restartSpy).toHaveBeenCalledTimes(1);
    expect(useStore.getState().paperTrading).toBe(false);
  });

  it("Switch back to Paper: REAL liveDisable issues POST /api/live/disable, then the label settles to Paper", async () => {
    engine = newEngine(false); // booted live
    useStore.setState({ allowLive: true, paperTrading: false });

    render(<Harness />);

    // Clicking the Paper tile is instant (no confirm) → REAL toPaper() → REAL liveDisable.
    fireEvent.click(await screen.findByRole("button", { name: /^paper$/i }));

    await waitFor(() =>
      expect(screen.getByText(/simulated trading, no real capital at risk/i)).toBeTruthy(),
    );

    expect(engine.disableReq).not.toBeNull();
    const req = engine.disableReq as CapturedReq;
    expect(req.method).toBe("POST"); // liveDisable was ALSO GET-broken in #2182
    expect(req.url).toContain(`http://127.0.0.1:${ENGINE_PORT}`);
    expect(req.url).toContain("/api/live/disable");
    expect(req.headers["X-Engine-Key"]).toBe(ENGINE_KEY);
    const payload = JSON.parse(req.body as string) as { acknowledgment: string; nonce: string };
    expect(payload.acknowledgment).toContain("operator switched the active account back to paper");
    expect(payload.nonce).toMatch(/[0-9a-f-]{36}/i);

    expect(restartSpy).toHaveBeenCalledTimes(1);
    expect(useStore.getState().paperTrading).toBe(true);
  });
});
