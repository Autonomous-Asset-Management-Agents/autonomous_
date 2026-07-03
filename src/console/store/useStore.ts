import { create } from "zustand";
import type { ConsolePosition, PortfolioView } from "@/console/live/portfolio";
import type { ConsoleRoundTableDecision } from "@/console/live/roundTable";
import type { EquityCurvePoint, EquityView } from "@/console/live/equity";
import type { AuditEvent } from "@/console/live/audit";
import type { SpecialistReport } from "@/console/types";

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

interface ConsoleState {
  desktopPage: ConsolePage;
  setDesktopPage: (page: ConsolePage) => void;

  chatMessages: ChatMessage[];
  addChatMessage: (role: ChatMessage["role"], text: string) => void;

  // Live portfolio (G3 data slice) — populated by polling /portfolio-summary
  // through the desktop-aware api layer; empty until the first successful poll.
  positions: ConsolePosition[];
  cashEUR: number | null;
  currentEquity: number | null;
  setPortfolio: (view: PortfolioView) => void;

  // Timestamp (ms) of the last successful portfolio sync — drives the Overview
  // "Last sync" line; null until the first successful poll.
  lastSyncAt: number | null;
  markSynced: () => void;

  // Latest Round-Table decision per symbol (G3c) — polled from
  // /round-table-decisions; empty until the first successful poll.
  roundTable: ConsoleRoundTableDecision[];
  setRoundTable: (decisions: ConsoleRoundTableDecision[]) => void;

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
  setEquity: (view: EquityView) => void;

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
  // Kill-switch state from /health (#1642) — drives the Overview status bar.
  systemHalted: boolean | null;
  setSystemHalted: (halted: boolean | null) => void;

  // Market-open state + broker label from /health/deep (DASH-1 T5 #1473). Both
  // null until the first successful poll — the pills render an honest "—" /
  // nothing rather than a fabricated value.
  marketOpen: boolean | null;
  brokerLabel: string | null;
  setMarketHealth: (marketOpen: boolean | null, brokerLabel: string | null) => void;
}

export const useStore = create<ConsoleState>((set) => ({
  desktopPage: "overview",
  setDesktopPage: (page) => set({ desktopPage: page }),

  chatMessages: [],
  addChatMessage: (role, text) =>
    set((state) => ({
      chatMessages: [
        ...state.chatMessages,
        { id: `${state.chatMessages.length}-${role}-${text.length}`, role, text },
      ],
    })),

  positions: [],
  cashEUR: null,
  currentEquity: null,
  setPortfolio: (view) =>
    set({ positions: view.positions, cashEUR: view.cashEUR, currentEquity: view.currentEquity }),

  lastSyncAt: null,
  markSynced: () => set({ lastSyncAt: Date.now() }),

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
  setEquity: (view) =>
    set({ equityCurve: view.equityCurve, benchmarkCurve: view.benchmarkCurve, lastEquity: view.lastEquity }),

  audit: [],
  setAudit: (events) => set({ audit: events }),

  brokerName: null,
  accountTag: null,

  strategyRunning: null,
  setStrategyRunning: (running) => set({ strategyRunning: running }),
  systemHalted: null,
  setSystemHalted: (halted) => set({ systemHalted: halted }),

  marketOpen: null,
  brokerLabel: null,
  setMarketHealth: (marketOpen, brokerLabel) => set({ marketOpen, brokerLabel }),
}));

/**
 * Boot-ready signal: true once the engine has returned a real portfolio (live
 * equity is set). Used by the BootSplash to reveal the dashboard only after real
 * data has arrived, so the empty store never flashes.
 */
export const selectDataReady = (s: ConsoleState): boolean => s.currentEquity != null;
