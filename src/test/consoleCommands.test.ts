// XAI-1 / XAI-T9 command-surface (#1338): pins the deterministic navigation-command
// interpreter. Typing a nav command in the console chat drives setDesktopPage — a pure,
// LLM-free, bilingual (EN/DE) mapping; questions and unknown targets fall through to the
// engine chat (return null), so the interpreter can NEVER hijack a real question.
import { describe, it, expect } from "vitest";
import { interpretConsoleCommand } from "@/console/desktop/consoleCommands";

describe("interpretConsoleCommand (XAI-T9 command-surface, #1338)", () => {
  it("routes the Gherkin example 'öffne das Dashboard' to the overview page", () => {
    expect(interpretConsoleCommand("öffne das Dashboard")).toEqual({
      kind: "navigate",
      page: "overview",
    });
  });

  it.each([
    ["open positions", "positions"],
    ["go to settings", "settings"],
    ["zeige die Berichte", "reports"],
    ["show me the audit chain", "audit"],
    ["navigate to chat", "chat"],
    ["wechsle zu Entscheidungen", "decisions"],
    ["open the overview", "overview"],
    ["show portfolio", "overview"],
  ] as const)("maps %s -> %s", (text, page) => {
    expect(interpretConsoleCommand(text)).toEqual({ kind: "navigate", page });
  });

  it.each([
    "how many positions do I have?",
    "summarize my portfolio",
    "what is the audit chain?",
    "is the market open?",
    "show me how positions work",
    "open the fridge",
    "",
    "   ",
  ])("returns null for non-navigation input: %s", (text) => {
    expect(interpretConsoleCommand(text)).toBeNull();
  });
});
