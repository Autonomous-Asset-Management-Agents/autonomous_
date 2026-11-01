import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * #3646 — der Import-Smoke-Test des OSS-Snapshots prüfte die falsche Eigenschaft.
 *
 * Seit #3553 ist `config.oss.py` eine PEP-562-Fassade: `__getattr__` delegiert jeden
 * Namen an `settings._config_state`. `hasattr(config, "GCP_PROJECT_ID")` ist damit
 * immer wahr — die Zusicherung stand noch da, sagte aber nichts mehr aus und hat den
 * Snapshot blockiert, ohne dass ein Wert geleckt wäre.
 *
 * Für eine dynamisch delegierende Fassade ist der WERT die einzige sinnvolle Frage.
 */

const repoRoot = join(__dirname, "..", "..");
const verify = readFileSync(join(repoRoot, "scripts/verify_oss_snapshot.sh"), "utf-8");
const ossConfig = readFileSync(join(repoRoot, "ai_trading_bot/config.oss.py"), "utf-8");

describe("#3646 OSS-Smoke-Test prüft den Wert, nicht das Attribut", () => {
  it("config.oss.py delegiert dynamisch — hasattr wäre hier immer wahr", () => {
    // Die Voraussetzung des Befunds. Fällt die Fassade weg, darf dieser Test
    // auffallen, damit jemand die Zusicherung erneut bewertet.
    expect(ossConfig).toContain("__getattr__");
    expect(ossConfig).toContain("settings");
  });

  it("der Smoke-Test benutzt getattr auf den Wert statt hasattr", () => {
    expect(verify).toContain("getattr(config, _marker, None)");
    expect(verify).not.toContain("hasattr(config, 'GCP_PROJECT_ID')");
  });

  it("deckt die Marker ab, die die private Umgebung verraten würden", () => {
    for (const marker of ["GCP_PROJECT_ID", "VERTEX_ENDPOINT_ID"]) {
      expect(verify).toContain(marker);
    }
  });

  it("nimmt nur Marker, die settings.py OHNE Vorgabewert deklariert (#3650)", () => {
    // GCP_REGION trägt per Entwurf 'us-central1'. Ein Marker mit Vorgabewert ist
    // immer gesetzt und blockiert den Snapshot dauerhaft, ohne etwas zu schützen.
    const settings = readFileSync(join(repoRoot, "ai_trading_bot/settings.py"), "utf-8");
    const markers = [...verify.matchAll(/_marker in \(([^)]*)\)/g)]
      .flatMap((m) => [...m[1].matchAll(/'([A-Z_]+)'/g)].map((x) => x[1]));
    expect(markers.length).toBeGreaterThan(0);
    for (const marker of markers) {
      const decl = settings.split(/\r?\n/).find((l) => l.trim().startsWith(`${marker}:`));
      expect(decl, `${marker} ist in settings.py nicht deklariert`).toBeDefined();
      expect(
        /_clean_env\(\s*"[A-Z_]+"\s*\)/.test(decl ?? ""),
        `${marker} hat einen Vorgabewert und taugt nicht als Leck-Marker: ${decl?.trim()}`,
      ).toBe(true);
    }
  });

  it("die Stub-Zusicherung für secret_manager_utils bleibt erhalten", () => {
    // Die Frage „ist das der OSS-Stub oder die Enterprise-Klasse?" beantwortet
    // weiterhin dieser Test — er darf beim Umbau nicht mit verschwinden.
    expect(verify).toContain("hasattr(oauth_secrets, 'client')");
  });
});
