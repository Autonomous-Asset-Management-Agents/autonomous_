@epic-3738
Feature: Gestalt: das System in Einheiten, die am Stück lesbar sind (#3738)
  e2e: Größenregel gilt blockierend auf Stufe 2, und kein Rest über Stufe 1 ohne eingetragene Begründung

  Scharf geschaltet mit #4049: Die vier Struktur-Szenarien prüfen die Größenregel
  (step_defs/test_epic_3738.py), das fünfte prüft die Zusage „keine
  Verhaltensänderung" gegen die Verhaltensnetze der Umbauten
  (step_defs/test_epic_3738_verhalten.py). Das sechste prüft die Hotspots aus dem
  Neuzuschnitt (Epic §8.2) gegen 800/150 Zeilen und ihre Kennzahlen
  (step_defs/test_epic_3738_hotspots.py, #4190).

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

  Scenario: Der Umbau hat kein Verhalten verändert
    Given die Verhaltensnetze der ARC-E6-Umbauten
    When die Verhaltensabnahme des Epics läuft
    Then entspricht jeder Broker-Aufruf im Mandantenpfad und im Strategiepfad der eingecheckten Referenz
    And stimmt die HTTP-Fläche mit dem eingecheckten Schnappschuss überein
    And entspricht der Specialist-Bericht der Charakterisierung von vor G-8a
    And ist jeder vor ARC-E6 importierbare Agenten-Name weiter importierbar
    And greift kein Test-Patch auf Agenten- oder Router-Module ins Leere

  Scenario: Die Hotspots aus dem Neuzuschnitt sind lesbar und ihre Kennzahlen erhoben
    Given die fünf Hotspots aus Epic §8.2 mit ihren Themen-Modulen aus den Schnitt-Entscheidungen H-1 bis H-5
    When die Hotspot-Abnahme des Epics läuft
    Then liegt jede dieser Dateien bei höchstens 800 Zeilen
    And liegt jede Funktion darin bei höchstens 150 Zeilen
    And trägt der Vertrag für keine dieser Dateien eine Ausnahme und keine Begründung aus H-1 bis H-5, G-8b oder G-8c
    And trägt `docs/3738-arc-e6-gestalt/HOTSPOT_KENNZAHLEN.md` den Ausgangswert vom 06.10.2026 und einen Nachher-Messpunkt für jede dieser Dateien
