// XAI-1 / XAI-T9 command-surface (#1338): a deterministic, LLM-free interpreter that turns a
// typed navigation command in the console chat into a page switch. Exact-match by design —
// the input must START with an imperative navigation verb and, once the verb and filler words
// are stripped, EQUAL a known page alias. Anything else returns null so a real question
// ("how many positions do I have?") is never hijacked and falls through to the engine chat.
import type { ConsolePage } from "@/console/store/useStore";

export interface ConsoleCommand {
  kind: "navigate";
  page: ConsolePage;
}

// Human labels for the page keys (mirrors the sidebar) — used for the chat confirmation line.
export const PAGE_LABELS: Record<ConsolePage, string> = {
  overview: "Overview",
  decisions: "Decisions",
  positions: "Positions",
  reports: "Reports",
  audit: "Audit chain",
  settings: "Settings",
  chat: "Chat",
  simulation: "Simulation",
};

// Build-time flag (mirrors VITE_ENABLE_FIREBASE): the command-surface ships dormant and is
// enabled by building the desktop console with VITE_XAI_CONSOLE_EMBED=true.
export function isCommandSurfaceEnabled(): boolean {
  return (import.meta.env.VITE_XAI_CONSOLE_EMBED as string | undefined) === "true";
}

// Cleaned page aliases (EN/DE synonyms) -> ConsolePage.
const PAGE_ALIASES: Record<string, ConsolePage> = {
  dashboard: "overview",
  overview: "overview",
  übersicht: "overview",
  uebersicht: "overview",
  portfolio: "overview",
  decisions: "decisions",
  entscheidungen: "decisions",
  queue: "decisions",
  positions: "positions",
  positionen: "positions",
  reports: "reports",
  report: "reports",
  berichte: "reports",
  bericht: "reports",
  audit: "audit",
  "audit chain": "audit",
  "audit-chain": "audit",
  auditkette: "audit",
  settings: "settings",
  einstellungen: "settings",
  chat: "chat",
};

// An imperative navigation verb MUST lead the input, so a question never matches. Longest
// multi-word verbs first so e.g. "go to" wins over a bare "go".
const NAV_VERB =
  /^(navigate to|take me to|go to|goto|open|show|öffne|zeige|zeig|wechsle zu|wechsle|gehe zu|geh zu|navigiere zu|navigiere)\b/;
const LEADING_FILLER = /^(the|to|my|das|die|der|den|dem|zur|zum|me|mir|mal|a|an)\b/;
const TRAILING_FILLER = /\b(page|view|tab|screen|section|seite|bereich|ansicht)$/;

export function interpretConsoleCommand(text: string): ConsoleCommand | null {
  let t = (text ?? "").trim().toLowerCase();
  if (!t) return null;

  // 1) must start with an imperative navigation verb
  if (!NAV_VERB.test(t)) return null;
  t = t.replace(NAV_VERB, "").trim();

  // 2) strip trailing punctuation, then leading/trailing filler words (repeatedly)
  t = t.replace(/[.?!,]+$/g, "").trim();
  let prev: string;
  do {
    prev = t;
    t = t.replace(LEADING_FILLER, "").trim();
    t = t.replace(TRAILING_FILLER, "").trim();
  } while (t !== prev);

  // 3) exact-match the cleaned remainder against a known page alias
  const page = PAGE_ALIASES[t];
  return page ? { kind: "navigate", page } : null;
}
