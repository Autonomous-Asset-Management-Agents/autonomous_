// GTM-1 T5 (#1468): minor trust polish.
// (a) EU AI Act Art. 50 — the landing chat widget must disclose AI interaction.
// (b) RFC 9116 — a /.well-known/security.txt responsible-disclosure contact.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const readSrc = (p: string) => readFileSync(path.join(dir, "..", p), "utf8");
const readRepo = (p: string) => readFileSync(path.join(dir, "..", "..", p), "utf8");

describe("GTM-1 T5: trust polish", () => {
  for (const file of ["LandingViewE.tsx", "LandingViewD.tsx", "LandingViewB.tsx"]) {
    it(`${file}: discloses AI interaction at the chat widget (EU AI Act Art. 50)`, () => {
      const src = readSrc(`components/views/${file}`);
      // the chat widget exists...
      expect(src).toMatch(/ask the agents anything/i);
      // ...and it discloses that the user is interacting with an AI system
      expect(src).toMatch(/interacting with an AI system/i);
      expect(src).toMatch(/AI-generated/i);
    });
  }

  it("ships an RFC 9116 /.well-known/security.txt with a responsible-disclosure contact", () => {
    const txt = readRepo("public/.well-known/security.txt");
    expect(txt).toMatch(/^Contact:\s*mailto:security@aaagents\.de/m);
    expect(txt).toMatch(/^Expires:/m);
  });
});
