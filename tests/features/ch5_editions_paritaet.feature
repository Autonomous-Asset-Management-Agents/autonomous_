
@chain @editions
Feature: CH-5 Editions-Paritaet

  Scenario: Zwei Laeufe derselben Zusammenstellung sind gleich
    Given zweimal dieselbe Zusammenstellung mit demselben Seed und derselben festen Uhr
    When beide Laeufe enden
    Then sind die Entscheidungen identisch

  Scenario: Gleiche Eingabe, gleiches Modell, feste Uhr
    Given derselbe Eingabesatz, derselbe Modell-Stub und dieselbe feste Uhr
    When der Zyklus einmal in der Enterprise- und einmal in der Desktop-Zusammenstellung laeuft
    Then sind die Entscheidungen und die Order-Intents Feld fuer Feld identisch

  Scenario: Unterschiede nur in den Adaptern
    Given die beiden Laeufe weichen voneinander ab
    When der Test die Abweichung meldet
    Then nennt er Modul und Feld der Abweichung
    And trennt zulaessige Adapter-Unterschiede von unzulaessigen Kern-Unterschieden
