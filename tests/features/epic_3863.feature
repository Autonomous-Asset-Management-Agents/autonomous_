@epic-3863
Feature: [Epic] FAB-1 — Sichtbarkeit: was nicht läuft, soll es sagen (#3863)
  e2e: Kein toter Workflow, kein unbeachtetes Testverzeichnis und kein Leerlauf bleibt unsichtbar

  Diese Abnahme ist beim Anlegen rot und bleibt es, solange das Epic offen ist.
  Sie wird grün, wenn alle Sub-Issues von #3863 umgesetzt sind — und
  erst dann darf der xfail-Marker in den Schrittdefinitionen fallen.

  Scenario: Die Fabrik meldet, was sie nicht tut
    Given die Sichtbarkeitspruefungen aus FAB-1 laufen ueber den Stand von main
    When der Bericht ueber die Rueckmeldung der Fabrik erzeugt wird
    Then nennt er keinen Agenten-Workflow, der ohne sein Kernkommando success melden kann
    And kein Testverzeichnis, das in keinem Workflow vorkommt
    And keinen Cron-Workflow, der einen Lauf ohne Auftrag als success abschliesst
    And keinen Agenten-Workflow, dessen letztes Lebenszeichen aelter ist als die vereinbarte Frist
