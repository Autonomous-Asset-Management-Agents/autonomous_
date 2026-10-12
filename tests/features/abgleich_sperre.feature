Feature: Die Abgleich-Sperre sperrt und wird von einem Menschen aufgehoben
  Abnahme ARC-E2.12 (Issue 3488) fuer Epic 3367. Prueft die Sperre aus Issue 3389, ihre Wirkung im
  Order-Pfad (Issue 3491) und die Bedienstelle (Issue 3430) in einem Lauf: echter Abgleich gegen
  das Auftragsbuch, echte Absendestelle, echter Engine-Endpunkt.

  Scenario: Eine Abweichung sperrt Einstiege, Schutz-Exits laufen
    Given beim Broker liegt eine Order, die die Engine nicht kennt, und die Sperrwirkung ist an
    When der Abgleich laeuft
    Then ist die Sperre gesetzt
    And wird ein Einstieg zurueckgehalten
    And geht ein Schutz-Exit hinaus

  Scenario: Ein sauberer Folgelauf hebt die Sperre nicht auf
    Given beim Broker liegt eine Order, die die Engine nicht kennt, und die Sperrwirkung ist an
    When der Abgleich laeuft
    Then findet der Folgelauf keine Abweichung mehr
    And bleibt die Sperre bestehen

  Scenario: Die Aufhebung durch einen Menschen gibt Einstiege frei und ist protokolliert
    Given beim Broker liegt eine Order, die die Engine nicht kennt, und die Sperrwirkung ist an
    When der Abgleich laeuft
    Then nimmt der Engine-Endpunkt die Aufhebung an
    And geht der naechste Einstieg hinaus
    And traegt das Protokoll Zeitpunkt, Urheber und die freigegebene Abweichung
