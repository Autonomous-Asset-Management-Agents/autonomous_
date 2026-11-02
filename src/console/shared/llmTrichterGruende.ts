/**
 * #4658 (Epic #4638): code → text for the LLM-QA funnel. The engine delivers machine codes only
 * (core/engine/llm_trichter.py, core/engine/llm_ranking.py::_pruefe, the execution codes of the
 * order path); readable text is made here. Purely descriptive: no text states a recommendation or
 * an expected outcome. Every value is checked against FORBIDDEN (src/test/wordingGuard.ts).
 * The final wording depends on the legal review #4627.
 */

/** The four stages, in funnel order (n_vorgeschlagen → n_universum → n_round_table → n_iron_dome). */
export const STUFEN_TEXT = {
  vorgeschlagen: "vom LLM vorgeschlagen",
  universum: "im Universum",
  round_table: "nach Round Table",
  iron_dome: "nach Iron Dome",
} as const;

/** #4861: the same stages when the source is the user's own list (no LLM involved). */
export const STUFEN_TEXT_LISTE = {
  ...STUFEN_TEXT,
  vorgeschlagen: "aus der eigenen Liste",
} as const;

/** Stage texts for the engine's `quelle`; anything but `liste` keeps the LLM wording. */
export function stufenText(quelle: string | null | undefined) {
  return quelle === "liste" ? STUFEN_TEXT_LISTE : STUFEN_TEXT;
}

/** Final status per proposal (llm_trichter.py::ENDSTATUS). */
export const ENDSTATUS_TEXT: Record<string, string> = {
  verworfen_vorab: "vorab verworfen",
  noch_nicht_bewertet: "noch nicht bewertet",
  abgelehnt_round_table: "kein Handelssignal",
  abgelehnt_iron_dome: "von Iron Dome gestoppt",
  zugelassen: "zugelassen",
};

/** Reason codes: pre-check, Round Table, execution gate. */
export const GRUND_TEXT: Record<string, string> = {
  // llm_ranking.py::_pruefe — discarded before the Round Table
  formfehler: "kein gültiges Tickerformat",
  ueber_top_k: "über der Listenlänge",
  unbekannt: "Symbol unbekannt",
  nicht_im_universum: "nicht im Universum",
  // llm_trichter.py — Round Table
  keine_round_table_entscheidung: "seit dem Lauf keine Round-Table-Entscheidung",
  signal_hold: "Round Table: HOLD",
  signal_buy: "Round Table: BUY",
  signal_sell: "Round Table: SELL",
  // execution codes of the order path (core/round_table/execution_outcomes.py)
  executed: "ausgeführt",
  resized: "ausgeführt, Größe reduziert",
  closed: "Position geschlossen",
  pending: "Order ausstehend",
  error: "Fehler bei der Ausführung",
  hitl_held: "wartet auf Freigabe",
  max_position_pct: "Positionsgrenze erreicht",
  "blocked:order_value": "Ordervolumen über der Grenze",
  "blocked:daily_limit": "Tageslimit erreicht",
  "blocked:kill_switch": "Kill-Switch aktiv",
  "blocked:risk": "Risiko-Sizing",
  "blocked:churn": "Anti-Churn",
  "blocked:portfolio": "Portfolio-Grenze",
  "blocked:market_closed": "Markt geschlossen",
  "blocked:earnings": "Earnings-Sperre",
  "blocked:earnings_data_missing": "Earnings-Daten fehlen",
  "blocked:precheck": "Vorprüfung nicht bestanden",
  "blocked:reentry_lockout": "Wiedereinstiegssperre",
};

/** Status of the current list (core/engine/llm_ranking_lauf.py: FRISCH … KEINER). */
export const LISTE_STATUS_TEXT: Record<string, string> = {
  frisch: "frisch",
  veraltet_aber_gueltig: "veraltet, noch gültig",
  abgelaufen: "abgelaufen — LSTM-Ranking aktiv",
  keiner: "keine Liste — LSTM-Ranking aktiv",
  // #4861: the user's own list does not expire
  dauerhaft: "gilt bis zur nächsten Änderung",
};

/** #4861: notes on the funnel (core/engine/llm_trichter.py::HINWEIS_LISTE_HEUTIGES_WISSEN). */
export const HINWEIS_TEXT: Record<string, string> = {
  liste_heutiges_wissen: "Die Liste wurde mit dem Wissen von heute zusammengestellt.",
};
export const hinweisText = (code: string): string => HINWEIS_TEXT[code] ?? code;

export const QUELLE_TEXT = {
  llm: "LLM-Vorauswahl",
  lstm: "LSTM-Ranking",
  liste: "Eigene Liste",
} as const;

/** A valid list (frisch / veraltet_aber_gueltig) feeds the ranking; otherwise the LSTM panel does
 *  (zyklus_kontext.py::_rang_quelle_waehlen). #4861: `dauerhaft` = the user's own list. */
export function aktiveQuelle(listeStatus: string): string {
  if (listeStatus === "dauerhaft") return QUELLE_TEXT.liste;
  return listeStatus === "frisch" || listeStatus === "veraltet_aber_gueltig"
    ? QUELLE_TEXT.llm
    : QUELLE_TEXT.lstm;
}

/** Dot tone of the list status: a list in force is "on", a stale-but-valid one "neutral". */
export const statusTone = (s: string): "on" | "off" | "neutral" =>
  s === "frisch" || s === "dauerhaft" ? "on" : s === "veraltet_aber_gueltig" ? "neutral" : "off";

/** Unknown codes (e.g. a free-text gatekeeper reason) are shown verbatim, never invented. */
export const grundText = (code: string): string => GRUND_TEXT[code] ?? code;
export const endstatusText = (code: string): string => ENDSTATUS_TEXT[code] ?? code;
export const listeStatusText = (code: string): string => LISTE_STATUS_TEXT[code] ?? code;

/** Age of the list in the coarsest sensible unit. */
export function alterText(sekunden: number | null): string | null {
  if (sekunden == null || !Number.isFinite(sekunden) || sekunden < 0) return null;
  if (sekunden < 60) return "< 1 min";
  if (sekunden < 3600) return `${Math.floor(sekunden / 60)} min`;
  if (sekunden < 86400) return `${Math.floor(sekunden / 3600)} h`;
  return `${Math.floor(sekunden / 86400)} d`;
}
