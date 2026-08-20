import { create } from "zustand";
import type { ConsolePosition, PortfolioView } from "@/console/live/portfolio";
import type { ConsoleRoundTableDecision } from "@/console/live/roundTable";
import type { EquityCurvePoint, EquityView } from "@/console/live/equity";
import type { AuditEvent } from "@/console/live/audit";
import type { ConsoleActivity } from "@/console/live/activities";
import type { ConsoleOpenOrder } from "@/console/live/openOrders";
import type { ConsolePendingApproval } from "@/console/live/hitlQueue";
import type { SpecialistReport } from "@/console/types";
// Runtime import (equityCache only imports TYPES from ./equity, so no cycle): a Paper↔Live switch
// must drop the PERSISTED equity cache too, else the old account's curve repaints on the next mount.
import { clearEquityCaches } from "@/console/live/equityCache";

/**
 * Console store (G3, #1050). Trimmed port of the desktop bundle's store —
 * grows as data-driven pages land. For this shell slice it carries the page
 * navigation state, the chat transcript (so history survives page switches),
 * and the optional broker tag shown in the title bar.
 */

export type ConsolePage =
  | "overview"
  | "decisions"
  | "positions"
  | "activities"
  | "reports"
  | "audit"
  | "settings"
  | "chat"
  | "simulation";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
}

/**
 * Deliberate Paper↔Live switch in progress (operator finding #8). Set at the very
 * start of LiveTradingSwitchCard.toLive()/toPaper() (before the engine restart) and
 * cleared once the switch resolves. While set, App.tsx renders the calm
 * ModeSwitchOverlay (autonomous boot-splash heartbeat → wordmark) and SUPPRESSES the
 * /offline routing — the restart is expected, not a crash. `to` selects the copy.
 */
// `startedAt` (epoch-ms, set by the switch initiator) marks when the Paper↔Live switch began, so the
// ModeSwitchOverlay can hold its pulse until the TARGET account's portfolio has been re-synced SINCE
// then (lastSyncAt > startedAt) — otherwise the reveal would flash the OLD account's equity/positions.
export type ModeSwitchTarget = { to: "live" | "paper"; startedAt?: number };

// Persist the switch flag across a renderer reload (see below). sessionStorage —
// not localStorage — so it lives only for the current window session and can never
// resurrect a stale "switching…" overlay on a fresh app launch.
const MODE_SWITCH_KEY = "aaa.modeSwitch";

function readPersistedModeSwitch(): ModeSwitchTarget | null {
  // Renderer-reload edge: if the engine restart also tears down the renderer
  // (Electron render_process_gone → reload), the in-memory store is lost. Reading
  // the persisted flag on store creation lets the overlay survive that reload and
  // keep beating until the engine serves again (or the overlay's safety timeout
  // fires). Guarded + try/catch: harmless when sessionStorage is unavailable.
  try {
    const raw = typeof sessionStorage !== "undefined" ? sessionStorage.getItem(MODE_SWITCH_KEY) : null;
    if (!raw) return null;
    // Legacy format was the bare target string ("live"/"paper"); current format is JSON with startedAt.
    if (raw === "live" || raw === "paper") return { to: raw };
    const parsed = JSON.parse(raw) as ModeSwitchTarget;
    if (parsed && (parsed.to === "live" || parsed.to === "paper")) {
      return typeof parsed.startedAt === "number" ? { to: parsed.to, startedAt: parsed.startedAt } : { to: parsed.to };
    }
  } catch {
    /* storage unavailable / unparseable — non-fatal, just start with no switch in progress */
  }
  return null;
}

function persistModeSwitch(value: ModeSwitchTarget | null): void {
  try {
    if (typeof sessionStorage === "undefined") return;
    if (value) sessionStorage.setItem(MODE_SWITCH_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(MODE_SWITCH_KEY);
  } catch {
    /* storage unavailable — non-fatal */
  }
}

interface ConsoleState {
  desktopPage: ConsolePage;
  setDesktopPage: (page: ConsolePage) => void;
  /** Deep-link target (#2732 Strand B): a symbol to open expanded in Reports.
   *  Set by clicking a ticker anywhere (SymbolLink); consumed + cleared by the
   *  Reports page. null = no pending target. */
  reportSymbol: string | null;
  setReportSymbol: (sym: string | null) => void;

  chatMessages: ChatMessage[];
  addChatMessage: (role: ChatMessage["role"], text: string) => void;

  // Deliberate Paper↔Live switch in progress (#8) — null when idle. Drives the
  // calm full-screen ModeSwitchOverlay and the /offline suppression in App.tsx.
  modeSwitch: ModeSwitchTarget | null;
  setModeSwitch: (value: ModeSwitchTarget | null) => void;

  // Live portfolio (G3 data slice) — populated by polling /portfolio-summary
  // through the desktop-aware api layer; empty until the first successful poll.
  positions: ConsolePosition[];
  cashEUR: number | null;
  currentEquity: number | null;
  accountCurrency: string;
  setPortfolio: (view: PortfolioView) => void;
  // Clear the account snapshot at the START of a Paper↔Live switch so the OLD account's
  // equity/positions/cash cannot linger on screen after the switch. Unlike resetEquityCurves (which
  // deliberately keeps currentEquity), this nulls the live numbers too — the ModeSwitchOverlay keeps
  // its pulse up until the target account re-syncs, so the operator never sees the null flash. It also
  // clears lastSyncAt so no PRE-switch sync counts as "fresh" — the pulse holds until a genuine
  // post-switch /portfolio-summary poll lands (lastSyncAt > startedAt).
  resetAccountSnapshot: () => void;

  // Broker fill history (DESK-1) — polled from /activities (the FULL paginated
  // filled-order history, newest first); empty until the first successful poll.
  // `activitiesTruncated` is true when the backend hit its page-count safety cap.
  activities: ConsoleActivity[];
  activitiesTruncated: boolean;
  setActivities: (activities: ConsoleActivity[], truncated?: boolean) => void;

  // #2137: the operator's currently OPEN / working orders — polled from /open-orders on a fast
  // cadence (they churn as orders fill/cancel). Empty until the first successful poll.
  openOrders: ConsoleOpenOrder[];
  setOpenOrders: (orders: ConsoleOpenOrder[]) => void;

  // #2660 (Epic #2667): orders the HITL gate HELD for human approval — polled from
  // /api/hitl/pending on the same fast cadence (they expire after HITL_EXPIRY_SECONDS).
  pendingApprovals: ConsolePendingApproval[];
  setPendingApprovals: (approvals: ConsolePendingApproval[]) => void;

  // Timestamp (ms) of the last successful portfolio sync — drives the Overview
  // "Last sync" line; null until the first successful poll.
  lastSyncAt: number | null;
  // Bug B (0.3.5): the `paper_trading` flag of the LAST synced /portfolio-summary — i.e. which
  // ACCOUNT the currently-shown equity/positions belong to (true=paper, false=live, null=unknown).
  // The ModeSwitchOverlay reveal gate matches this against the switch target so an in-flight poll
  // that resolves against the OLD account (e.g. a dying live engine during a →paper switch) can no
  // longer satisfy the freshness gate with stale data.
  syncedPaper: boolean | null;
  markSynced: (paper?: boolean | null) => void;

  // Latest Round-Table decision per symbol (G3c) — polled from
  // /round-table-decisions; empty until the first successful poll.
  roundTable: ConsoleRoundTableDecision[];
  setRoundTable: (decisions: ConsoleRoundTableDecision[]) => void;

  // #2822: clear every account-scoped FEED slice at Paper↔Live switch start — openOrders,
  // activities(+truncated), pendingApprovals, roundTable. These sat outside the #2465/#Bug-B
  // freshness machinery (which covers the snapshot + equity curves), so the OLD account's
  // orders/decisions survived the reveal. Called next to resetEquityCurves/resetAccountSnapshot
  // in LiveConsentModal; the pollers additionally drop writes while modeSwitch != null.
  resetAccountFeeds: () => void;

  // Per-symbol specialist-report cards (G1b′) — polled from /specialist-reports;
  // empty until the first successful poll. `specialistStatus` mirrors the
  // endpoint's status ("ok" | "unavailable" | "error" | …) so the Reports page
  // can render an honest empty/unavailable message; `specialistMessage` carries
  // the engine's explanation (e.g. "registry not running on this deployment").
  specialistReports: SpecialistReport[];
  specialistStatus: string;
  specialistMessage: string | null;
  setSpecialistReports: (
    reports: SpecialistReport[],
    status: string,
    message?: string | null,
  ) => void;

  // Live equity curves (G3b) — polled from /benchmark-equity. `lastEquity` is
  // the prior close, so Overview can show today's P/L vs `currentEquity`.
  equityCurve: EquityCurvePoint[];
  benchmarkCurve: EquityCurvePoint[];
  lastEquity: number | null;
  // #1957: engine-computed deposit-neutral KPIs (null → the frontend falls back to the legacy
  // (now-start)/start calc). twrPct is the headline "since inception" return.
  twrPct: number | null;
  netDeposits: number | null;
  pnlNetOfDeposits: number | null;
  setEquity: (view: EquityView) => void;
  // #2465: clear the accumulated equity curves on a Paper↔Live switch so the stale account's
  // "since inception" curve (e.g. the €152k paper curve) never bleeds into the freshly-switched
  // account. currentEquity (the live number) is left intact; the Overview "Live history is building"
  // banner then shows (paperTrading===false && currentEquity!=null && equityCurve.length===0).
  resetEquityCurves: () => void;

  // Hash-linked audit log (G3d-2) — polled from the local audit_log file via the
  // desktop bridge (newest-first); empty in the cloud build and until the first
  // poll.
  audit: AuditEvent[];
  setAudit: (events: AuditEvent[]) => void;

  // Title-bar account tag — populated by live data once a real broker connects;
  // null until then (the title bar shows nothing rather than a placeholder).
  brokerName: string | null;
  accountTag: string | null;

  // Engine trading-loop state for the sidebar "Live Trading" indicator — polled
  // from /health (strategy_running). null until the first successful poll.
  strategyRunning: boolean | null;
  setStrategyRunning: (running: boolean | null) => void;

  // #2190: /health `paper_trading` — false = live, true = paper, null until first poll.
  // Drives the Overview order panel (order book in live, order history in paper).
  paperTrading: boolean | null;
  setPaperTrading: (paper: boolean | null) => void;
  // Kill-switch state from /health (#1642) — drives the Overview status bar.
  systemHalted: boolean | null;
  setSystemHalted: (halted: boolean | null) => void;

  // LSR R4 (#2251): true only when the last background /health showed a SERVING engine (reachable
  // AND status !== "starting"). LiveTradingSwitchCard watches the false→true edge to recover the
  // account tile from a transient reconcile give-up, so a slow restart never leaves it stuck on
  // "engine isn't responding" while the engine is in fact trading. Defaults false (not yet confirmed).
  engineServing: boolean;
  setEngineServing: (serving: boolean) => void;

  // Market-open state + broker label from /health/deep (DASH-1 T5 #1473). Both
  // null until the first successful poll — the pills render an honest "—" /
  // nothing rather than a fabricated value.
  marketOpen: boolean | null;
  brokerLabel: string | null;
  // #2743/S3: a broker/model component is faulted (from /health/deep). With `strategyRunning` this
  // lets the sidebar show an honest "Degraded" instead of a misleading "running". null = unknown.
  componentsDegraded: boolean | null;
  setMarketHealth: (
    marketOpen: boolean | null,
    brokerLabel: string | null,
    componentsDegraded?: boolean | null,
  ) => void;

  // Resolved entitlement tier (GTM-1 #1915) — polled from /api/entitlement/status.
  // `tier` is the token string ("BASIC" | "PRO" | …); `canUpgrade` is true only
  // for Junior (BASIC) desktops and drives the sidebar Upgrade CTA. Both null
  // until the first successful poll (cloud/browser build → stays null → no CTA).
  tier: string | null;
  canUpgrade: boolean | null;
  // Whether the resolved tier permits live trading (allow_live from the engine). false
  // = paper-only (Junior/BASIC) → gates the LiveTradingSwitchCard. null until first poll.
  allowLive: boolean | null;
  // GTM/SIM — whether the Simulation nav item + route are exposed, from the engine's
  // resolved entitlement (core/entitlement/tier.py). null until the first poll; the
  // nav treats anything but strict `true` as HIDDEN (fail-closed while it's disabled).
  simulationEnabled: boolean | null;
  setEntitlement: (
    tier: string | null,
    canUpgrade: boolean | null,
    simulationEnabled: boolean | null,
    allowLive: boolean | null,
  ) => void;
  sidebarStatusMsg: string | null;
  setSidebarStatusMsg: (msg: string | null) => void;
  // #2441 INC-1: one-shot signal to launch TODAY'S go-live consent flow
  // (LiveTradingSwitchCard's WORM/ACK confirm) from the "Start Live Trading"
  // button — set true by the button, consumed+cleared by the card. Never
  // arms live itself; only opens the existing confirm.
  requestLiveConsent: boolean;
  setRequestLiveConsent: (v: boolean) => void;
  // #2454: the live account equity captured at live-key validation — the go-live consent
  // prefills the per-trade HITL limit at 10% of it. null until a live key validates.
  liveEquity: number | null;
  setLiveEquity: (v: number | null) => void;
  // #2465: one-shot signal to switch the active account back to Paper from the (now global)
  // LiveConsentModal — set by the Settings "Paper" tile, consumed+cleared by the modal.
  requestSwitchToPaper: boolean;
  setRequestSwitchToPaper: (v: boolean) => void;
  // #2465: the Paper↔Live reconcile display state, hoisted to the store so the Settings card's
  // state line still shows "confirming…/unconfirmed" while the switch logic runs in the global
  // LiveConsentModal. Set by the modal's reconcilePaper; read by LiveTradingSwitchCard.
  switchReconciling: boolean;
  setSwitchReconciling: (v: boolean) => void;
  switchConfirmFailed: boolean;
  setSwitchConfirmFailed: (v: boolean) => void;
  // #2465: the last switch error, in the store so a toPaper failure (run in the global
  // LiveConsentModal) surfaces in the Settings card's error block too.
  switchError: string | null;
  setSwitchError: (v: string | null) => void;
}

export const useStore = create<ConsoleState>((set) => ({
  desktopPage: "overview",
  setDesktopPage: (page) => set({ desktopPage: page }),
  reportSymbol: null,
  setReportSymbol: (sym) => set({ reportSymbol: sym }),

  chatMessages: [],
  addChatMessage: (role, text) =>
    set((state) => ({
      chatMessages: [
        ...state.chatMessages,
        { id: `${state.chatMessages.length}-${role}-${text.length}`, role, text },
      ],
    })),

  modeSwitch: readPersistedModeSwitch(),
  setModeSwitch: (value) => {
    persistModeSwitch(value);
    set({ modeSwitch: value });
  },

  positions: [],
  cashEUR: null,
  currentEquity: null,
  accountCurrency: "USD",
  setPortfolio: (view) =>
    set({
      positions: view.positions,
      cashEUR: view.cashEUR,
      currentEquity: view.currentEquity,
      accountCurrency: view.accountCurrency,
    }),
  resetAccountSnapshot: () => set({ positions: [], cashEUR: null, currentEquity: null, lastSyncAt: null, syncedPaper: null }),
  // #2822: the account-scoped feeds (see the interface note). Empty is honest post-switch —
  // wrong-account is not; the target account's polls refill them right after the reveal.
  resetAccountFeeds: () =>
    set({ openOrders: [], activities: [], activitiesTruncated: false, pendingApprovals: [], roundTable: [] }),

  activities: [],
  activitiesTruncated: false,
  setActivities: (activities, truncated = false) =>
    set({ activities, activitiesTruncated: truncated }),

  openOrders: [],
  setOpenOrders: (orders) => set({ openOrders: orders }),
  pendingApprovals: [],
  setPendingApprovals: (approvals) => set({ pendingApprovals: approvals }),

  lastSyncAt: null,
  syncedPaper: null,
  // Bug B (0.3.5): a poll with a boolean tag updates syncedPaper; an UNTAGGED poll (null — the
  // /portfolio-summary error path, or a pre-0.3.5 engine) only bumps lastSyncAt and must NOT clobber
  // a previously-synced target tag (that would re-block the reveal for up to one poll cycle, or wedge
  // it to the 90s timeout if every poll is untagged). resetAccountSnapshot still forces null at the
  // START of a switch, so a stale pre-switch tag can never survive into the new account's window.
  markSynced: (paper = null) =>
    set((s) => ({ lastSyncAt: Date.now(), syncedPaper: paper ?? s.syncedPaper })),

  roundTable: [],
  setRoundTable: (decisions) => set({ roundTable: decisions }),

  specialistReports: [],
  specialistStatus: "",
  specialistMessage: null,
  setSpecialistReports: (reports, status, message) =>
    set({ specialistReports: reports, specialistStatus: status, specialistMessage: message ?? null }),

  equityCurve: [],
  benchmarkCurve: [],
  lastEquity: null,
  twrPct: null,
  netDeposits: null,
  pnlNetOfDeposits: null,
  setEquity: (view) =>
    set({
      equityCurve: view.equityCurve,
      benchmarkCurve: view.benchmarkCurve,
      lastEquity: view.lastEquity,
      twrPct: view.twrPct,
      netDeposits: view.netDeposits,
      pnlNetOfDeposits: view.pnlNetOfDeposits,
    }),
  resetEquityCurves: () => {
    // Drop the persisted localStorage cache FIRST (see equityCache.clearEquityCaches): clearing only
    // the in-memory curves let the stale paper ~100K baseline repaint on the next Overview mount.
    clearEquityCaches();
    set({
      equityCurve: [],
      benchmarkCurve: [],
      lastEquity: null,
      twrPct: null,
      netDeposits: null,
      pnlNetOfDeposits: null,
    });
  },

  audit: [],
  setAudit: (events) => set({ audit: events }),

  brokerName: null,
  accountTag: null,

  strategyRunning: null,
  setStrategyRunning: (running) => set({ strategyRunning: running }),

  paperTrading: null,
  setPaperTrading: (paper) => set({ paperTrading: paper }),
  systemHalted: null,
  setSystemHalted: (halted) => set({ systemHalted: halted }),

  engineServing: false,
  setEngineServing: (serving) => set({ engineServing: serving }),

  marketOpen: null,
  brokerLabel: null,
  componentsDegraded: null,
  setMarketHealth: (marketOpen, brokerLabel, componentsDegraded = null) =>
    set({ marketOpen, brokerLabel, componentsDegraded }),

  tier: (import.meta.env.DEV && import.meta.env.MODE !== "test") ? "BASIC" : null,
  canUpgrade: (import.meta.env.DEV && import.meta.env.MODE !== "test") ? true : null,
  allowLive: (import.meta.env.DEV && import.meta.env.MODE !== "test") ? false : null,
  simulationEnabled: null,
  setEntitlement: (tier, canUpgrade, simulationEnabled, allowLive) =>
    set({ tier, canUpgrade, simulationEnabled, allowLive }),
    
  sidebarStatusMsg: null,
  setSidebarStatusMsg: (msg) => set({ sidebarStatusMsg: msg }),

  requestLiveConsent: false,
  setRequestLiveConsent: (v) => set({ requestLiveConsent: v }),

  liveEquity: null,
  setLiveEquity: (v) => set({ liveEquity: v }),
  requestSwitchToPaper: false,
  setRequestSwitchToPaper: (v) => set({ requestSwitchToPaper: v }),
  switchReconciling: false,
  setSwitchReconciling: (v) => set({ switchReconciling: v }),
  switchConfirmFailed: false,
  setSwitchConfirmFailed: (v) => set({ switchConfirmFailed: v }),
  switchError: null,
  setSwitchError: (v) => set({ switchError: v }),
}));

/**
 * Boot-ready signal: true once the engine has returned a real portfolio (live
 * equity is set). Used by the BootSplash to reveal the dashboard only after real
 * data has arrived, so the empty store never flashes.
 */
export const selectDataReady = (s: ConsoleState): boolean => s.currentEquity != null;
