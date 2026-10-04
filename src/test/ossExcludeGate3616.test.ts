import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * #3616 — Kopierer und Ausschluss-Tor teilen eine Liste, aber nicht deren Auslegung.
 *
 * Der Snapshot war blockiert, weil `oss_make_snapshot.py` mit `fnmatch` nur
 * `ai_trading_bot/` filtert, waehrend `verify_oss_snapshot.sh` mit `pathspec`
 * den GANZEN Snapshot prueft. Alles ausserhalb von `ai_trading_bot/` — etwa das
 * per `cp -r` kopierte `src/` — entkam der Liste, bis das fail-closed Tor kam.
 *
 * Diese Tests halten die drei Eigenschaften fest, die das wieder zusammenfuehren.
 */

const repoRoot = join(__dirname, '..', '..')
const read = (p: string) => readFileSync(join(repoRoot, p), 'utf-8')

const excludeList = read('scripts/oss_exclude.txt')
const makeSnapshot = read('scripts/oss_make_snapshot.sh')
const verifySnapshot = read('scripts/verify_oss_snapshot.sh')

/** Negationen (`!pfad`) aus der Sperrliste, ohne Kommentare. */
const negations = excludeList
  .split('\n')
  .map(line => line.replace(/#.*$/, '').trim())
  .filter(line => line.startsWith('!'))
  .map(line => line.slice(1).trim())

describe('#3616 OSS-Ausschluss: eine Liste, eine Auslegung', () => {
  it('bereinigt den GANZEN Snapshot mit derselben Engine wie das Tor', () => {
    // Ohne diesen Durchgang filtert der Kopierer nur ai_trading_bot/, und
    // alles aus `cp -r src public` erreicht ungeprueft das Tor.
    expect(makeSnapshot).toContain('oss_prune_snapshot.py')

    // Dieselbe Engine wie verify_oss_snapshot.sh — sonst driften beide erneut.
    const prune = read('scripts/oss_prune_snapshot.py')
    expect(prune).toContain('pathspec')
    expect(prune).toContain('gitwildmatch')

    // Fail-closed: ein fehlendes pathspec darf nicht stillschweigend
    // durchwinken, sonst ist die Bereinigung wertlos (vgl. #3601/#3607).
    expect(prune).toMatch(/sys\.exit\(1\)/)
  })

  it('bereinigt auch die Frontend-Verzeichnisse, die per cp -r kommen', () => {
    const pruneCall = makeSnapshot.indexOf('oss_prune_snapshot.py')
    const frontendCopy = makeSnapshot.indexOf('for d in src public')
    expect(frontendCopy).toBeGreaterThan(-1)
    // Die Bereinigung muss NACH dem Frontend-Kopieren laufen.
    expect(pruneCall).toBeGreaterThan(frontendCopy)
  })

  it('deklariert jedes bewusst oeffentliche Artefakt als Negation', () => {
    // `data/*` ist nur aus Groessengruenden gesperrt; das Manifest wird
    // absichtlich nachkopiert (oss_make_snapshot.sh Step 2.4).
    expect(negations).toContain('data/models_manifest.json')
    // `.env.example` entsteht aus `.env.oss.example` — dieselbe Trennung
    // oeffentlich/privat wie README.oss.md -> README.md.
    expect(negations).toContain('.env.example')
  })

  it('erzeugt zu jeder Negation auch wirklich eine Datei', () => {
    // Eine Negation ohne Erzeuger waere ein stilles Loch in der Sperrliste.
    for (const path of negations) {
      expect(
        makeSnapshot.includes(path),
        `Negation "${path}" hat keinen Erzeuger in oss_make_snapshot.sh`,
      ).toBe(true)
    }
  })

  it('haelt echten Muell draussen: src/dummy.txt bleibt gesperrt', () => {
    expect(negations).not.toContain('src/dummy.txt')
    expect(negations).not.toContain('dummy.txt')
    expect(excludeList).toMatch(/^dummy\.txt$/m)
  })

  it('prueft bei umbenannten Dateien die Herkunft, nicht nur den Namen', () => {
    // Ein blosses `!.env.example` wuerde auch die PRIVATE .env.example
    // durchlassen. Das Tor muss den Inhalt gegen die Quelle stellen.
    expect(verifySnapshot).toContain('.env.oss.example')
    expect(existsSync(join(repoRoot, '.env.oss.example'))).toBe(true)
  })
})
