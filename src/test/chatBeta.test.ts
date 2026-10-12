// Chat is BETA — the Sidebar launcher carries a BETA badge and the Chat page sets expectations
// (especially for the local-LLM / Ollama path, which is the roughest).
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const read = (p: string) => readFileSync(path.join(dir, "..", p), "utf8");

describe("Chat BETA labelling", () => {
  it("the Sidebar marks the Chat launcher as BETA", () => {
    const s = read("console/desktop/Sidebar.tsx");
    expect(s).toMatch(/>BETA</);
    expect(s).toMatch(/Chat<\/span>[\s\S]{0,180}BETA/); // the badge sits next to the Chat label
  });

  it("the Chat page shows a BETA notice that calls out the local-LLM (Ollama) path", () => {
    const c = read("console/desktop/pages/Chat.tsx");
    expect(c).toMatch(/BETA/);
    expect(c).toMatch(/Ollama/);
  });
});
