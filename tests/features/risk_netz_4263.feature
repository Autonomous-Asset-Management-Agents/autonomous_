Feature: Netz fuer Konto, Halt, Liquidation und Vorpruefung (H-3a, #4263)
  Als Betreiber
  Moechte ich Breaker, Halt und Vorpruefung des RiskManager gegen eine eingecheckte Referenz gefahren sehen
  Damit die Zerlegung H-3c bis H-3h kein Verhalten am Broker und am Halt unbemerkt aendert

  @vc4
  Scenario: Portfolio-Stop gerissen
    Given ein echter RiskManager mit eigenem Halt und zwei gehaltenen Positionen
    When das Eigenkapital den Portfolio-Stop reisst
    Then ist der eigene Halt gesetzt
    And Orders am Broker-Client und Risiko-Ereignisse entsprechen der Referenz

  @vc4
  Scenario: Tages-Limit gerissen
    Given ein echter RiskManager mit eigenem Halt, zwei Positionen und abgeschaltetem Portfolio-Stop
    When der Drawdown das Tages-Limit ueberschreitet
    Then gehen die Orders aus der Referenz durch das echte Tor an den Broker-Client
    And jede traegt die Absicht "breaker"
    And der eigene Halt ist gesetzt

  @vc4
  Scenario: Tages-Limit gerissen, Broker kennt get_all_positions nicht
    Given ein echter RiskManager mit eigenem Halt und einem Broker ohne Positionsabfrage
    When der Drawdown das Tages-Limit ueberschreitet
    Then geht keine Order an den Broker-Client
    And der eigene Halt ist gesetzt

  @vc4
  Scenario: Warnstufe ohne Halt
    When der Drawdown zwischen 60 und 100 Prozent des Tages-Limits liegt
    Then ist die Bemessung reduziert, der Halt nicht gesetzt und keine Order gesendet
    And nach der Erholung unter 50 Prozent ist die Bemessung wieder normal

  @vc4
  Scenario Outline: Erholung
    Given der Breaker hat ausgeloest
    When das Eigenkapital sich erholt im Fall <fall>
    Then entspricht der Halt-Zustand der Referenz fuer <fall>
    And der Halt ist <halt>

    Examples:
      | fall     | halt         |
      | frei     | aufgehoben   |
      | gesperrt | gesetzt      |
      | nach_3h  | gesetzt      |
      | nach_5h  | gesetzt      |

  @vc4
  Scenario: Nach einem Portfolio-Stop gibt es keine Erholung
    Given der Portfolio-Stop hat ausgeloest
    When das Eigenkapital sich vollstaendig erholt
    Then ist der eigene Halt weiter gesetzt

  @vc4
  Scenario: Tagesgrenze neu setzen
    When reset_daily_limit mit neuem Eigenkapital laeuft
    Then sind Tages-Limit und Halt-Zustand wie in der Referenz

  @vc4
  Scenario: VIX unbestaetigt
    When evaluate_new_trade ohne bestaetigten VIX laeuft
    Then wird ein Kauf abgelehnt und ein Verkauf freigegeben

  @vc4
  Scenario: KI-Regel blockiert
    Given eine aktive Regel block_trade passt auf den Trade
    When evaluate_new_trade laeuft
    Then ist der Trade abgelehnt
    And der Span "risk.evaluate_trade" traegt symbol, trade.side, risk.approved und risk.reason wie in der Referenz

  @vc4
  Scenario: Halt-Ausgang
    Given der eigene Halt ist gesetzt
    When evaluate_new_trade laeuft
    Then wird der Trade ohne Span abgelehnt

  @vc4
  Scenario: Der globale Halt bleibt unberuehrt
    When alle Szenarien gelaufen sind
    Then wurde der globale Kill-Switch weder ausgeloest noch aufgehoben

  @vc4
  Scenario: Das Netz ist deterministisch
    When alle Szenarien dreimal hintereinander laufen
    Then sind die drei Messungen bitgleich

  @vc4
  Scenario: Eine veraenderte Liquidation macht das Netz rot
    Given die gemessene Menge einer Breaker-Order weicht um ein Stueck ab
    When das Netz gegen die Referenz vergleicht
    Then meldet es einen Befund

  @vc4
  Scenario: Ein veraenderter Halt macht das Netz rot
    Given der gemessene Halt-Zustand nach dem Tages-Limit ist "nicht gehalten"
    When das Netz gegen die Referenz vergleicht
    Then meldet es einen Befund
