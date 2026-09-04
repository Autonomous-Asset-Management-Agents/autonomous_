/**
 * UXC-1 S5 (#3158, Epic #3151) — data-driven field table of the "Advanced" section.
 *
 * CLIENT MIRROR ONLY: bounds and defaults here are convenience for slider ends and
 * badges; the AUTHORITATIVE clamps live server-side in `core/trading_settings.py`
 * (S2) and every apply goes through the WORM-audited flow (S3). The neutrality
 * guard test and the rendering share THIS one source, so a wording change cannot
 * bypass the guard.
 *
 * Deliberately NOT here (epic non-scope): TRAILING_STOP_PCT (never wired on the
 * live round-table path, B9), COMPLIANCE_MAX_DAILY_TRADES (A3), HARD_STOP_LOSS_PCT
 * as an INPUT (A6 — rendered as a read-only guardrail row instead), any DrawdownGuard
 * veto/severe knobs (agent toggles live on the Agents card, S4), and
 * SMART_EXIT_EXIT_RANK_HYSTERESIS (hardcoded in config.py:473 — offering it would be
 * silent misoperation until an S1 seam exists).
 */

export type AdvancedFieldKind = "toggle" | "slider" | "select";

export interface AdvancedField {
  key: string;
  label: string;
  /** Factual mechanics only — no advice, no profiles (#2866). */
  desc: string;
  kind: AdvancedFieldKind;
  min?: number;
  max?: number;
  step?: number;
  choices?: string[];
}

export interface AdvancedGroup {
  id: string;
  title: string;
  fields: AdvancedField[];
}

export const ADVANCED_GROUPS: AdvancedGroup[] = [
  {
    id: "thresholds",
    title: "Consensus thresholds",
    fields: [
      {
        key: "SIGNAL_BUY_THRESHOLD",
        label: "Buy threshold",
        desc: "Consensus score a name must reach to qualify as a buy. Also feeds the signal-integrity check (ADR-SEC-01), hence the tight band.",
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
    ],
  },
  {
    id: "rotation",
    title: "Rotation",
    fields: [
      {
        key: "ROTATION_EXIT_ENABLED",
        label: "Rotation exits",
        desc: "Allows the engine to replace a held name with a higher-ranked candidate.",
        kind: "toggle",
      },
      {
        key: "ROTATION_MAX_EXITS_PER_CYCLE",
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
        label: "Ranking panel max age",
        desc: "How old (in days) the cross-sectional ranking panel may be before rotation pauses.",
        kind: "slider",
        min: 1,
        max: 10,
        step: 1,
      },
    ],
  },
  {
    id: "trailing",
    title: "Trailing",
    fields: [
      {
        key: "EXIT_POLICY_TRAILING_ENABLED",
        label: "Trailing exit",
        desc: "Exits a winner when it falls back a set distance from its peak.",
        kind: "toggle",
      },
      {
        key: "TRAILING_FROM_PEAK_PCT",
        label: "Distance from peak",
        desc: "How far below its peak a position may fall before the trailing exit fires. Inert while the trailing exit is off.",
        kind: "slider",
        min: 3,
        max: 25,
        step: 0.5,
      },
      {
        key: "EXIT_TRAIL_PROFILE",
        label: "Trailing rule set",
        desc: "Which trailing rule set applies. Only the two engine-known variants are offered.",
        kind: "select",
        choices: ["midterm", "legacy"],
      },
    ],
  },
  {
    id: "stops",
    title: "Stop / take profit",
    fields: [
      {
        key: "STOP_LOSS_PCT",
        label: "Stop loss",
        // Slider max strictly below the 8.0 hard-stop backstop (A6) — server re-clamps.
        desc: "Exit when a position falls this far below entry. Always tighter than the fixed backstop below.",
        kind: "slider",
        min: 1,
        max: 7.5,
        step: 0.5,
      },
      {
        key: "TAKE_PROFIT_PCT",
        label: "Take profit",
        desc: "Take gains when a position rises this far above entry.",
        kind: "slider",
        min: 5,
        max: 100,
        step: 1,
      },
    ],
  },
  {
    id: "sizing",
    title: "Position sizing bands",
    fields: [
      {
        key: "MIN_POSITION_PERCENT",
        label: "Minimum position size",
        desc: "Smallest share of the book a new position may take. Must fit into the equal-weight slot of the configured position count.",
        kind: "slider",
        min: 0.01,
        max: 0.1,
        step: 0.01,
      },
      {
        key: "MAX_POSITION_PERCENT",
        label: "Maximum position size",
        desc: "Largest share of the book a single name may reach.",
        kind: "slider",
        min: 0.05,
        max: 0.4,
        step: 0.01,
      },
      {
        key: "MAX_POSITION_PERCENT_SIZING",
        label: "Sizing cap",
        desc: "Upper cap the dynamic sizer may scale a position to.",
        kind: "slider",
        min: 0.05,
        max: 0.5,
        step: 0.01,
      },
      {
        key: "MAX_TOTAL_EXPOSURE_PCT",
        label: "Maximum total exposure",
        desc: "Share of the book that may be invested at once.",
        kind: "slider",
        min: 0.5,
        max: 1,
        step: 0.05,
      },
      {
        key: "SKEW_SIZE_TILT_ENABLED",
        label: "Skew sizing tilt",
        desc: "Lets the 25-delta option risk-reversal nudge a position's size — a bullish upside skew sizes up, a downside skew sizes down. Off by default; no effect until the cap below is above zero.",
        kind: "toggle",
      },
      {
        key: "SKEW_SIZE_TILT_CAP",
        // Slider max mirrors the server clamp (core/trading_settings.py: 0.0–0.20); the
        // tilt is bounded to [1-cap, 1+cap] per order. Server re-clamps authoritatively.
        label: "Skew tilt cap",
        desc: "The largest fraction the skew tilt may move a position's size, up or down. Zero disables the tilt. Inert while the skew sizing tilt is off.",
        kind: "slider",
        min: 0,
        max: 0.2,
        step: 0.01,
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
      },
    ],
  },
  {
    id: "universe",
    title: "Universe",
    fields: [
      {
        key: "FULL_UNIVERSE_TRADING_ENABLED",
        label: "Full universe",
        desc: "Evaluates the full S&P 500 universe instead of the fixed core list.",
        kind: "toggle",
      },
      {
        key: "ROUND_TABLE_TOP_K_EVAL",
        label: "Round-table candidates",
        desc: "How many top-ranked names the round table evaluates per cycle. Must stay above the configured position count (displacement headroom).",
        kind: "slider",
        min: 10,
        max: 60,
        step: 1,
      },
    ],
  },
];

export const ADVANCED_KEYS: string[] = ADVANCED_GROUPS.flatMap((g) =>
  g.fields.map((f) => f.key),
);
