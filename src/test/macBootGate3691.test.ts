import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * #3691 — die mac-Release-Linie hatte kein Gegenstück zum C3-Boot-Test.
 *
 * Sie bewies bis zur Veröffentlichung nur, dass sich die Archive *bauen* lassen.
 * Deshalb ging `desktop-mac-v0.5.1-rc1-beta` startunfähig raus, während der
 * Windows-Build derselben Quelle im C3-Test durchfiel.
 *
 * Geprüft wird die Eigenschaft, nicht der Wortlaut: Der Boot-Test läuft, er läuft
 * VOR der Veröffentlichung, und er prüft seine eigene Entscheidungslogik zuerst.
 */

const repoRoot = join(__dirname, "..", "..");
const macWorkflow = readFileSync(join(repoRoot, ".github/workflows/release-mac.yml"), "utf-8");
const bootScript = readFileSync(join(repoRoot, "desktop/scripts/verify-artifact-boot.sh"), "utf-8");
const lines = macWorkflow.split(/\r?\n/);
const lineOf = (needle: string) => lines.findIndex((l) => l.includes(needle));

describe("#3691 mac-Linie: der Boot-Test steht vor der Veröffentlichung", () => {
  it("der Workflow führt den Boot-Test überhaupt aus", () => {
    expect(macWorkflow).toContain("verify-artifact-boot.sh");
    expect(macWorkflow).toContain("--python-tar");
    expect(macWorkflow).toContain("--engine-tar");
  });

  it("er läuft VOR dem Veröffentlichen — sonst prüft er ein bereits ausgeliefertes Artefakt", () => {
    const boot = lineOf("verify-artifact-boot.sh");
    const publish = lineOf("Publish to the autonomous_ mac release");
    expect(boot).toBeGreaterThan(-1);
    expect(publish).toBeGreaterThan(-1);
    expect(boot).toBeLessThan(publish);
  });

  it("der Selbsttest der Entscheidungslogik läuft zuerst", () => {
    // Ein Tor mit kaputter eigener Logik meldet sonst grün, ohne etwas zu prüfen.
    const selfTest = macWorkflow.indexOf("verify-artifact-boot.sh --self-test");
    const realRun = macWorkflow.indexOf("--python-tar");
    expect(selfTest).toBeGreaterThan(-1);
    expect(selfTest).toBeLessThan(realRun);
  });

  it("der Schritt verliert seinen Exit-Code nicht (#3687)", () => {
    const boot = lineOf("Verify assembled artifact boots the engine (mac twin of C3)");
    const window = lines.slice(boot, boot + 8).join("\n");
    expect(window).toMatch(/set\s+-[A-Za-z]*\s*(-o\s+)?pipefail/);
  });

  it("das Erfolgssignal ist 'healthy' — nicht 'erreichbar' und nicht 'gestartet'", () => {
    // 'starting' heisst: die Lifespan hat die BotEngine noch nicht gebaut. Genau der
    // Unterschied macht den Test zum Boot-Beweis statt zum Port-Test.
    expect(bootScript).toContain('"status"[[:space:]]*:[[:space:]]*"healthy"');
  });

  it("ein früher Prozessabgang ist ein Fehlschlag, kein Warten", () => {
    expect(bootScript).toContain("exited early");
    expect(bootScript).toContain("kill -0");
  });

  it("kopflos und ohne Zugangsdaten — damit es ein hartes Tor bleibt", () => {
    for (const env of ["IS_CI=true", "PAPER_TRADING=true", "ALPACA_API_KEY=offline_mode", "LLM_PROVIDER=ollama"]) {
      expect(bootScript).toContain(env);
    }
  });

  it("folgt dem Layout-Vertrag der echten Bereitstellung", () => {
    // resolve-paths.cjs erwartet BOT_DIR_NAME unter AAA_SOURCE_ROOT; das Runtime-Bundle
    // legt python/bin/python3 an. Weicht der Test davon ab, prüft er eine Fantasie.
    expect(bootScript).toContain('BOT_DIR_NAME="ai_trading_bot"');
    expect(bootScript).toContain("python/bin/python3");
    expect(bootScript).toContain('AAA_SOURCE_ROOT="$ROOT"');
  });
});
