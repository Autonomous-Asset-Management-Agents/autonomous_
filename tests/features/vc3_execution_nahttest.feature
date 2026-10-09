Feature: VC3 Execution Nahttest
  Als Betreiber
  Moechte ich sicherstellen, dass genehmigte Signale unweigerlich an den Broker gesendet werden
  Damit Code-Fehler auf dem Weg zur Ausfuehrung sofort auffallen

  @vc3
  Scenario: Ein genehmigtes Signal erreicht die Absendestelle
    Given der Round Table hat ein BUY mit Konsens oberhalb der Kaufschwelle beschlossen
    And der Halt ist frei, das Tagesbudget offen, der Abgleich sauber
    When die Handelsschleife einen Zyklus ausfuehrt
    Then erreicht genau eine Order die Absendestelle
    And sie traegt eine ComplianceDecision mit Grund-Code

  @vc3
  Scenario: Ein unterbrochener Pfad ist rot, nicht still
    Given eine Aenderung laesst den Zyklus vor der Absendung abbrechen
    When der Nahttest laeuft
    Then schlaegt er fehl und nennt die Stufe, an der der Weg endet
