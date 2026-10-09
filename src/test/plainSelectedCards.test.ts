import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// Plain "selected" standard (#2035 follow-up). An active/selected OPTION card carries its
// state through the StatusDot (a small dot + white text) — NOT a coloured background fill.
// The old pattern filled the selected card with a green/amber tint + coloured border/title
// (`border-bull/50 bg-bull/10` for the LLM tiles + "Full Autonomous", `border-amber/50
// bg-amber/10` for "Human-in-the-loop"), which read as a green "shimmer" and clashed with
// the calm plain design. This guard fails if that filled-selected pattern creeps back in.

const consoleDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "console");

const OPTION_CARD_FILES = [
  path.join(consoleDir, "desktop", "LlmProviderCard.tsx"),
  path.join(consoleDir, "desktop", "pages", "Settings.tsx"),
];

// The tinted "selected fill" / coloured-rim shades that must not return on option cards.
const BANNED_SELECTED_FILL = [/bg-bull\/10/, /bg-amber\/10/, /border-bull\/50/, /border-amber\/50/];

describe("selected option cards use the plain StatusDot standard (no colour fill)", () => {
  it("LlmProviderCard + Settings option cards carry no green/amber selected-fill", () => {
    const offenders: string[] = [];
    for (const f of OPTION_CARD_FILES) {
      const src = readFileSync(f, "utf8");
      for (const re of BANNED_SELECTED_FILL) {
        if (re.test(src)) offenders.push(`${path.basename(f)} :: ${re}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
