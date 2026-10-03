@allure.label.feature:VC-4_Risk_Management_&_Compliance
@allure.label.story:CH-2_Iron_Dome_Completeness
@chain @h2
Feature: CH-2 — Vollstaendigkeit des Iron Dome
  Jede Kapitalbewegung traegt eine geprueffte Entscheidung.
  Das Epic ARC-E1 (#3366) ist erst fertig, wenn dieses Szenario gruen ist.

  Heute ist es rot, und zwar aus dem richtigen Grund: der Vertrag ComplianceDecision
  existiert nicht. core/compliance.py:244 liefert einen Wahrheitswert, keinen Datensatz —
  es gibt also keinen unvollstaendigen Beleg, es gibt gar keinen.

  Scenario: Jede Broker-Order traegt eine ComplianceDecision
    Given der Vertrag ComplianceDecision ist im Kern verfuegbar
    When ein Zyklus Einstieg, Stop, Verdraengung, Notverkauf, Breaker und Strategiewechsel ausloest
    Then traegt jede Broker-Order eine ComplianceDecision mit Grund-Code und Halt-Zustand
    And es existiert keine Broker-Order ohne zugehoerigen OrderIntent

  Scenario: Genau ein Broker-Aufrufer im Kern
    Given der Kern ist unveraendert
    When die Broker-mutierenden Aufrufstellen gezaehlt werden
    Then fuehrt genau eine Stelle Orders an den Broker aus
