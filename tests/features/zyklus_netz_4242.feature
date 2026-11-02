Feature: Zyklus-Netz der Handelsschleife (H-2a, #4242)
  Als Betreiber
  Moechte ich jeden Zweig der Handelsschleife in einem vollen Zyklus gefahren sehen
  Damit die Zerlegung H-2b bis H-2k kein Verhalten am Broker unbemerkt aendert

  @vc3
  Scenario: Offener Markt mit gehaltener Position
    Given eine Engine mit einer gehaltenen Position und offenem Markt
    And keine Methode der Handelsschleife ist gemockt
    When ein vollstaendiger Zyklus von live_trading_loop laeuft
    Then entsprechen die Aufrufe am Broker-Client der eingecheckten Referenz
    And Stop-Pflege, HWM-Fortschreibung, Ausstiegsrunde und Kontostand wurden ausgefuehrt

  @vc3
  Scenario: Markt geschlossen
    Given eine Engine bei geschlossenem Markt
    When ein vollstaendiger Zyklus von live_trading_loop laeuft
    Then liefen _markt_geschlossen_berichten und _run_closed_report_pass
    And entsprechen die Aufrufe am Broker-Client der eingecheckten Referenz

  @vc3
  Scenario: Shutdown an der Zyklusgrenze
    Given das Shutdown-Signal ist nach dem ersten Zyklus gesetzt
    When ein vollstaendiger Zyklus von live_trading_loop laeuft
    Then lief _perform_graceful_handover genau einmal
    And entsprechen die Aufrufe am Broker-Client der eingecheckten Referenz

  @vc3
  Scenario Outline: Flag-Pfad eines Zustandsschluessels
    Given das Flag <flag> ist eingeschaltet
    When ein vollstaendiger Zyklus von live_trading_loop laeuft
    Then entspricht der Zustand vor dem Round-Table-Graph der Referenz fuer <flag>

    Examples:
      | flag                                 |
      | REGIME_CONDITIONER_ENABLED           |
      | ROUND_TABLE_POSITION_CONTEXT_ENABLED |
      | VIXAWARE_IMPLIED_VOL_ENABLED         |
      | QUALITY_AGENT_ENABLED                |
      | UPSIDE_SKEW_AGENT_ENABLED            |

  @vc3
  Scenario: Das Netz ist deterministisch
    When alle Szenarien dreimal hintereinander laufen
    Then sind die drei Messungen bitgleich

  @vc3
  Scenario: Eine veraenderte Order macht das Netz rot
    Given die gemessene Menge einer Order weicht um eine Stueckzahl ab
    When das Netz gegen die Referenz vergleicht
    Then meldet es einen Befund und der Test schlaegt fehl
