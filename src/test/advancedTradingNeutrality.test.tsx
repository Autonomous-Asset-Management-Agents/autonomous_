import { describe, it, expect } from "vitest";
import { FORBIDDEN } from "./wordingGuard";

/**
 * UXC-1 S5 (#3158) — neutrality guard over the WHOLE data-driven field table
 * (#2866 pattern). Rendering and this guard share advancedTradingFields.ts as the
 * one source, so a wording change cannot bypass the guard.
 */

import {
  ADVANCED_GROUPS,
  ADVANCED_KEYS,
  inertHint,
} from "../console/desktop/advancedTradingFields";

/** #5003: the table before the regrouping. A later feature extends this list; a key leaves
 *  it only with a reason (epic #4983, "Gegen Verfälschung"). */
const KEYS_BEFORE_5003 = [
  "SIGNAL_BUY_THRESHOLD", "SIGNAL_SELL_THRESHOLD",
  "ROTATION_EXIT_ENABLED", "ROTATION_MAX_EXITS_PER_CYCLE", "ROTATION_MAX_EXITS_PER_SESSION",
  "ROTATION_PANEL_MAX_AGE_DAYS",
  "DISPLACEMENT_ENABLED", "DISPLACEMENT_MAX_PER_SESSION", "CONSENSUS_RETENTION_THRESHOLD",
  "EXIT_TRAIL_PROFILE",
  "LOSS_CUT_PCT", "LOSS_WATCH_PCT", "LOSS_ESCALATION_PCT", "BROKER_STOPS_ENABLED", "STOP_LOSS_PCT",
  "PANIC_PROTECTION_ENABLED", "PANIC_PROTECTION_HOURS",
  "CLEAN_WEIGHT_SIZING", "MIN_POSITION_PERCENT", "MAX_POSITION_PERCENT",
  "MAX_POSITION_PERCENT_SIZING", "MAX_TOTAL_EXPOSURE_PCT", "COVERAGE_SIZING_STRENGTH",
  "PROPORTIONAL_SLOT_CASH_ENABLED", "CONVICTION_EWMA_ENABLED", "CONVICTION_EWMA_ALPHA",
  "REGIME_THROTTLE_ENABLED", "REGIME_RISKOFF_PERCENTILE", "REGIME_THROTTLE_SIZE_FACTOR",
  "COMPLIANCE_MAX_ORDER_VALUE", "COMPLIANCE_MAX_DAILY_TRADES",
  "GLOBAL_BUY_COOLDOWN_MINUTES", "MAX_BUYS_PER_HOUR", "NO_BUY_OPENING_MINUTES",
  "REENTRY_LOCKOUT_DAYS", "STOP_EXIT_SLOT_HOLD_DAYS", "POSITION_TOPUP_DEAD_BAND_REL",
  "FULL_UNIVERSE_TRADING_ENABLED", "ROUND_TABLE_TOP_K_EVAL",
  "LLM_RANKING_QUELLE", "LLM_RANKING_RHYTHMUS", "LLM_RANKING_PROMPT", "LLM_RANKING_ANBIETER",
  "LLM_RANKING_LISTE",
];

describe("Advanced trading neutrality (UXC-1 S5)", () => {
  it("N1: no advice, profile, risk-characterisation or performance wording in any field text", () => {
    const banned = [
      /profil/i,
      /empfohlen/i,
      /recommend/i,
      /aggressiv/i,
      /conservativ|konservativ/i,
      /kapitalerhalt/i,
      /sharpe/i,
      /outperform/i,
      /guarantee/i,
      /drawdown/i,
      /safe(r|st)?\b/i,
      /risky|riskier/i,
    ];
    for (const g of ADVANCED_GROUPS) {
      for (const f of g.fields) {
        // #4660: the select captions are field text too.
        // #4987: so is the hint of a locked field, in either method.
        const hints = ["a", "b"].map((m) => inertHint(f, () => m) ?? "").join(" ");
        const text = `${g.title} ${f.label} ${f.desc} ${Object.values(f.choiceLabels ?? {}).join(" ")} ${hints}`;
        for (const re of banned) {
          expect(text, `${f.key}: ${re}`).not.toMatch(re);
        }
      }
    }
  });

  it("N1b #4940: the provider and cadence texts carry no evaluative wording", () => {
    const banned = [/\bbest\b/i, /winner/i, /\btop\b/i, /\brank/i];
    const fields = ADVANCED_GROUPS.flatMap((g) => g.fields).filter((f) =>
      ["LLM_RANKING_ANBIETER", "LLM_RANKING_RHYTHMUS"].includes(f.key),
    );
    expect(fields).toHaveLength(2);
    for (const f of fields) {
      const text = `${f.label} ${f.desc} ${Object.values(f.choiceLabels ?? {}).join(" ")}`;
      for (const re of banned) expect(text, `${f.key}: ${re}`).not.toMatch(re);
      expect(text.match(FORBIDDEN)).toBeNull();
    }
  });

  it("N2: the non-scope keys are absent from the field table entirely", () => {
    for (const banned of [
      "TRAILING_STOP_PCT",
      "HARD_STOP_LOSS_PCT",
      "HITL_ENABLED",
      "RISK_FORBID_LEVERAGE",
      "SMART_EXIT_EXIT_RANK_HYSTERESIS",
    ]) {
      expect(ADVANCED_KEYS).not.toContain(banned);
    }
  });

  it("N1c #5003: no internal term or ID in any field text", () => {
    const internal = [
      /ADR-/i,
      /#\d/,
      /\bbook\b/i,
      /armed direction agents/i,
      /cross-sectional/i,
      /ranking panel/i,
      /dynamic sizer/i,
      /engine-known/i,
      /headroom/i,
    ];
    for (const g of ADVANCED_GROUPS) {
      for (const f of g.fields) {
        const hints = ["a", "b"].map((m) => inertHint(f, () => m) ?? "").join(" ");
        const text = `${g.title} ${f.label} ${f.desc} ${Object.values(f.choiceLabels ?? {}).join(" ")} ${hints}`;
        for (const re of internal) expect(text, `${f.key}: ${re}`).not.toMatch(re);
        expect(text.match(FORBIDDEN)).toBeNull();
      }
    }
  });

  it("N1d #5003: `book` is matched as a word, not inside another word", () => {
    expect("the whole-book turnover").toMatch(/\bbook\b/i);
    expect("bookmark").not.toMatch(/\bbook\b/i);
  });

  it("N3 #5003: six groups in the order of the decision flow; the set of keys is the one before the rearrangement (44 editable fields)", () => {
    expect(ADVANCED_GROUPS).toHaveLength(6);
    expect([...ADVANCED_KEYS].sort()).toEqual([...KEYS_BEFORE_5003].sort());
    expect(new Set(ADVANCED_KEYS).size).toBe(44);
  });
});
