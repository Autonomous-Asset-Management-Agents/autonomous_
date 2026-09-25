Feature: Lückenkarte und Golden Set

  Background:
    Given Ein Golden Set "sizing_golden_2811.json" liegt als Erwartung vor

  Scenario: Lückenkarte im Bericht
    Given Ein durchgeführter Audit-Lauf
    When der Bericht erstellt wird
    Then nennt er je Agent den Anteil der Symbole mit vollständiger Datengrundlage
    And je Enthaltung den Grund-Code

  Scenario: Kuensliche Luecke im Golden Set
    Given eine kuenstliche Luecke im Golden Set eingefuegt wurde
    When der Bericht erstellt wird
    Then aendert sich der Anteil des betroffenen Agents
