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
  /** Key of a toggle this field is inert without. When that toggle is OFF the
   *  field is HIDDEN (not just disabled) — a dependent knob shown as active while
   *  its feature is off reads as operable when it does nothing. Visibility follows
   *  the PENDING toggle value, so switching the parent hides the children at once. */
  dependsOn?: string;
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
    id: "displacement",
    title: "Displacement",
    fields: [
      {
        key: "DISPLACEMENT_ENABLED",
        label: "Displacement exits",
        desc: "When the book is full, allow selling the weakest holding to fund a higher-ranked new buy. Off means a full book waits for a slot to free via a sell signal or a risk exit.",
        kind: "toggle",
      },
      {
        key: "DISPLACEMENT_MAX_PER_SESSION",
        dependsOn: "DISPLACEMENT_ENABLED",
        label: "Max displacements per session",
        // UI-min 1: <= 0 means "unlimited" and stays env-only — same as the rotation cap.
        desc: "How many displacements one trading session may execute in total. The per-cycle limit alone does not bound a whole-book turnover: displacement sells are risk-reducing and exempt from the daily trade cap, and the loop runs hundreds of cycles a day.",
        kind: "slider",
        min: 1,
        max: 10,
        step: 1,
      },
      {
        // Global opinion-exit retention (rotation + displacement + book-overflow), not
        // displacement-only — so it is shown regardless of the switch above. Server clamp:
        // core/trading_settings.py (0.0–0.65). Threshold 0 disables it.
        key: "CONSENSUS_RETENTION_THRESHOLD",
        label: "Consensus retention",
        desc: "Keep a held name the live round table still rates at or above this — it is not rotated, displaced, or unwound on book overflow. Risk exits (loss cut, hard stop, trailing) are never affected. Zero turns it off.",
        kind: "slider",
        min: 0,
        max: 0.65,
        step: 0.01,
      },
    ],
  },
  {
    id: "trailing",
    title: "Trailing",
    // #3632 one exit authority: the trailing toggle and the distance-from-peak slider
    // steered only the retired second rule set; the profile selects the exit engine's
    // own trailing tiers.
    fields: [
      {
        key: "EXIT_TRAIL_PROFILE",
        label: "Trailing rule set",
        desc: "Which trailing rule set applies to a position in profit. Only the two engine-known variants are offered.",
        kind: "select",
        choices: ["midterm", "legacy"],
      },
    ],
  },
  {
    id: "stops",
    title: "Stops",
    // #3632: the three loss levels are the values the exit engine sells at (server clamp:
    // watch > cut > escalation > hard stop, core/trading_settings.py rule (d)); the broker
    // stop order is the backstop held at the broker (ADR-020).
    fields: [
      {
        key: "LOSS_CUT_PCT",
        label: "Loss cut",
        desc: "Loss level at which the engine sells once the position has been held at least 24 hours (sooner at the escalation level). This is the value the engine sells at. Kept above the hard stop.",
        kind: "slider",
        min: -7.9,
        max: -1,
        step: 0.1,
      },
      {
        key: "LOSS_WATCH_PCT",
        label: "Loss watch",
        desc: "Loss level from which the position is reviewed every cycle. Does not sell by itself. Kept above the loss cut.",
        kind: "slider",
        min: -7.9,
        max: -0.5,
        step: 0.1,
      },
      {
        key: "LOSS_ESCALATION_PCT",
        label: "Loss escalation",
        desc: "Loss level from which the exit pressure rises fastest and the engine sells at once. Kept between the loss cut and the hard stop.",
        kind: "slider",
        min: -7.9,
        max: -2,
        step: 0.1,
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
        // #3199 skew sizing tilt moved to the Agents card → "Position sizing" stage
        // (a per-symbol options-skew sizing input, peer of VIXAware). No longer an
        // Advanced field. Config lives in the S2 registry (SKEW_SIZE_TILT_*).
        key: "COVERAGE_SIZING_STRENGTH",
        // Slider range mirrors the server clamp (core/trading_settings.py: 0.0–1.0).
        // discount = 1 - strength*(1 - sqrt(coverage)); 0 disables. Server re-clamps.
        label: "Coverage sizing strength",
        desc: "How strongly a position is sized down when fewer of the armed direction agents actually voted this round — a caution factor for thinly-covered decisions. Zero disables it (no effect on size). This is a prudence control, not a return signal.",
        kind: "slider",
        min: 0,
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
    // #3220/#3221: operating limits INSIDE the ratified guardrails. The slider ends
    // mirror the server clamp in core/trading_settings.py, whose upper bounds are the
    // ceilings from governance/iron_dome_policy.py (imported there, not copied).
    // A value above the ceiling is clamped server-side; the response carries the
    // value that was actually applied.
    id: "limits",
    title: "Order and trade limits",
    fields: [
      {
        key: "COMPLIANCE_MAX_ORDER_VALUE",
        label: "Maximum order value",
        desc: "Largest amount a single order may be worth. The engine sizes an order to fit under this value; anything above it is rejected before it reaches the broker. A position can still grow past this amount across several orders.",
        kind: "slider",
        min: 1000,
        max: 100000,
        step: 1000,
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
