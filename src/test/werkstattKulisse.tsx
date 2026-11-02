import { render, screen, within } from "@testing-library/react";
import { vi } from "vitest";
import { Settings } from "@/console/desktop/pages/Settings";
import { useStore } from "@/console/store/useStore";
import type { NamedStrategy } from "@/lib/api";
import { BRIDGE_KEYS, initDesktopBridge, type FleetEngine, type PaperSlot } from "@/lib/desktopBridge";
import { payload } from "./advancedTradingFixtures";

/**
 * STW-10 (#4636) — the stage of the workshop's operating test. Renders the real Settings page and
 * replaces only its outer edges, the same ones as `e2e/consoleHarness.ts`: the engine's HTTP
 * answers (a `fetch` stub with a small in-memory strategy store) and the desktop bridge
 * `window.aaagents`. Not a test file.
 */

export type Antwort = { status?: number; body?: unknown };
type Route = (anfrage: { url: URL; body: Record<string, unknown> }) => Antwort | Promise<Antwort>;
type Routen = Record<string, Route>;

export const haengt: Route = () => new Promise<Antwort>(() => {});
export const scheitert = (status: number, body?: unknown): Route => () => ({ status, body });

export const SLOTS_LEER: PaperSlot[] = ["paper1", "paper2", "paper3"].map((slot) => ({
  slot: slot as PaperSlot["slot"],
  connected: false,
  accountMasked: null,
  engineStatus: null,
}));

export interface Szene {
  strategien?: NamedStrategy[];
  zuweisungen?: { konto: string; strategie: string }[];
  hitl?: boolean;
  live?: boolean;
  slots?: PaperSlot[];
  fleet?: FleetEngine[] | null;
  aktuell?: Record<string, string>;
  routen?: Routen;
  shell?: Record<string, unknown>;
}

const SLOT_MEMBERS = ["listPaperSlots", "connectPaperSlot", "disconnectPaperSlot", "startPaperSlotEngine"];

function engineRouten(s: Szene, strategien: NamedStrategy[]): Routen {
  return {
    "GET /api/strategies": () => ({ body: { strategien, max_strategien: 3 } }),
    "POST /api/strategies": ({ body }) => {
      const neu = { name: body.name as string, werte: body.werte as Record<string, string> };
      strategien.push(neu);
      return { body: { success: true, aktion: "create", ...neu, detail: "strategy recorded on the WORM chain" } };
    },
    "DELETE /api/strategies/*": ({ url }) => {
      const name = decodeURIComponent(url.pathname.split("/").pop()!);
      strategien.splice(strategien.findIndex((x) => x.name === name), 1);
      return { body: { success: true, aktion: "delete", name, werte: {}, detail: "deleted" } };
    },
    "GET /api/strategies/*/versions": () => ({ body: { name: "x", versionen: [] } }),
    "POST /api/strategies/*/assign-live": ({ url }) => {
      const name = decodeURIComponent(url.pathname.split("/")[3]);
      const werte = strategien.find((x) => x.name === name)?.werte ?? {};
      const changes = Object.entries(werte).map(([key, to]) => ({ key, from: "", to }));
      return { body: { success: true, strategie: name, applied: werte, changes, detail: "recorded" } };
    },
    "GET /api/strategy-assignments": () => ({
      body: { zuweisungen: (s.zuweisungen ?? []).map((z) => ({ ...z, strategie_hash: "sha256:x", timestamp: "2026-10-30T10:00:00+00:00" })) },
    }),
    "GET /api/trading-settings": () => ({ body: payload(s.aktuell) }),
    "GET /api/hitl/policy": () => ({ body: { HITL_ENABLED: s.hitl ?? true } }),
    "GET /health": () => ({ body: { status: "healthy", paper_trading: !s.live } }),
    "POST /api/strategy-sim/runs": () => ({ body: { run_id: "0123456789abcdef", status: "laeuft" } }),
  };
}

function findeRoute(routen: Routen, schluessel: string): Route | undefined {
  if (routen[schluessel]) return routen[schluessel];
  const treffer = Object.keys(routen).find(
    (k) => k.includes("*") && new RegExp(`^${k.replace(/\*/g, "[^/]+")}$`).test(schluessel),
  );
  return treffer ? routen[treffer] : undefined;
}

function shellVon(s: Szene, slots: PaperSlot[], protokoll: string[]) {
  const fehlend = SLOT_MEMBERS.filter((k) => !BRIDGE_KEYS.includes(k));
  if (fehlend.length > 0) throw new Error(`paper-slot bridge — STW-6b: ${fehlend.join(", ")}`);
  const leer = Object.fromEntries(BRIDGE_KEYS.map((k) => [k, vi.fn().mockResolvedValue(undefined)]));
  const setze = (slot: string, teil: Partial<PaperSlot>) => {
    slots.splice(slots.findIndex((x) => x.slot === slot), 1, { ...slots.find((x) => x.slot === slot)!, ...teil });
  };
  return {
    ...leer,
    isDesktop: true,
    platform: "win32",
    getEngineConnection: vi.fn().mockResolvedValue({ port: 8001, apiKey: "k-test" }),
    getEngineStatus: vi.fn().mockResolvedValue({ status: "running" }),
    getEngineLogs: vi.fn().mockResolvedValue([]),
    onEngineStatus: vi.fn(() => () => {}),
    onEngineLog: vi.fn(() => () => {}),
    onOllamaProgress: vi.fn(() => () => {}),
    getSetupState: vi.fn().mockResolvedValue({}),
    getKeychainStatus: vi.fn().mockResolvedValue({}),
    getEngineFleet: vi.fn().mockResolvedValue(s.fleet ?? null),
    listPaperSlots: vi.fn(async () => slots.map((x) => ({ ...x }))),
    connectPaperSlot: vi.fn(async (slot: string) => {
      setze(slot, { connected: true, accountMasked: "••••4321", engineStatus: "running" });
      return { ok: true, accountMasked: "••••4321" };
    }),
    disconnectPaperSlot: vi.fn(async (slot: string) => {
      setze(slot, { connected: false, accountMasked: null, engineStatus: null });
      return { ok: true };
    }),
    startPaperSlotEngine: vi.fn(async (slot: string) => {
      setze(slot, { engineStatus: "running" });
      return { ok: true };
    }),
    saveSetupState: vi.fn(async () => { protokoll.push("persist"); }),
    restartEngine: vi.fn(async () => { protokoll.push("restart"); }),
    ...s.shell,
  };
}

export async function werkstatt(szene: Szene = {}) {
  const strategien = [...(szene.strategien ?? [])];
  const slots = [...(szene.slots ?? SLOTS_LEER)];
  const protokoll: string[] = [];
  const routen = { ...engineRouten(szene, strategien), ...szene.routen };
  const anfragen: { schluessel: string; body: Record<string, unknown> }[] = [];

  vi.stubGlobal("fetch", async (ziel: string, init?: RequestInit) => {
    const url = new URL(ziel, "http://127.0.0.1:8001");
    const schluessel = `${init?.method ?? "GET"} ${url.pathname}`;
    const body = init?.body ? (JSON.parse(init.body as string) as Record<string, unknown>) : {};
    anfragen.push({ schluessel, body });
    if (schluessel.includes("assign-live")) protokoll.push("record");
    const route = findeRoute(routen, schluessel);
    const antwort = route ? await route({ url, body }) : { status: 404, body: { detail: "not stubbed" } };
    return new Response(JSON.stringify(antwort.body ?? {}), { status: antwort.status ?? 200 });
  });

  const gesprungenZu: string[] = [];
  Element.prototype.scrollIntoView = function (this: Element) { gesprungenZu.push(this.id); };

  const shell = shellVon(szene, slots, protokoll);
  (window as unknown as { aaagents: unknown }).aaagents = shell;
  await initDesktopBridge();
  useStore.setState({ paperTrading: !szene.live });

  render(<Settings />);
  return { shell, anfragen, protokoll, gesprungenZu };
}

export function raeumeAuf() {
  delete (window as unknown as { aaagents?: unknown }).aaagents;
  vi.unstubAllGlobals();
  window.location.hash = "";
}

export type Bereich = ReturnType<typeof within<typeof screen.queries>>;

/** The card under its eyebrow title, as a query scope: Settings holds several "Retry", "Loading…". */
export async function karte(titel: string): Promise<Bereich> {
  const kopf = await screen.findByText(titel, { selector: ".eyebrow" });
  return within(kopf.closest(".surface") as HTMLElement);
}
