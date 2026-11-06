Feature: Die Startschritte der Handelsschleife tragen Outbox und Sperre
  Abnahme ARC-E2.13 (Issue 3489) fuer Epic 3367. Die bisherige Kette ruft die Absendestelle direkt; hier
  laeuft ein Engine-Prozess die Schritte, die live_trading_loop vor und zu Beginn eines Zyklus
  ausfuehrt: Outbox-Abgleich, Erwerb der Schreibberechtigung, dann ein Einstieg.

  Scenario: Die Vorrichtung folgt der Reihenfolge der Handelsschleife
    Given die Startschritte der Handelsschleife
    Then ruft live_trading_loop Outbox-Abgleich, Erwerb und Zyklus-Erwerb in dieser Reihenfolge

  Scenario: Der Start gleicht einen unbestaetigten Intent ab, statt nachzusenden
    Given ein Engine-Prozess stirbt zwischen Absenden und Bestaetigung
    When die Engine mit ihren Startschritten neu startet
    Then ist der Intent in der Outbox bestaetigt
    And haelt der Broker genau eine Order

  Scenario: Zwei Engines im selben Konto - nur eine handelt im Zyklus
    Given zwei Engine-Prozesse durchlaufen gleichzeitig ihre Startschritte
    Then haelt der Broker genau eine Order
    And eine Engine hat gemeldet, dass sie nicht handelt

  Scenario: Eine abgestuerzte Engine wird sofort abgeloest
    Given eine Engine haelt die Schreibberechtigung und stirbt hart
    When sie auf demselben Rechner sofort neu startet
    Then handelt sie im ersten Zyklus
