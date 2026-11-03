Feature: Enthaltung statt Rauschen

  Agenten, die keine Daten haben (z. B. DrawdownGuard bei fehlenden Preisen),
  sollen sich enthalten statt mit Ersatzwerten ein Richtungsvotum abzugeben.

  @chain @vc1 @vc2
  Scenario: Enthaltung statt Rauschen
    Given die Datenquelle eines Agenten liefert keine Werte fuer ein Symbol
    When der Rat ueber das Symbol abstimmt
    Then meldet der Agent eine Enthaltung mit Grund
    And seine Stimme geht nicht als Richtungsvotum in den Konsens ein
    And der Konsens ist identisch mit dem Konsens ohne diese Stimme

  @chain @vc1
  Scenario: Score und Enthaltung schliessen sich aus
    Given eine Stimme mit Score und Enthaltungsgrund
    When sie gebaut wird
    Then wird sie abgewiesen
