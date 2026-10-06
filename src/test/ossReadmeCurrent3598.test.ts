// #3598: the READMEs that ship in the OSS snapshot carried claims that contradict the canonical
// sources, and one link into the PRIVATE repo — 404 for every visitor, the same defect class as
// the website links in #3304.
//
// Measured on 23.09.:
//   README.oss.md:4   OSS-CI badge -> .../Dev-Enviroment/...            404 (private repo)
//   README.oss.md:64  "9-Agent Consensus"        canonical: 14 agents
//   docs/oss/README.md:89  "8/9 agents active"   canonical: 14 agents
//   README.md:45 "9-Agent Board", :53 "all 11 agents vote"
// The same files state 14 elsewhere, so they contradicted themselves.
//
// The snapshot renames README.oss.md -> README.md (scripts/oss_make_snapshot.sh), so README.oss.md
// IS the public front page; the private README.md is never published.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const read = (p: string) => readFileSync(resolve(process.cwd(), p), "utf8");

/** Files that reach the public snapshot verbatim. */
const PUBLISHED = ["README.oss.md", "docs/oss/README.md", "docs/oss/TROUBLESHOOTING.md"];
/** Every README that states an agent count, published or not. */
const READMES = [...PUBLISHED, "README.md"];

/** The canonical roster size, read from the agent catalogue itself — so this test follows the
 *  source instead of pinning a number that would rot the next time an agent is added. */
function canonicalAgentCount(): number {
  const cat = read("docs/2_agentic_operations/AGENT_CATALOG.md");
  const m = cat.match(/(\d+)\s+Agenten/);
  expect(m, "AGENT_CATALOG.md no longer states its roster size").toBeTruthy();
  return parseInt(m![1], 10);
}

describe("OSS READMEs (#3598) — no links into the private repo", () => {
  it.each(PUBLISHED)("%s does not reference Dev-Enviroment", (file) => {
    expect(read(file), `${file} links the PRIVATE repo — 404 for every visitor`).not.toContain(
      "Autonomous-Asset-Management-Agents/Dev-Enviroment",
    );
  });
});

describe("READMEs (#3598) — agent counts follow the catalogue", () => {
  const expected = canonicalAgentCount();

  it.each(READMES)("%s states no agent count other than the canonical one", (file) => {
    const text = read(file);
    const claims = [
      ...text.matchAll(/(\d+)[-\s]?(?:Agent|Agenten|agents)\b/gi),
      ...text.matchAll(/\b\d+\/(\d+)\s*agents\b/gi),
    ].map((m) => parseInt(m[1], 10));
    const wrong = claims.filter((n) => n !== expected);
    expect(wrong, `${file} claims ${wrong.join(", ")} agents; catalogue says ${expected}`).toEqual([]);
  });
});

describe("README.md (#3598) — no stale status badge", () => {
  it("does not advertise a hardcoded 1.0.0 status", () => {
    const text = read("README.md");
    // RELEASE_BASELINE.md says of itself: "not maintained as the current system state".
    expect(text).not.toMatch(/badge\/Status-1\.0\.0/);
  });
});
