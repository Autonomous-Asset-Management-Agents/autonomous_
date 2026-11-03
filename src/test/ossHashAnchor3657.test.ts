import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * #3657 — der Snapshot-Hash suchte sein Skript im Snapshot statt im Repo.
 *
 * `verify_oss_snapshot.sh` wechselt früh mit `cd "$PUBLIC_DIR"` ins Snapshot-
 * Verzeichnis. Ab da zeigt JEDER relative Pfad dorthin — auch `$(dirname "$0")`,
 * denn der Workflow ruft das Skript als `bash scripts/verify_oss_snapshot.sh` auf.
 * Die frühere Ratekaskade landete deshalb bei `$PUBLIC_DIR/scripts/…`, wo das
 * Skript nie liegt: es gehört zum privaten Repo, während das `scripts/` des
 * Snapshots aus `ai_trading_bot/scripts/` stammt.
 *
 * Die Invariante ist nicht „benutze REPO_ROOT", sondern: **jeder Pfad ins Repo
 * wird aufgelöst, bevor das Skript das Verzeichnis wechselt.**
 */

const repoRoot = join(__dirname, "..", "..");
const verify = readFileSync(join(repoRoot, "scripts/verify_oss_snapshot.sh"), "utf-8");
const lines = verify.split(/\r?\n/);
const lineOf = (needle: string) => lines.findIndex((l) => l.includes(needle));

describe("#3657 Snapshot-Hash: Repo-Pfade vor dem Verzeichniswechsel auflösen", () => {
  it("REPO_ROOT wird aufgelöst, BEVOR ins Snapshot-Verzeichnis gewechselt wird", () => {
    const anchor = lineOf("REPO_ROOT=");
    const cd = lineOf('cd "$PUBLIC_DIR"');
    expect(anchor).toBeGreaterThan(-1);
    expect(cd).toBeGreaterThan(-1);
    expect(anchor).toBeLessThan(cd);
  });

  it("das Hash-Skript wird über REPO_ROOT adressiert, nicht geraten", () => {
    expect(verify).toContain('HASH_SCRIPT="$REPO_ROOT/scripts/compute_snapshot_hash.py"');
  });

  it("keine relativen Kandidaten mehr für das Hash-Skript", () => {
    // Genau diese Pfade zeigten nach dem cd in den Snapshot.
    expect(verify).not.toContain('"./scripts/compute_snapshot_hash.py"');
    expect(verify).not.toContain('"../scripts/compute_snapshot_hash.py"');
    expect(verify).not.toContain('$(dirname "$0")/compute_snapshot_hash.py');
  });

  it("fehlt das Skript, bricht der Lauf ab statt weiterzuraten", () => {
    expect(verify).toContain("compute_snapshot_hash.py fehlt unter");
  });

  it("das Skript liegt im privaten Repo — und NICHT unter ai_trading_bot/scripts/", () => {
    // Die Verwechslung dieser beiden scripts/-Verzeichnisse war die Ursache.
    expect(existsSync(join(repoRoot, "scripts/compute_snapshot_hash.py"))).toBe(true);
    expect(existsSync(join(repoRoot, "ai_trading_bot/scripts/compute_snapshot_hash.py"))).toBe(false);
  });
});
