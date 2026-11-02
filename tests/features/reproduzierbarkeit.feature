Feature: Reproduzierbarkeit der Simulation
  
  Um die Streuung von Ergebnissen zu eliminieren und eine verlaessliche Evidenzbasis zu schaffen,
  sollen identische Parameter auch zu identischen Resultaten fuehren.

  @chain @vc1 @vc2 @vc3
  Scenario: Reproduzierbarer Lauf
    Given derselbe Code, derselbe Korpus, dasselbe Fenster und derselbe Seed
    When die Simulation dreimal laeuft
    Then weichen Rendite und maximaler Rueckgang um weniger als 0.5 Prozentpunkte voneinander ab
