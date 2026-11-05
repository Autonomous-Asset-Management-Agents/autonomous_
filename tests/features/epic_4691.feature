# language: de
@epic-4691
Funktionalität: [Epic] FAB-COST — CI/CD Runner-Minuten- & GitHub-Kosten-Reduktion ohne Verlust an Testtiefe, BORA oder Factory-Autonomie (#4691)
  e2e: Jede Kostenmaßnahme des Epics ist in Kraft und durch eine Ratsche gehalten, und kein Pflicht-, Release- oder Testschutz ist verloren

  Zweck-Kennzahl: Abgerechnete Linux-Minuten von Dev-Enviroment je ci.yml-PR-Lauf.
  Sie bleibt die Kennzahl des Epics und wird **berichtet**, nicht geprüft
  (`python scripts/mess_ci_kosten.py --tage 7`). Begründung in Fassung 5 des Plans
  zu #4709: Die Zahl teilt eine feste Grundlast durch einen variablen Nenner und
  schwankte über sieben Tage zwischen 73,0 (24.10.2026, 76 Läufe) und 133,4
  (19.10.2026, 7 Läufe). Eine Schwelle darauf prüft den Tag, nicht die Maßnahme —
  und bei einem Einzeltag ist die Streuung größer, nicht kleiner. Dazu braucht die
  Erhebung die GitHub-API, die es im Job „Backend BDD Tests" nicht gibt.

  Diese Abnahme prüft deshalb ausschließlich **am Dateistand**: offline, ohne Datum,
  ohne Netz. Owner-Entscheidung vom 27.10.2026.

  Szenario: Jede Maßnahme des Epics ist in Kraft und durch eine Ratsche gehalten
  Angenommen die auf main gemergten Maßnahmen von #4691
  Wenn die Workflows und die Ratschen in scripts/tests/ gelesen werden
  Dann rufen die Melder aus #4766 A eine concurrency-Gruppe je PR mit cancel-in-progress true auf
  Und aut9-flow.yml, project-automation.yml und sync-plan-labels.yml tragen cancel-in-progress false
  Und kein Workflow mit einem Pflicht- oder Release-Kontext bricht ab
  Und release-drafter.yml hat als einzigen Auslöser workflow_dispatch
  Und jede Dependabot-Gruppe gilt nur für version-updates und nur für minor und patch
  Und der Job „Docs / Markdown link check" ruft markdown-link-check gebündelt auf
  Und ein Backend-Lauf von ci.yml installiert requirements-torch-ci.txt in höchstens 4 Jobs
  Und oss-ci.yml bricht überholte PR-Läufe ab und überspringt bei reinen Factory-Pfaden den Docker-Stack-Build
  Und tor-neulauf.yml übernimmt den Neulauf der Pflicht-Tore und ist selbst kein Pflicht-Kontext

  Szenario: Kein Bestandsschutz geht verloren
  Angenommen die Pflicht-Checks aus .github/pflicht_checks.yml und die Release-Checks aus desktop-release.yml
  Wenn die Workflows gelesen werden
  Dann stehen alle unter unverändertem Namen
  Und jeder heutige Pytest-Aufruf der Backend-Gates steht mit unveränderten Testpfaden und Coverage-Schwellen 75 und 60 weiter in ci.yml
