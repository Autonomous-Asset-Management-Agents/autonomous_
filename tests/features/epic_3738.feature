@epic-3738
Feature: Gestalt: das System in Einheiten, die am Stück lesbar sind (#3738)
  e2e: Größenregel gilt blockierend auf Stufe 2, und kein Rest über Stufe 1 ohne eingetragene Begründung

  Diese Abnahme ist beim Anlegen rot und bleibt es, solange das Epic offen ist.
  Sie wird grün, wenn alle Sub-Issues von #3738 umgesetzt sind — und
  erst dann darf der xfail-Marker in den Schrittdefinitionen fallen.

  Scenario: Die Größenregel gilt blockierend und trägt die Stufe-2-Schwellen
    Given der Architektur-Vertrag `ai_trading_bot/tests/architecture/vertrag.toml`
    When die Abnahme des Epics läuft
    Then enthält der Abschnitt `[modus]` den Eintrag `groessen = 'blockieren'`
    And die eingetragene Dateischwelle ist höchstens 1000 Zeilen
    And die eingetragene Funktionsschwelle ist höchstens 200 Zeilen

  Scenario: Kein Rest über Stufe 1 ohne eingecheckte Begründung
    Given der gemessene Produktivcode unter `ai_trading_bot/` ohne Tests und Vendor
    When eine Datei über 2000 Zeilen oder eine Funktion über 400 Zeilen liegt
    And die zugehörige Vertragszeile kein gefülltes Feld `begruendung` trägt
    Then schlägt die Abnahme fehl und nennt Datei und Zeile

  Scenario: Die im Befund genannten Einheiten liegen unter ihren Stufe-1-Zahlen
    Given die sechs Dateien über 2000 Zeilen und die acht Funktionen über 400 Zeilen aus dem Befund vom 28./30.09.2026
    When die Abnahme läuft
    Then liegt jede dieser Einheiten unter ihrer Stufe-1-Zahl oder trägt eine begründete Vertragszeile

  Scenario: Die Ratsche kann nicht still zurückrollen
    Given eine Einheit mit eingetragener Obergrenze im Vertrag
    When ihr gemessener Wert unter die eingetragene Zahl fällt
    Then schlägt die Abnahme fehl, bis die Zahl im Vertrag mitgesenkt ist
