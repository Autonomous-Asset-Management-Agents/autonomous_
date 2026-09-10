import { describe, it, expect } from "vitest";

/**
 * UXC-1 S5 (#3158) — neutrality guard over the WHOLE data-driven field table
 * (#2866 pattern). Rendering and this guard share advancedTradingFields.ts as the
 * one source, so a wording change cannot bypass the guard.
 */

import {
  ADVANCED_GROUPS,
  ADVANCED_KEYS,
} from "../console/desktop/advancedTradingFields";

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
        const text = `${g.title} ${f.label} ${f.desc}`;
        for (const re of banned) {
          expect(text, `${f.key}: ${re}`).not.toMatch(re);
        }
      }
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

  it("N3: the field table matches the nine-group plan scope (25 editable fields; #3291 Displacement group added: DISPLACEMENT_ENABLED + CONSENSUS_RETENTION_THRESHOLD)", () => {
    expect(ADVANCED_GROUPS).toHaveLength(9);
    expect(ADVANCED_KEYS).toHaveLength(25);
    expect(new Set(ADVANCED_KEYS).size).toBe(25);
  });
});
