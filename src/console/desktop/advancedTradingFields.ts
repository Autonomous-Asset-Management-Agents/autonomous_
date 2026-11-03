/**
 * UXC-1 S5 (#3158, Epic #3151) — data-driven field table of the "Advanced" section.
 *
 * CLIENT MIRROR ONLY: bounds and defaults here are convenience for slider ends and
 * badges; the AUTHORITATIVE clamps live server-side in `core/trading_settings.py`
 * (S2) and every apply goes through the WORM-audited flow (S3). The neutrality guard
 * and the rendering (AdvancedFieldRow.tsx, in Advanced AND the strategy editor #4873)
 * share THIS one source, so a wording change cannot bypass the guard.
 *
 * Deliberately NOT here (epic non-scope): TRAILING_STOP_PCT (never wired on the
 * live round-table path, B9), HARD_STOP_LOSS_PCT
 * as an INPUT (A6 — rendered as a read-only guardrail row instead), any DrawdownGuard
 * veto/severe knobs (agent toggles live on the Agents card, S4), and
 * SMART_EXIT_EXIT_RANK_HYSTERESIS (hardcoded in config.py:473 — offering it would be
 * silent misoperation until an S1 seam exists).
 *
 * #3220/#3221 (owner decision 05.09.2026): the two compliance OPERATING limits —
 * COMPLIANCE_MAX_ORDER_VALUE and COMPLIANCE_MAX_DAILY_TRADES — moved IN here. The
 * guardrail itself is untouched: a cap still exists and the ceilings ratified in
 * #1599 (100,000 and 50) still bind, server-side. Same shape as HITL, where five of
 * six values have long been operator-settable while HITL_ENABLED stays boot-enforced.
 */

import type { FieldUnit } from "@/console/desktop/fieldUnits";

export type AdvancedFieldKind = "toggle" | "slider" | "select" | "text";

export interface AdvancedField {
  key: string;
  label: string;
  /** Factual mechanics only — no advice, no profiles (#2866). */
  desc: string;
  kind: AdvancedFieldKind;
  min?: number;
  max?: number;
  step?: number;
  /** #4999: the unit its value reads and is typed in (fieldUnits.ts). Scores, factors and
   *  plain counts have none. min, max and step stay in the stored value. */
  unit?: FieldUnit;
  choices?: string[];
  /** Key of a toggle this field is inert without. When that toggle is OFF the
   *  field is HIDDEN (not just disabled) — a dependent knob shown as active while
   *  its feature is off reads as operable when it does nothing. Visibility follows
   *  the PENDING toggle value, so switching the parent hides the children at once. */
  dependsOn?: string;
  /** #4660: shown only while `key` holds one of `values` (pending value, like dependsOn).
   *  A hidden field keeps its value and is still sent. */
  visibleWhen?: { key: string; values: string[] };
  /** #4660: caption per raw choice of a select; a missing one shows the raw value. */
  choiceLabels?: Record<string, string>;
  /** #4660: input limit of a text field — the server limit (core/trading_settings.py). */
  maxLength?: number;
  /** #4987: sizing methods in which this field does nothing. The row stays VISIBLE and
   *  explains itself, the input is locked: unlike `dependsOn`, the function is not off,
   *  it belongs to another method. Follows the PENDING method. Display only — the
   *  server does not refuse the key. */
  inertIn?: string[];
}

/** #4660: one visibility rule for Advanced and the strategy editor. */
export function isFieldVisible(f: AdvancedField, val: (key: string) => string): boolean {
  if (f.dependsOn && val(f.dependsOn) !== "true") return false;
  if (f.visibleWhen && !f.visibleWhen.values.includes(val(f.visibleWhen.key))) return false;
  return true;
}

const SIZING_METHOD_KEY = "CLEAN_WEIGHT_SIZING";
export const SIZING_METHOD_LABELS: Record<string, string> = {
  off: "Conviction",
  a: "Equal weight",
  b: "Equal weight with risk tilts",
};

/** #4987: why `f` is locked in the pending sizing method, or null if it is not. */
export function inertHint(f: AdvancedField, val: (key: string) => string): string | null {
  const method = val(SIZING_METHOD_KEY);
  if (!f.inertIn?.includes(method)) return null;
  const effective = Object.keys(SIZING_METHOD_LABELS)
    .filter((m) => !f.inertIn?.includes(m))
    .map((m) => SIZING_METHOD_LABELS[m]);
  return `No effect in ${SIZING_METHOD_LABELS[method] ?? method}. Takes effect in ${effective.join(" or ")}.`;
}

/** #4987: a locked minimum cannot be lowered here, so its slot rule must not block an
 *  apply. The server clamps it to the slot (core/trading_settings.py, rule a). */
export function minimumFitsSlotCheckApplies(val: (key: string) => string): boolean {
  const min = ADVANCED_GROUPS.flatMap((g) => g.fields).find((f) => f.key === "MIN_POSITION_PERCENT");
  return !min || inertHint(min, val) === null;
}

/** #5001: cross-field messages per field key. A list, because the minimum carries two rules. */
export type FieldViolations = Record<string, string[]>;

export function addViolation(violations: FieldViolations, key: string, message: string): void {
  (violations[key] ??= []).push(message);
}

/** #5007: the engine's effective position count, or null when it did not report one.
 *  Never a fixed fallback: without the number the slot rules drop out, with a note. */
export function effectivePositions(s: { effective_positions?: number } | null): number | null {
  const n = Number(s?.effective_positions);
  return Number.isFinite(n) && n > 0 ? Math.round(n) : null;
}

/** Equal as text or as number — "0.30" and "0.3" are the same value. #4660: a blank
 *  string is not a number (Number("") === 0), so it only equals another blank. */
export const numEq = (a: string | undefined, b: string | undefined): boolean => {
  if (a === b) return true;
  if (a === undefined || b === undefined || a.trim() === "" || b.trim() === "") return false;
  const na = Number(a);
  const nb = Number(b);
  return Number.isFinite(na) && Number.isFinite(nb) && na === nb;
};

export interface AdvancedGroup {
  id: string;
  title: string;
  fields: AdvancedField[];
}

/** #5003: the groups follow the engine's decision: which names, buy or not, how much,
 *  within which limits, when, and how a position ends. */
export const ADVANCED_GROUPS: AdvancedGroup[] = [
  {
    // #4660: where the round table's candidates come from (core/trading_settings.py,
    // LLM_RANKING_*, #4861). #4940: the strategy editor reads the choices from meta.choices;
    // the ones here are the fallback. ADR-027 rule 1: the prompt has no example text.
    id: "candidates",
    title: "Candidates",
    fields: [
      {
        key: "FULL_UNIVERSE_TRADING_ENABLED",
        label: "Full universe",
        desc: "Evaluates the full S&P 500 universe instead of the fixed core list.",
        kind: "toggle",
      },
      {
        key: "LLM_RANKING_QUELLE",
        label: "Candidates from",
        desc: "Where the names come from that the round table evaluates each cycle.",
        kind: "select",
        choices: ["lstm", "llm", "llm_und_lstm", "liste"],
        choiceLabels: {
          lstm: "Model forecast (LSTM)",
          llm: "Your prompt",
          llm_und_lstm: "Your prompt, filled up by model forecast",
          liste: "Own list",
        },
      },
      {
        key: "LLM_RANKING_RHYTHMUS",
        label: "Refresh cadence",
        desc: "How often the prompt is asked again for a new list of names.",
        kind: "select",
        choices: ["je_refresh", "stuendlich", "alle_4_stunden", "taeglich_vor_oeffnung", "woechentlich"],
        choiceLabels: {
          je_refresh: "Every candidate refresh",
          stuendlich: "Hourly",
          alle_4_stunden: "Every 4 hours",
          taeglich_vor_oeffnung: "Daily before market open",
          woechentlich: "Weekly",
        },
        visibleWhen: { key: "LLM_RANKING_QUELLE", values: ["llm", "llm_und_lstm"] },
      },
      {
        key: "LLM_RANKING_PROMPT",
        label: "Prompt",
        desc: "Your text to the language model. Names it returns outside the S&P 500 are discarded before the round table.",
        kind: "text",
        maxLength: 2000,
        visibleWhen: { key: "LLM_RANKING_QUELLE", values: ["llm", "llm_und_lstm"] },
      },
      {
        key: "LLM_RANKING_ANBIETER",
        label: "Provider for the prompt",
        desc: "System uses the provider the engine is set up with, without web search. A chosen provider answers with its web search.",
        kind: "select",
        choices: ["system", "gemini", "openai", "anthropic"],
        choiceLabels: {
          system: "System",
          gemini: "Gemini",
          openai: "OpenAI",
          anthropic: "Anthropic",
        },
        visibleWhen: { key: "LLM_RANKING_QUELLE", values: ["llm", "llm_und_lstm"] },
      },
      {
        key: "LLM_RANKING_LISTE",
        label: "Symbols",
        desc: "Ticker symbols separated by commas. Symbols outside the S&P 500 are discarded before the round table.",
        kind: "text",
        maxLength: 1000,
        visibleWhen: { key: "LLM_RANKING_QUELLE", values: ["liste"] },
      },
      {
        key: "ROUND_TABLE_TOP_K_EVAL",
        label: "Round-table candidates",
        desc: "How many top-ranked names the round table evaluates per cycle. Must stay above the configured position count, so a replacement candidate is left when the portfolio is full. Example: with 20 positions, at least 21.",
        kind: "slider",
        min: 10,
        max: 60,
        step: 1,
      },
    ],
  },
  {
    id: "decision",
    title: "Decision",
    fields: [
      {
        key: "SIGNAL_BUY_THRESHOLD",
        label: "Buy threshold",
        desc: "Consensus score a name must reach to qualify as a buy. Also used by the signal-integrity check, which is why the range is narrow.",
        kind: "slider",
        min: 0.55,
        max: 0.75,
        step: 0.01,
      },
      {
        key: "SIGNAL_SELL_THRESHOLD",
        label: "Sell threshold",
        desc: "Consensus score below which a held name becomes a sell. Must stay below the buy threshold.",
        kind: "slider",
        min: 0.25,
        max: 0.45,
        step: 0.01,
      },
      {
        // Global opinion-exit retention (rotation + displacement + book-overflow), not
        // displacement-only — so it sits with the decision, not with an exit. Server clamp:
        // core/trading_settings.py (0.0–0.65). Threshold 0 disables it.
        key: "CONSENSUS_RETENTION_THRESHOLD",
        label: "Consensus retention",
        desc: "Keep a held name the live round table still rates above this: it is not rotated, displaced, or sold because the portfolio holds more names than its position count. Risk exits (loss cut, hard stop, trailing) are never affected. Example: at 0.60, a holding rated 0.62 stays and one rated 0.58 can be replaced. Zero turns it off.",
        kind: "slider",
        min: 0,
        max: 0.65,
        step: 0.01,
      },
    ],
  },
  {
    id: "sizing",
    title: "Position sizing",
    fields: [
      {
        key: "CLEAN_WEIGHT_SIZING",
        label: "Sizing method",
        desc: "How the engine sizes a new buy. Equal weight gives every position the same share of the portfolio: with 20 positions, 1/20. With risk tilts, that share is scaled by volatility, skew and coverage. With Conviction, the share depends on the consensus score of the round table.",
        kind: "select",
        choices: ["off", "a", "b"],
        choiceLabels: SIZING_METHOD_LABELS,
      },
      {
        key: "MIN_POSITION_PERCENT",
        label: "Minimum position size",
        desc: "Smallest share of the portfolio a new position may take. Must fit into the equal-weight slot of the configured position count: with 20 positions the slot is 1/20 = 5%, so the minimum can be 5% at most.",
        kind: "slider",
        min: 0.01,
        max: 0.1,
        step: 0.01,
        unit: "fraction",
        inertIn: ["a", "b"],
      },
      {
        key: "MAX_POSITION_PERCENT_SIZING",
        label: "Sizing cap",
        desc: "Highest share of the portfolio the Conviction method scales a position to. The share rises from the minimum at the buy threshold to this cap at a score of 1; the maximum position size still applies. Example: minimum 5%, cap 30%, buy threshold 0.60, score 0.80: 5% + 25% × 0.5 = 17.5%.",
        kind: "slider",
        min: 0.05,
        max: 0.5,
        step: 0.01,
        unit: "fraction",
        inertIn: ["a", "b"],
      },
      {
        // #3199 skew sizing tilt moved to the Agents card → "Position sizing" stage
        // (a per-symbol options-skew sizing input, peer of VIXAware). No longer an
        // Advanced field. Config lives in the S2 registry (SKEW_SIZE_TILT_*).
        key: "COVERAGE_SIZING_STRENGTH",
        // Slider range mirrors the server clamp (core/trading_settings.py: 0.0–1.0).
        // discount = 1 - strength*(1 - sqrt(coverage)); 0 disables. Server re-clamps.
        label: "Coverage sizing strength",
        desc: "How strongly a position is sized down when only part of the round table's direction votes came in this round. The size is multiplied by 1 − strength × (1 − √share voted). Example: strength 0.5 and a quarter of the vote weight in: 1 − 0.5 × (1 − 0.5) = 0.75. Zero disables it (no effect on size). This is a prudence control, not a return signal.",
        kind: "slider",
        min: 0,
        max: 1,
        step: 0.05,
        inertIn: ["a"],
      },
      {
        // #4997: runs only in the conviction branch of the cash cap (risk_deckel.py); the
        // equal-weight methods always couple cash to their own target.
        key: "PROPORTIONAL_SLOT_CASH_ENABLED",
        label: "Proportional cash per slot",
        desc: "Scales the cash a buy may use with the position's own target. Off gives every open slot the same amount. It can only lower a slot's amount, never raise it.",
        kind: "toggle",
        inertIn: ["a", "b"],
      },
      {
        // #4997: deliberately no inertIn. The stored conviction feeds the score of held
        // positions in every method (portfolio_bestand.py::update_position_conviction).
        key: "CONVICTION_EWMA_ENABLED",
        label: "Conviction smoothing",
        desc: "Blends a new conviction reading with the value stored for a held position instead of replacing it. Feeds the score of held positions in every sizing method, and the top-up target in Conviction.",
        kind: "toggle",
      },
      {
        // Slider ends mirror the server clamp (core/trading_settings.py: 0.05-1.0).
        key: "CONVICTION_EWMA_ALPHA",
        dependsOn: "CONVICTION_EWMA_ENABLED",
        label: "Weight of the new reading",
        desc: "Share of the new reading in the blend; the rest comes from the stored value. 0.3 keeps 70% of the stored value, 1 uses the new reading alone.",
        kind: "slider",
        min: 0.05,
        max: 1,
        step: 0.05,
      },
      {
        // #3361 regime throttle (dark default). Slider ends mirror the server clamp
        // (core/trading_settings.py: percentile 50–95, factor 0.1–1.0). The read-only
        // "Market regime reading" block under this group shows the live signal.
        key: "REGIME_THROTTLE_ENABLED",
        label: "Regime throttle",
        desc: "When on, new buy orders are sized down while the market regime reading (credit, rates, oil, sector correlation, index decline) is at or above the threshold below. Sells and existing positions are not affected. Without a current reading, orders keep their normal size.",
        kind: "toggle",
      },
      {
        key: "REGIME_RISKOFF_PERCENTILE",
        label: "Regime threshold (percentile)",
        desc: "The reading counts as elevated when it is at or above this percentile of its own history over roughly the last year. A higher value means the throttle applies on fewer days.",
        kind: "slider",
        min: 50,
        max: 95,
        step: 5,
        unit: "percentile",
        dependsOn: "REGIME_THROTTLE_ENABLED",
      },
      {
        key: "REGIME_THROTTLE_SIZE_FACTOR",
        label: "Throttled order size",
        desc: "Multiplier applied to the size of a new buy order while the throttle is active. 0.5 halves the order; 1 leaves it unchanged.",
        kind: "slider",
        min: 0.1,
        max: 1,
        step: 0.05,
        dependsOn: "REGIME_THROTTLE_ENABLED",
      },
    ],
  },
  {
    // #3220/#3221: order value and daily trades are operating limits INSIDE the ratified
    // guardrails. The slider ends mirror the server clamp in core/trading_settings.py, whose
    // upper bounds are the ceilings from governance/iron_dome_policy.py (imported there, not
    // copied). A value above the ceiling is clamped server-side; the response carries the
    // value that was actually applied.
    id: "limits",
    title: "Portfolio limits",
    fields: [
      {
        key: "MAX_POSITION_PERCENT",
        label: "Maximum position size",
        desc: "Largest share of the portfolio a single name may reach.",
        kind: "slider",
        min: 0.05,
        max: 0.4,
        step: 0.01,
        unit: "fraction",
      },
      {
        key: "MAX_TOTAL_EXPOSURE_PCT",
        label: "Maximum total exposure",
        desc: "Share of the portfolio that may be invested at once.",
        kind: "slider",
        min: 0.5,
        max: 1,
        step: 0.05,
        unit: "fraction",
      },
      {
        key: "COMPLIANCE_MAX_ORDER_VALUE",
        label: "Maximum order value",
        desc: "Largest amount a single order may be worth. The engine sizes an order to fit under this value; anything above it is rejected before it reaches the broker. A position can still grow past this amount across several orders.",
        kind: "slider",
        min: 1000,
        max: 100000,
        step: 1000,
        unit: "usd",
      },
      {
        key: "COMPLIANCE_MAX_DAILY_TRADES",
        label: "Maximum trades per day",
        desc: "How many orders the engine may place in one day. Once reached, further orders are blocked until the next day. Human-approved orders and risk-reducing exits do not count against it; rotations do.",
        kind: "slider",
        min: 1,
        max: 50,
        step: 1,
      },
    ],
  },
  {
    id: "pacing",
    title: "Buy pacing",
    fields: [
      {
        key: "GLOBAL_BUY_COOLDOWN_MINUTES",
        label: "Buy cooldown",
        desc: "Minutes the engine waits after a buy before the next buy.",
        kind: "slider",
        min: 0,
        max: 240,
        step: 5,
        unit: "minutes",
      },
      {
        key: "MAX_BUYS_PER_HOUR",
        label: "Max buys per hour",
        desc: "How many buys one hour may execute.",
        kind: "slider",
        min: 1,
        max: 12,
        step: 1,
      },
      {
        key: "NO_BUY_OPENING_MINUTES",
        label: "Opening pause",
        desc: "Minutes after the market open in which no buys are placed.",
        kind: "slider",
        min: 0,
        max: 120,
        step: 5,
        unit: "minutes",
      },
      {
        // #3604 re-entry lockout. Slider ends mirror the server clamp (0-10). Trading
        // days (weekdays); 1 = a fully sold name is not bought again before the next
        // trading day. Applies to every exit reason; never affects a sell.
        key: "REENTRY_LOCKOUT_DAYS",
        label: "Re-entry lockout",
        desc: "Trading days a name that was fully sold stays excluded from new buys, whatever the reason for the sale. 1 means not before the next trading day. Sells and existing positions are not affected. Zero turns it off.",
        kind: "slider",
        min: 0,
        max: 10,
        step: 1,
        unit: "tradingDays",
      },
      {
        // #3655 slot hold. Mirror of the re-entry lockout: there the NAME is locked, here
        // the SLOT a stop-loss sale freed. Server clamp 0-10 trading days.
        key: "STOP_EXIT_SLOT_HOLD_DAYS",
        label: "Slot hold after stop-loss",
        desc: "Trading days the position slot freed by a stop-loss sale stays closed to new names. The sold name itself may return once its re-entry lockout has passed. Sells are not affected. Zero turns it off.",
        kind: "slider",
        min: 0,
        max: 10,
        step: 1,
        unit: "tradingDays",
      },
      {
        // #4997 / ADR-R18: slider ends mirror the server clamp (0-0.9), which is the range
        // the engine accepts (portfolio_manager.py::_valid_topup_band_rel).
        key: "POSITION_TOPUP_DEAD_BAND_REL",
        label: "Top-up dead band",
        desc: "A held name is topped up only while it sits more than this share below its target. 25% means below 75% of the target. Applies in every sizing method. Zero turns it off.",
        kind: "slider",
        min: 0,
        max: 0.9,
        step: 0.05,
        unit: "fraction",
      },
    ],
  },
  {
    // #5003: id stays "stops", the fixed limits render after the stop fields (ahead of
    // EXIT_TRAIL_PROFILE, AdvancedTradingSection.tsx).
    // #3632: the three loss levels are the values the exit engine sells at (server clamp:
    // watch > cut > escalation > hard stop, core/trading_settings.py rule (d)); the broker
    // stop order is the backstop held at the broker (ADR-020).
    // #3632 one exit authority: the trailing toggle and the distance-from-peak slider
    // steered only the retired second rule set; the profile selects the exit engine's
    // own trailing tiers.
    id: "stops",
    title: "Exits",
    fields: [
      {
        key: "LOSS_CUT_PCT",
        label: "Loss cut",
        desc: "Loss level at which the engine sells once the position has been held at least 24 hours (sooner at the escalation level). This is the value the engine sells at. Kept above the hard stop.",
        kind: "slider",
        min: -7.9,
        max: -1,
        step: 0.1,
        unit: "percent",
      },
      {
        key: "LOSS_WATCH_PCT",
        label: "Loss watch",
        desc: "Loss level from which the position is reviewed every cycle. Does not sell by itself. Kept above the loss cut.",
        kind: "slider",
        min: -7.9,
        max: -0.5,
        step: 0.1,
        unit: "percent",
      },
      {
        key: "LOSS_ESCALATION_PCT",
        label: "Loss escalation",
        desc: "Loss level from which the exit pressure rises fastest and the engine sells at once. Kept between the loss cut and the hard stop.",
        kind: "slider",
        min: -7.9,
        max: -2,
        step: 0.1,
        unit: "percent",
      },
      {
        key: "BROKER_STOPS_ENABLED",
        label: "Broker stop order",
        desc: "Keeps a stop order at the broker for each position, for the case the engine is not running.",
        kind: "toggle",
      },
      {
        key: "STOP_LOSS_PCT",
        dependsOn: "BROKER_STOPS_ENABLED",
        label: "Broker stop distance",
        // Slider max strictly below the 8.0 hard-stop backstop (A6) — server re-clamps.
        desc: "How far below entry the broker stop order is placed. Always tighter than the fixed backstop below.",
        kind: "slider",
        min: 1,
        max: 7.5,
        step: 0.5,
        unit: "lossPercent",
      },
      {
        key: "PANIC_PROTECTION_ENABLED",
        label: "Panic protection",
        desc: "Right after a buy, only the fixed backstop can sell. Loss tiers and the trailing stop wait until the window below has passed.",
        kind: "toggle",
      },
      {
        key: "PANIC_PROTECTION_HOURS",
        dependsOn: "PANIC_PROTECTION_ENABLED",
        label: "Panic protection window (hours)",
        desc: "Hours after a buy during which only the fixed backstop can sell.",
        kind: "slider",
        min: 0.5,
        max: 4,
        step: 0.5,
        unit: "hours",
      },
      {
        key: "EXIT_TRAIL_PROFILE",
        label: "Trailing rule set",
        desc: "Which trailing rules apply to a position in profit. A trailing stop sells when the price falls a set distance from its peak, and that distance depends on the gain so far. Example in midterm: at a gain of 5 to 10%, a fall of 8% from the peak sells, once the position has been held 72 hours.",
        kind: "select",
        choices: ["midterm", "legacy"],
      },
      {
        key: "ROTATION_EXIT_ENABLED",
        label: "Rotation exits",
        desc: "Allows the engine to replace a held name with a higher-ranked candidate.",
        kind: "toggle",
      },
      {
        key: "ROTATION_MAX_EXITS_PER_CYCLE",
        dependsOn: "ROTATION_EXIT_ENABLED",
        label: "Max exits per cycle",
        // UI-min 1: values <= 0 floor to 1 inside the loop — the footgun is not offered.
        desc: "How many rotation exits a single evaluation cycle may execute.",
        kind: "slider",
        min: 1,
        max: 5,
        step: 1,
      },
      {
        key: "ROTATION_MAX_EXITS_PER_SESSION",
        dependsOn: "ROTATION_EXIT_ENABLED",
        label: "Max exits per session",
        // UI-min 1: <= 0 means "unlimited" and stays env-only.
        desc: "How many rotation exits one trading session may execute in total.",
        kind: "slider",
        min: 1,
        max: 10,
        step: 1,
      },
      {
        key: "ROTATION_PANEL_MAX_AGE_DAYS",
        dependsOn: "ROTATION_EXIT_ENABLED",
        label: "Ranking data max age",
        desc: "How old (in days) the ranking data that compares the names may be before rotation pauses.",
        kind: "slider",
        min: 1,
        max: 10,
        step: 1,
        unit: "days",
      },
      {
        key: "DISPLACEMENT_ENABLED",
        label: "Displacement exits",
        desc: "When the portfolio is full, allow selling the weakest holding to fund a higher-ranked new buy. Off means a full portfolio waits for a position to free up through a sell signal or a risk exit.",
        kind: "toggle",
      },
      {
        key: "DISPLACEMENT_MAX_PER_SESSION",
        dependsOn: "DISPLACEMENT_ENABLED",
        label: "Max displacements per session",
        // UI-min 1: <= 0 means "unlimited" and stays env-only — same as the rotation cap.
        desc: "How many displacements one trading session may execute in total. The per-cycle limit alone would not stop the whole portfolio being replaced: displacement sells are risk-reducing and exempt from the daily trade limit, and the engine runs hundreds of cycles a day.",
        kind: "slider",
        min: 1,
        max: 10,
        step: 1,
      },
    ],
  },
];

export const ADVANCED_KEYS: string[] = ADVANCED_GROUPS.flatMap((g) =>
  g.fields.map((f) => f.key),
);
