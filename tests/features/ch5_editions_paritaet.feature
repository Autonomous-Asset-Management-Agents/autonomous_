@chain @editions
Feature: CH-5 Editions-Paritaet

  Scenario: Zwei Laeufe derselben Zusammenstellung sind im Kern gleich
    Given zweimal derselbe Order-Intent mit fester Uhr und festem Seed
    When beide Prozesslaeufe enden
    Then stimmen alle Kern-Felder ueberein
    And unterscheiden sich die Identitaets-Felder
    And bleibt die client_order_id aus der decision_id abgeleitet

  Scenario: Gleiche Eingabe, gleiches Modell, feste Uhr
    Given derselbe Eingabesatz, derselbe Modell-Stub und dieselbe feste Uhr
    When der Zyklus einmal in der Enterprise- und einmal in der Desktop-Zusammenstellung laeuft
    Then sind die Entscheidungen und die Order-Intents Feld fuer Feld identisch

  Scenario: Eine Kern-Abweichung wird benannt
    Given zwei Saetze, die sich im Symbol unterscheiden
    When der Vergleich laeuft
    Then nennt er Modul und Feld der Abweichung
    And weist sie als unzulaessig aus

  Scenario: Eine Adapter-Abweichung ist zulaessig
    Given zwei Saetze, die sich nur in der Broker-Order-ID unterscheiden
    When der Vergleich laeuft
    Then meldet er keine unzulaessige Abweichung
