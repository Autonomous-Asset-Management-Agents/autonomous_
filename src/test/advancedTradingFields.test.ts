/**
 * #4660 — the shared field table: value-dependent visibility (`visibleWhen`) of the
 * candidate-source group, and the blank-safe value comparison behind "Custom".
 */
import { describe, it, expect } from "vitest";
import {
  addViolation,
  ADVANCED_GROUPS,
  inertHint,
  isFieldVisible,
  numEq,
  type AdvancedField,
} from "@/console/desktop/advancedTradingFields";

const field = (key: string): AdvancedField => {
  const f = ADVANCED_GROUPS.flatMap((g) => g.fields).find((x) => x.key === key);
  if (!f) throw new Error(`no field ${key}`);
  return f;
};
const withValues = (values: Record<string, string>) => (key: string) => values[key] ?? "";

describe("advancedTradingFields (#4660)", () => {
  it("prompt and cadence are visible for llm and llm_und_lstm, hidden for lstm and liste", () => {
    for (const key of ["LLM_RANKING_PROMPT", "LLM_RANKING_RHYTHMUS"]) {
      expect(isFieldVisible(field(key), withValues({ LLM_RANKING_QUELLE: "llm" }))).toBe(true);
      expect(isFieldVisible(field(key), withValues({ LLM_RANKING_QUELLE: "llm_und_lstm" }))).toBe(true);
      expect(isFieldVisible(field(key), withValues({ LLM_RANKING_QUELLE: "lstm" }))).toBe(false);
      expect(isFieldVisible(field(key), withValues({ LLM_RANKING_QUELLE: "liste" }))).toBe(false);
    }
  });

  it("the symbol list is visible only for liste", () => {
    const f = field("LLM_RANKING_LISTE");
    expect(isFieldVisible(f, withValues({ LLM_RANKING_QUELLE: "liste" }))).toBe(true);
    for (const q of ["lstm", "llm", "llm_und_lstm"]) {
      expect(isFieldVisible(f, withValues({ LLM_RANKING_QUELLE: q }))).toBe(false);
    }
    expect(isFieldVisible(field("LLM_RANKING_QUELLE"), withValues({}))).toBe(true);
  });

  it("toggle dependsOn is unchanged", () => {
    const f = field("STOP_LOSS_PCT");
    expect(isFieldVisible(f, withValues({ BROKER_STOPS_ENABLED: "true" }))).toBe(true);
    expect(isFieldVisible(f, withValues({ BROKER_STOPS_ENABLED: "false" }))).toBe(false);
  });

  it("the prompt is a plain text field: 2000 characters, no placeholder", () => {
    const f = field("LLM_RANKING_PROMPT");
    expect(f.kind).toBe("text");
    expect(f.maxLength).toBe(2000);
    expect(field("LLM_RANKING_LISTE").maxLength).toBe(1000);
  });

  it("#4940: the provider follows the prompt, with the prompt's visibility", () => {
    const fields = ADVANCED_GROUPS.find((g) => g.id === "candidates")?.fields ?? [];
    const keys = fields.map((f) => f.key);
    expect(keys.indexOf("LLM_RANKING_ANBIETER")).toBe(keys.indexOf("LLM_RANKING_PROMPT") + 1);
    const f = field("LLM_RANKING_ANBIETER");
    expect(f.kind).toBe("select");
    expect(f.visibleWhen).toEqual(field("LLM_RANKING_PROMPT").visibleWhen);
    expect(f.choices).toEqual(["system", "gemini", "openai", "anthropic"]);
    expect(Object.keys(f.choiceLabels ?? {})).toEqual(f.choices);
  });

  it("#4940: the fallback knows all registry cadences, each with a caption", () => {
    const f = field("LLM_RANKING_RHYTHMUS");
    expect(f.choiceLabels?.stuendlich).toBe("Hourly");
    expect(f.choiceLabels?.alle_4_stunden).toBe("Every 4 hours");
    expect(f.choices).toEqual(Object.keys(f.choiceLabels ?? {}));
  });

  it("numEq: a blank string never equals a number", () => {
    expect(numEq("", "0")).toBe(false);
    expect(numEq("0", "")).toBe(false);
    expect(numEq("  ", "0")).toBe(false);
    expect(numEq("", "")).toBe(true);
    expect(numEq("0.30", "0.3")).toBe(true);
    expect(numEq(undefined, "0")).toBe(false);
    expect(numEq("abc", "abc")).toBe(true);
  });
});

describe("cross-field messages per key (#5001)", () => {
  it("two messages on one key do not overwrite each other", () => {
    const v: Record<string, string[]> = {};
    addViolation(v, "MIN_POSITION_PERCENT", "first");
    addViolation(v, "MIN_POSITION_PERCENT", "second");
    addViolation(v, "MAX_POSITION_PERCENT", "third");
    expect(v).toEqual({ MIN_POSITION_PERCENT: ["first", "second"], MAX_POSITION_PERCENT: ["third"] });
  });

  // The message hangs at its row now: a key that can be hidden would block Apply unseen.
  it("the keys carrying a cross-field rule are always visible", () => {
    const keys = [
      "MIN_POSITION_PERCENT",
      "MAX_POSITION_PERCENT",
      "ROUND_TABLE_TOP_K_EVAL",
      "SIGNAL_SELL_THRESHOLD",
    ];
    for (const key of keys) {
      expect(isFieldVisible(field(key), withValues({}))).toBe(true);
    }
  });
});

describe("sizing method and inert fields (#4987)", () => {
  const inMethod = (m: string) => withValues({ CLEAN_WEIGHT_SIZING: m });

  it("the method is the first field of the sizing group, with its three captions", () => {
    const sizing = ADVANCED_GROUPS.find((g) => g.id === "sizing");
    const f = sizing?.fields[0];
    expect(f?.key).toBe("CLEAN_WEIGHT_SIZING");
    expect(f?.kind).toBe("select");
    expect(f?.choices).toEqual(["off", "a", "b"]);
    expect(f?.choiceLabels).toEqual({
      off: "Conviction",
      a: "Equal weight",
      b: "Equal weight with risk tilts",
    });
    // #5091: nothing beyond these, so no choice is held back.
    expect(Object.keys(f ?? {}).sort()).toEqual(["choiceLabels", "choices", "desc", "key", "kind", "label"]);
  });

  it("#5091: the method names what Conviction depends on, and no longer a lock", () => {
    const desc = field("CLEAN_WEIGHT_SIZING").desc;
    expect(desc.endsWith("With Conviction, the share depends on the consensus score of the round table.")).toBe(true);
    expect(desc).not.toContain("cannot be chosen");
  });

  it("the inert table is the effect matrix of the sizer (risk_bemessung.py)", () => {
    const inert = (m: string) =>
      ADVANCED_GROUPS.flatMap((g) => g.fields)
        .filter((f) => inertHint(f, inMethod(m)) !== null)
        .map((f) => f.key);
    expect(inert("off")).toEqual([]);
    expect(inert("b")).toEqual([
      "MIN_POSITION_PERCENT",
      "MAX_POSITION_PERCENT_SIZING",
      "PROPORTIONAL_SLOT_CASH_ENABLED",
    ]);
    expect(inert("a")).toEqual([
      "MIN_POSITION_PERCENT",
      "MAX_POSITION_PERCENT_SIZING",
      "COVERAGE_SIZING_STRENGTH",
      "PROPORTIONAL_SLOT_CASH_ENABLED",
    ]);
  });

  it("#4997: slot cash is locked outside Conviction, smoothing and dead band stay usable", () => {
    expect(inertHint(field("PROPORTIONAL_SLOT_CASH_ENABLED"), inMethod("b"))).toBe(
      "No effect in Equal weight with risk tilts. Takes effect in Conviction.",
    );
    expect(inertHint(field("PROPORTIONAL_SLOT_CASH_ENABLED"), inMethod("off"))).toBeNull();
    for (const key of ["CONVICTION_EWMA_ENABLED", "CONVICTION_EWMA_ALPHA", "POSITION_TOPUP_DEAD_BAND_REL"]) {
      expect(inertHint(field(key), inMethod("a"))).toBeNull();
      expect(inertHint(field(key), inMethod("b"))).toBeNull();
    }
  });

  it("#4997: the new fields sit in their groups, with the server's bounds", () => {
    const keys = (id: string) => ADVANCED_GROUPS.find((g) => g.id === id)?.fields.map((f) => f.key);
    expect(keys("sizing")).toEqual(
      expect.arrayContaining([
        "CONVICTION_EWMA_ENABLED",
        "CONVICTION_EWMA_ALPHA",
        "PROPORTIONAL_SLOT_CASH_ENABLED",
      ]),
    );
    expect(keys("pacing")).toContain("POSITION_TOPUP_DEAD_BAND_REL");
    expect(field("CONVICTION_EWMA_ENABLED").kind).toBe("toggle");
    expect(field("PROPORTIONAL_SLOT_CASH_ENABLED").kind).toBe("toggle");
    const alpha = field("CONVICTION_EWMA_ALPHA");
    expect([alpha.min, alpha.max, alpha.dependsOn]).toEqual([0.05, 1, "CONVICTION_EWMA_ENABLED"]);
    const band = field("POSITION_TOPUP_DEAD_BAND_REL");
    expect([band.min, band.max]).toEqual([0, 0.9]);
  });

  it("#4997: the dead band says it applies in every sizing method", () => {
    expect(field("POSITION_TOPUP_DEAD_BAND_REL").desc).toMatch(/every sizing method/i);
  });

  it("the hint names the active method and every method the field works in", () => {
    expect(inertHint(field("MIN_POSITION_PERCENT"), inMethod("b"))).toBe(
      "No effect in Equal weight with risk tilts. Takes effect in Conviction.",
    );
    expect(inertHint(field("COVERAGE_SIZING_STRENGTH"), inMethod("a"))).toBe(
      "No effect in Equal weight. Takes effect in Conviction or Equal weight with risk tilts.",
    );
  });

  it("a field outside the table is never locked", () => {
    expect(inertHint(field("MAX_POSITION_PERCENT"), inMethod("a"))).toBeNull();
    expect(inertHint(field("CLEAN_WEIGHT_SIZING"), inMethod("a"))).toBeNull();
  });
});

describe("groups in the order of the decision flow (#5003)", () => {
  const group = (key: string) =>
    ADVANCED_GROUPS.find((g) => g.fields.some((f) => f.key === key))?.title;

  it("the groups follow the engine's decision", () => {
    expect(ADVANCED_GROUPS.map((g) => g.title)).toEqual([
      "Candidates",
      "Decision",
      "Position sizing",
      "Portfolio limits",
      "Buy pacing",
      "Exits",
    ]);
  });

  it("the render special cases keep their group ids", () => {
    expect(ADVANCED_GROUPS.find((g) => g.id === "sizing")?.title).toBe("Position sizing");
    expect(ADVANCED_GROUPS.find((g) => g.id === "stops")?.title).toBe("Exits");
  });

  it("consensus retention is a decision, not part of displacement", () => {
    expect(group("CONSENSUS_RETENTION_THRESHOLD")).toBe("Decision");
    expect(group("DISPLACEMENT_ENABLED")).toBe("Exits");
  });

  it("every field sits where the decision uses it", () => {
    expect(group("LLM_RANKING_QUELLE")).toBe("Candidates");
    expect(group("ROUND_TABLE_TOP_K_EVAL")).toBe("Candidates");
    expect(group("FULL_UNIVERSE_TRADING_ENABLED")).toBe("Candidates");
    expect(group("SIGNAL_BUY_THRESHOLD")).toBe("Decision");
    expect(group("MIN_POSITION_PERCENT")).toBe("Position sizing");
    expect(group("MAX_POSITION_PERCENT_SIZING")).toBe("Position sizing");
    expect(group("REGIME_THROTTLE_ENABLED")).toBe("Position sizing");
    expect(group("MAX_POSITION_PERCENT")).toBe("Portfolio limits");
    expect(group("MAX_TOTAL_EXPOSURE_PCT")).toBe("Portfolio limits");
    expect(group("COMPLIANCE_MAX_ORDER_VALUE")).toBe("Portfolio limits");
    expect(group("REENTRY_LOCKOUT_DAYS")).toBe("Buy pacing");
  });

  it("exits run stops, trailing, rotation, displacement", () => {
    const keys = ADVANCED_GROUPS.find((g) => g.id === "stops")?.fields.map((f) => f.key) ?? [];
    expect(keys.indexOf("PANIC_PROTECTION_HOURS")).toBeGreaterThanOrEqual(0);
    expect(keys.indexOf("PANIC_PROTECTION_HOURS")).toBeLessThan(keys.indexOf("EXIT_TRAIL_PROFILE"));
    expect(keys.indexOf("EXIT_TRAIL_PROFILE")).toBeLessThan(keys.indexOf("ROTATION_EXIT_ENABLED"));
    expect(keys.indexOf("ROTATION_PANEL_MAX_AGE_DAYS")).toBeLessThan(keys.indexOf("DISPLACEMENT_ENABLED"));
  });
});

describe("worked examples (#5003)", () => {
  it("the coverage example is the sizer's formula (risk_skalierer.py)", () => {
    expect(1 - 0.5 * (1 - Math.sqrt(0.25))).toBeCloseTo(0.75, 10);
    expect(field("COVERAGE_SIZING_STRENGTH").desc).toContain("0.75");
  });

  it("the sizing cap example is the conviction span (risk_skalierer.py)", () => {
    const span = (c: number, s: number, min: number, cap: number) =>
      min + ((cap - min) * (c - s)) / (1 - s);
    expect(span(0.8, 0.6, 0.05, 0.3)).toBeCloseTo(0.175, 10);
    expect(field("MAX_POSITION_PERCENT_SIZING").desc).toContain("5% + 25% × 0.5 = 17.5%");
  });

  it("the slot example is 1/N", () => {
    expect(field("MIN_POSITION_PERCENT").desc).toContain("1/20 = 5%");
    expect(field("CLEAN_WEIGHT_SIZING").desc).toContain("1/20");
  });

  it("a fraction field names its shares in the unit it shows (#4999)", () => {
    expect(field("POSITION_TOPUP_DEAD_BAND_REL").desc).toContain("25% means below 75%");
    for (const key of ["MIN_POSITION_PERCENT", "MAX_POSITION_PERCENT_SIZING", "POSITION_TOPUP_DEAD_BAND_REL"]) {
      expect(field(key).unit).toBe("fraction");
      expect(field(key).desc, key).not.toMatch(/\b0\.(05|175|25|30)\b/);
    }
  });

  it("consensus retention keeps a holding above the value, as the gate compares (consensus_retention.py)", () => {
    expect(field("CONSENSUS_RETENTION_THRESHOLD").desc).toContain("still rates above this");
  });

  it("a held name at the retention value and a trailing tier have their example", () => {
    expect(field("CONSENSUS_RETENTION_THRESHOLD").desc).toContain("Example");
    expect(field("EXIT_TRAIL_PROFILE").desc).toContain("Example");
  });
});
