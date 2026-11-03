// #3599: der Vertrag zwischen privater und öffentlicher README.
//
// Die Aufteilung ist bewusst: `README.md` ist die vollständige interne Fassung, `README.oss.md`
// die reduzierte öffentliche. Umgesetzt wird sie im Snapshot-Skript durch zwei Schritte, die beide
// stillschweigend brechen können:
//   1. eine POSITIVLISTE der Wurzel-Dokumente — steht `README.md` je darin, tritt die interne
//      Fassung an die Öffentlichkeit;
//   2. eine UMBENENNUNG `README.oss.md` → `README.md` — fällt sie weg, bleibt die Startseite von
//      `autonomous_` leer, weil GitHub nur `README.md` rendert.
// Beides war bisher reine Konvention im Skript und von keinem Test gedeckt.
//
// Dritter Vertrag: was öffentlich steht, darf nur auf Öffentliches verweisen. Gemessen am 23.09.
// enthielt das öffentliche `docs/` nur `oss/`, `superpowers/` und ein Issue-Verzeichnis, während
// die öffentliche README nach `docs/0_strategy_and_roadmap/` verlinkte — 404 für jeden Leser.
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

const read = (p: string) => readFileSync(resolve(process.cwd(), p), "utf8");
const SNAPSHOT = "scripts/oss_make_snapshot.sh";

/** Die Wurzel-Dokumente, die das Skript in den Snapshot kopiert (Positivliste in Schritt 2). */
function rootAllowList(): string[] {
  const m = read(SNAPSHOT).match(/for f in ([^;]+); do/);
  expect(m, `${SNAPSHOT}: Positivliste der Wurzel-Dokumente nicht gefunden`).toBeTruthy();
  return m![1].trim().split(/\s+/);
}

describe("OSS-Snapshot (#3599) — die private README bleibt privat", () => {
  it("die Positivliste kopiert README.oss.md, aber niemals README.md", () => {
    const list = rootAllowList();
    expect(list, "README.oss.md fehlt in der Positivliste").toContain("README.oss.md");
    expect(list, "README.md stünde damit öffentlich — die interne Fassung würde veröffentlicht")
      .not.toContain("README.md");
  });

  it("die reduzierte Fassung wird zur öffentlichen Startseite umbenannt", () => {
    // Ohne diesen Schritt rendert GitHub nichts: die Startseite von autonomous_ bliebe leer.
    expect(read(SNAPSHOT)).toMatch(/mv\s+"\$PUBLIC_DIR_NEW\/README\.oss\.md"\s+"\$PUBLIC_DIR_NEW\/README\.md"/);
  });
});

describe("OSS-Snapshot (#3599) — Veröffentlichtes verweist nur auf Veröffentlichtes", () => {
  // Vom Snapshot ausgeliefert: die Wurzel-Dokumente der Positivliste und docs/oss/.
  const publishedDocs = readdirSync(resolve(process.cwd(), "docs/oss"))
    .filter((f) => f.endsWith(".md"))
    .map((f) => `docs/oss/${f}`);
  const files = ["README.oss.md", ...publishedDocs];

  it.each(files)("%s verlinkt keinen Pfad, den der Snapshot nicht ausliefert", (file) => {
    const links = [...read(file).matchAll(/\]\((\.?\/?(?:docs|ai_trading_bot|scripts)\/[^)#]+)/g)]
      .map((m) => m[1].replace(/^\.\//, ""));
    const dead = links.filter((l) => !l.startsWith("docs/oss/"));
    expect(dead, `${file} verlinkt nicht veröffentlichte Pfade: ${dead.join(", ")}`).toEqual([]);
  });
});
