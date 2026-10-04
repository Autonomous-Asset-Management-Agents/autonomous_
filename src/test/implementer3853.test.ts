import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * #3853 — Verdrahtungs-Wächter für AUT-9 Stufe 3.
 *
 * `scripts/tests/test_impl_gate.py` prüft die Auswahl, `test_impl_apply.py` die Grenzen,
 * `test_impl_telemetrie.py` die Zahlen. Dieser Test prüft, dass der Workflow sie
 * überhaupt benutzt.
 *
 * Der Einsatz ist höher als bei Maker und Zustandsmaschine: Hier schreibt ein Automat
 * Produktionscode. Eine falsch verdrahtete Stufe 3 öffnet PRs, die niemand bestellt hat.
 */

const repoRoot = join(__dirname, "..", "..");
const read = (p: string) => readFileSync(join(repoRoot, p), "utf-8");

const wf = read(".github/workflows/implementer.yml");
// F-5 Teil B (#4054): Takt, Push-Auslöser und Auswahl liegen im Torwächter.
const tor = read(".github/workflows/implementer_tor.yml");
const gate = read("scripts/impl_gate.py");
const apply = read("scripts/impl_apply.py");
const prompt = read("docs/prompts/IMPL_SUB_ISSUE.md");

describe("#3853 Implementierer: Verdrahtung", () => {
  it("entscheidet über impl_gate.py, nicht im YAML", () => {
    // Eine Bedingung in einer Workflow-Datei ist nicht testbar — und diese entscheidet,
    // woran eine Maschine Produktionscode schreibt.
    expect(wf).toContain("python scripts/impl_gate.py");
    expect(gate).toContain("def pick_next_sub_issue(");
  });

  it("Netz 1: ohne freigegebenen Plan auf main läuft nichts", () => {
    // `plan-approved` ist das Tor, kein eigenes Label (Owner-Entscheidung 01.10.2026).
    // Ein Plan liegt nur auf `main`, wenn er freigegeben UND sein Merge beauftragt
    // wurde — zwei menschliche Entscheidungen, am Dateibaum messbar statt an einem
    // Label, dessen Sync kaputt ist (#3796).
    expect(gate).toMatch(/if not it\.get\("hat_plan"\):/);
    expect(gate).toContain("implementation_plan.md");
    // Das Wort darf in der Begruendung stehen; der MECHANISMUS darf es nicht geben.
    expect(gate).not.toContain("AUTH_LABEL");
    expect(wf).not.toContain("impl:authorized");
  });

  it("Netz 1b: greift nur nach Sub-Issues im Status „Implementing“ (#3868)", () => {
    // Lauf 36834033920 wählte #2945 — ein altes Issue aus dem Handelspfad. „Hat einen
    // Plan auf main“ allein traf 36 von 100 offenen Issues, weil dort 169 Plan-
    // verzeichnisse liegen. Zwei Bedingungen engen das auf die Zerlegung ein:
    // Elternteil (= Sub-Issue eines Epics) und der Board-Status, der die Freigabe
    // kodiert. Der MECHANISMUS, nicht das Wort — Kommentare dürfen beides nennen.
    expect(gate).toMatch(/if not it\.get\("eltern"\):/);
    expect(gate).toMatch(/if it\.get\("status"\) != IMPLEMENTING:/);
    // #3872: je Epic nur das fruehestes offene Sub-Issue.
    expect(gate).toContain("def _an_der_reihe(");
    expect(gate).toMatch(/an_der_reihe\.get\(it\["eltern"\]\) != it\["number"\]/);
    // Die Elternschaft kommt über GraphQL; `gh issue list --json` kennt `parent` nicht.
    expect(gate).toMatch(/parent\{number\}/);
  });

  it("Netz 2: der Pfadwächter prüft den Diff, nicht einen Text", () => {
    // Anders als beim Maker (#3797): Der konnte Workflows gar nicht erreichen, der
    // Implementierer kann es.
    expect(apply).toContain("def verbotene_pfade(");
    expect(apply).toContain('GOVERNANCE_VERZEICHNISSE = (".github/workflows/",)');
    expect(apply).toContain('GOVERNANCE_DATEIEN = ("scripts/ai_judge.py",)');
  });

  it("Netz 3: die Tests laufen vor dem PR, nicht danach", () => {
    const i = apply.indexOf("PRUEFBEFEHL");
    const j = apply.indexOf("gh", apply.indexOf("pruefe_und_oeffne_pr"));
    expect(i).toBeGreaterThan(-1);
    expect(j).toBeGreaterThan(-1);
    expect(apply).toMatch(/raise Abbruch\(\s*\n?\s*"tests_rot"/);
  });

  it("ein leerer Diff ist kein Erfolg", () => {
    expect(apply).toMatch(/raise Abbruch\("leer"/);
  });

  it("der PR schliesst sein Sub-Issue", () => {
    // Ohne `Closes #N` schliesst der Merge das Sub-Issue nicht, und das Board bleibt
    // stehen (WoW §8b).
    expect(apply).toContain("Closes #{sub_issue}");
  });

  it("setzt die Lease VOR der Arbeit und räumt sie immer auf", () => {
    const lease = wf.indexOf("Lease setzen");
    // Der AUFRUF, nicht das Wort: Kommentare duerfen "claude -p" nennen.
    // Der AUFRUF, nicht das Wort — und seit #3931 ohne Argument, mit STDIN.
    const arbeit = wf.indexOf("claude -p --output-format json");
    const auf = wf.indexOf("Lease aufräumen");
    expect(lease).toBeGreaterThan(-1);
    expect(arbeit).toBeGreaterThan(lease);
    expect(auf).toBeGreaterThan(arbeit);
    expect(wf.slice(auf, auf + 200)).toContain("if: always()");
  });

  it("die Zeitgrenze des Jobs stimmt mit der Lease-Grenze überein", () => {
    // Läuft die Lease früher ab, übernimmt ein zweiter Lauf, während der erste noch
    // arbeitet — und zwei Agenten im selben Arbeitsverzeichnis sind kein Zustand,
    // den man debuggen möchte.
    const env = wf.match(/IMPL_TIMEOUT_MINUTES:\s*"(\d+)"/)?.[1];
    const job = wf.match(/runs-on:\s*\[self-hosted[^\]]*\][\s\S]*?timeout-minutes:\s*(\d+)/)?.[1];
    expect(env).toBeDefined();
    expect(job).toBe(env);
    expect(gate).toContain(`DEFAULT_TIMEOUT_MINUTES = ${env}`);
  });

  it("ein Zeitlimit sichert den Zwischenstand, statt die Arbeit zu verwerfen", () => {
    // G-1b wurde zweimal nach 90 Minuten mitten in der Arbeit abgebrochen; der nächste
    // Lauf räumte den Arbeitsbaum, und `impl:failed` sperrte das Sub-Issue, als wäre der
    // Agent gescheitert. Jetzt: innere Zeitgrenze für den Agenten, Zwischenstand auf
    // wip/<nr>, und der nächste Lauf setzt dort fort.
    const arbeit = wf.indexOf("claude -p --output-format json");
    const vorAgent = wf.slice(0, arbeit);
    const nachAgent = wf.slice(arbeit);
    expect(vorAgent).toMatch(/impl_zwischenstand\.py bereite --sub-issue "\$ISSUE"/);
    expect(vorAgent).toMatch(/timeout [^\n]*"\$\{AGENT_MINUTEN\}m"[^\n]*$/m);
    expect(wf).toMatch(/AGENT_MINUTEN=\$\(\( IMPL_TIMEOUT_MINUTES - \d+ \)\)/);
    expect(nachAgent).toMatch(/impl_zwischenstand\.py sichere --sub-issue "\$ISSUE"/);
    // Der Agent-Exit darf das Skript nicht vor der Sicherung beenden.
    expect(wf).toMatch(/set \+e[\s\S]*AGENT_EXIT=\$\?[\s\S]*set -e/);
  });

  it("die Zeitgrenze reicht für die größten gemessenen Umsetzungen", () => {
    // Gemessen am 02./03.10.: G-1a brauchte 69 Minuten (146 Züge), G-1b wurde nach
    // 90 Minuten mitten in der Arbeit abgebrochen — die Arbeit war verloren, und das
    // Sub-Issue trug danach `impl:failed`, ohne dass der Agent etwas falsch gemacht hatte.
    const env = Number(wf.match(/IMPL_TIMEOUT_MINUTES:\s*"(\d+)"/)?.[1]);
    expect(env).toBeGreaterThanOrEqual(150);
  });

  it("ruft den Agenten mit --output-format json auf", () => {
    // Sonst gibt es keine Zahlen (§3b). Der Maker ruft claude -p ohne diesen Schalter
    // auf und wirft Token, Kosten und Latenz weg — das war der Grund für die
    // Ablehnung von PR #3854.
    expect(wf).toMatch(/claude -p[\s\S]{0,120}--output-format json/);
    expect(existsSync(join(repoRoot, "scripts/impl_telemetrie.py"))).toBe(true);
  });

  it("klont flach und vertieft erst bei Arbeit (#3865)", () => {
    // Das Repository hat 17,5 GiB in 20,8 Mio Objekten und 1.595 Branches. Ein voller
    // Klon dauerte zweimal gemessen 30 bzw. 39 Minuten, und beide Laeufe starben im
    // Checkout, ohne `claude -p` je zu erreichen. Ein Leerlauf-Lauf braucht die
    // Historie nicht.
    const checkout = wf.slice(wf.indexOf("actions/checkout@v4"), wf.indexOf("- name: Auswahl"));
    expect(checkout).not.toMatch(/^\s*fetch-depth:/m);

    // Vertieft wird im Arbeitsschritt, also NACH der Auswahl.
    const auswahl = wf.indexOf("python scripts/impl_gate.py");
    const vertiefen = wf.indexOf("--unshallow");
    expect(vertiefen).toBeGreaterThan(auswahl);
    expect(vertiefen).toBeLessThan(wf.indexOf("python scripts/impl_run.py"));
  });

  it("sichert die Telemetrie auch bei einem Abbruch", () => {
    const tele = wf.indexOf("Telemetrie sichern");
    expect(tele).toBeGreaterThan(-1);
    expect(wf.slice(tele, tele + 220)).toContain("if: always()");
  });

  it("läuft stündlich, nicht alle fünf Minuten", () => {
    // Ein Lauf darf 90 Minuten dauern; ein dichterer Takt stapelte sich nur.
    expect(tor).toMatch(/- cron: "40 \* \* \* \*"/);
    expect(wf).not.toMatch(/schedule:/);
    expect(wf).toMatch(/group:\s*implementer/);
    expect(wf).toContain("cancel-in-progress: false");
  });

  it("läuft vollständig auf dem lokalen Runner", () => {
    const runners = [...wf.matchAll(/^\s*runs-on:\s*(.+)$/gm)].map((m) => m[1].trim());
    expect(runners).toEqual(["[self-hosted, windows, claude]"]);
  });

  it("der Prompt macht den Plan verbindlich und verbietet stilles Abweichen", () => {
    // #3957: Verbindlich ist der Vertrag (Abnahmekriterien, Rahmen), nicht das
    // Drehbuch. Das alte „brich ab und sag es" bei jeder Abweichung hat 15 von 26
    // Laeufen in den Abbruch getrieben. Geblieben ist: Abweichen nur offen, und ein
    // Abbruch nur in den drei benannten Faellen.
    expect(prompt).toMatch(/Er ist verbindlich/);
    expect(prompt).toMatch(/Vertrag über das Ergebnis, kein Drehbuch/);
    expect(prompt).toMatch(/drei Fälle, sonst keiner/);
    expect(prompt).toMatch(/zu verschweigen/);
    expect(prompt).not.toMatch(/brich ab und sag es/);
    expect(prompt).toMatch(/zuerst rot|muss rot sein/i);
  });

  it("installiert KEIN Python auf dem Self-hosted-Runner (#3874)", () => {
    // #3872 pinnte hier `actions/setup-python` auf 3.12 — und legte Maker und
    // Implementierer lahm. Auf einem GitHub-gehosteten Runner liegt 3.12 im
    // Tool-Cache; auf DIESEM Runner nicht, also versucht die Action zu INSTALLIEREN:
    //
    //     Install Python 3.12.10 in C:\actions-runner\_work\_tool\Python...
    //     ##[error]Error happened during Python installation
    //
    // Gemessen mit `py -0p`: installiert sind 3.14 und 3.13; der PATH-Eintrag fuer
    // Python312 zeigt ins Leere. Ein Pin auf eine Version, die es nicht gibt, ist
    // kein Pin, sondern ein Ausfall. Die Version gehoert auf den Runner, nicht in
    // den Workflow (#3874).
    expect(wf).not.toContain("setup-python");
  });

  it("schreibt die Telemetrie nicht in den Arbeitsbaum (#3872)", () => {
    // `git add -A` nahm eine relative impl_run.jsonl mit; in PR #3871 lag sie im Diff.
    expect(wf).not.toMatch(/IMPL_TELEMETRIE_PFAD:\s*impl_run\.jsonl\s*$/m);
    expect(wf).toMatch(/IMPL_TELEMETRIE_PFAD:\s*\$\{\{\s*runner\.temp\s*\}\}/);
    expect(wf).toMatch(/IMPL_LOG_PFAD:\s*\$\{\{\s*runner\.temp\s*\}\}/);
  });

  it("Netz 2 sieht auch NEU angelegte Dateien (#3872)", () => {
    // git diff --name-only HEAD zeigt nur verfolgte Aenderungen, git add -A committet
    // auch neue. Der Waechter muss dasselbe sehen wie der Commit.
    expect(apply).toMatch(/ls-files",\s*\n?\s*"--others"/);
    expect(apply).toContain('"--exclude-standard"');
  });

  it("Netz 5: ohne Walkthrough-Fragment entsteht kein PR (#3872)", () => {
    expect(apply).toContain("def walkthrough_fehlt(");
    expect(apply).toMatch(/raise Abbruch\(\s*\n?\s*"walkthrough"/);
    expect(prompt).toMatch(/docs\/walkthroughs\/PR_/);
  });

  it("der PR-Text traegt die CIA aus dem Plan (#3872)", () => {
    expect(apply).toContain("def cia_aus_plan(");
    expect(apply).toMatch(/Impact-Analyse \(CIA\)/);
  });

  it("die Rueckkante in die Planung existiert (#3872)", () => {
    const run = read("scripts/impl_run.py");
    expect(run).toContain('BEFUND_DATEI = "BEFUND_PLAN.md"');
    expect(run).toContain('ZIELSPALTE_PLAN_BEFUND = "Analyzing (Plan)"');
    // Exit 0: der Workflow setzt impl:failed nur bei job.status != success, und ein
    // erkannter Planmangel ist ein Ergebnis, kein Fehlschlag des Agenten.
    expect(run).toContain("AUSGANG_PLAN_BEFUND = 0");
    expect(prompt).toContain("BEFUND_PLAN.md");
  });

  it("der Befund wird ein Plan-PR, kein Kommentar in der Sackgasse (#3902)", () => {
    const run = read("scripts/impl_run.py");
    // Die Bahn 5b nimmt `plan-review-requested` auf — kein neues Label erfinden.
    expect(run).toContain('BEFUND_LABEL = "plan-review-requested"');
    expect(run).toContain("def plan_pr_aus_befund(");
    // Ein Plan-PR darf das Sub-Issue nicht schliessen: keine Zeile Code existiert.
    const koerper = run.slice(run.indexOf("def _befund_pr_body"), run.indexOf("def _zurueck"));
    expect(koerper).not.toContain("Closes #");
    // Der Prompt verlangt den UEBERARBEITETEN Plan, nicht nur die Kritik.
    expect(prompt).toMatch(/implementation_plan\.md.{0,80}überarbeitet/s);
  });

  it("die Rückkante ruft find_board_item mit beiden Argumenten (#3902)", () => {
    const run = read("scripts/impl_run.py");
    expect(run).toMatch(/find_board_item\(\s*besitzer,\s*sub_issue\s*\)/);
    // Ein stiller Fehlschlag war der Defekt: die Kante meldet jetzt zurueck.
    expect(run).toMatch(/def _zurueck_in_die_planung\([^)]*\)\s*->\s*bool:/);
  });

  it("ein offener Plan-PR sperrt das Sub-Issue (#3902)", () => {
    expect(gate).toMatch(/if it\.get\("offener_plan_pr"\):/);
    expect(gate).toContain("plan-nachbesserung");
    // #3916 (R2): Der Branchname traegt die Lauf-Nummer, also Praefix-Suche statt
    // --head mit exaktem Namen.
    expect(gate).toContain("head:docs/");
  });

  it("ein Aussetzer VOR der Arbeit sperrt das Sub-Issue nicht (#3916)", () => {
    // Lauf 36886782713 starb im Lease-Schritt an einem GitHub-Serverfehler, bevor
    // Schritt 6 startete - und sperrte #3823 dauerhaft, denn impl:failed wird nie
    // automatisch entfernt.
    const auf = wf.indexOf("Lease aufräumen");
    const block = wf.slice(auf, auf + 1400);
    expect(block).toContain("steps.arbeit.outcome");
    expect(wf).toMatch(/id:\s*arbeit/);
  });

  it("ein blockiertes Epic meldet sich am Epic (#3927)", () => {
    // In der Nacht zum 02.10. lief der Implementierer neunmal erfolgreich und
    // lieferte nichts: acht Takte "keines bereit", weil PR #3924 rot stand und
    // die Reihenfolge-Regel das Epic anhielt. Niemand wurde geweckt.
    const wh = read("scripts/epic_wachhund.py");
    expect(wh).toContain("def blockierte_epics(");
    // Dieselbe Funktion wie die Auswahl - ein Waechter mit eigener Logik meldet
    // Stillstand, wo gearbeitet wird, oder schweigt, wo es steht.
    expect(wh).toContain("pick_next_sub_issue");
    // Die Meldung geht ans Epic, nicht ins Protokoll.
    expect(gate).toContain('"issue",');
    expect(gate).toContain('"comment",');
    // Nur wenn nichts lief.
    expect(gate).toMatch(/if auswahl\.issue is None:/);
  });

  it("der Prompt geht über STDIN, nicht als Argument (#3931)", () => {
    // Lauf 36975937103 starb an "Argument list too long" (Exit 126): Prompt 5.565
    // Bytes plus Plan #3819 31.382 Bytes = rund 37 KB. Pläne wachsen mit jeder
    // Nachbesserung, die Argumentgrenze nicht.
    expect(wf).not.toMatch(/claude -p "\$\(cat/);
    expect(wf).toMatch(/claude -p --output-format json/);
    expect(wf).toMatch(/< "\$RUNNER_TEMP\/impl_prompt\.md"/);
  });

  it("ein Merge auf main stößt den Implementierer an (#3931)", () => {
    // GitHub verwirft Cron-Takte: Am 02.10. lief der Maker mit `*/5` faktisch alle
    // 10 bis 37 Minuten, der Implementierer kam 6 bis 35 Minuten zu spät, 00:40 und
    // 06:40 fielen ganz aus. Nach einem Merge ist typischerweise das nächste
    // Sub-Issue frei - darauf soll die Kette nicht stundenlang warten.
    // Seit F-5 Teil B (#4054) im Torwächter, der dann den Arbeiter anstößt.
    const on = tor.slice(tor.indexOf("on:"), tor.indexOf("permissions:"));
    expect(on).toMatch(/push:/);
    expect(on).toMatch(/branches: \[main\]/);
    // Der Zeitplan bleibt als Netz darunter.
    expect(on).toMatch(/- cron:/);
    expect(tor).toMatch(/gh workflow run implementer\.yml/);
  });

  it("die Epic-Abnahme liest Check-Runs mit dem Standard-Token (#4025)", () => {
    // BPMN 11b: Das Epic geht nur bei grünem `Backend BDD Tests` auf Review. Den
    // Check-Run liest impl_gate mit CHECKS_TOKEN = github.token; dafür braucht der
    // Workflow `checks: read`. Fehlt eines, ist die Abnahme nie prüfbar.
    // Seit F-5 Teil B (#4054) läuft die Abnahme im Torwächter mit.
    const permissions = tor.slice(tor.indexOf("permissions:"), tor.indexOf("jobs:"));
    expect(permissions).toMatch(/checks: read/);
    const auswahl = tor.slice(
      tor.indexOf("Auswahl (scripts/impl_gate.py)"),
      tor.indexOf("python scripts/impl_gate.py"),
    );
    expect(auswahl).toMatch(/CHECKS_TOKEN: \$\{\{ github\.token \}\}/);
  });

  it("enthält keine Steuerzeichen", () => {
    const control = [...wf].filter(
      (c) => c.charCodeAt(0) < 9 || (c.charCodeAt(0) > 13 && c.charCodeAt(0) < 32),
    );
    expect(control).toHaveLength(0);
  });
});
