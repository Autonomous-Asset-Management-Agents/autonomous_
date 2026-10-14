Feature: ARC-E5.6 KPIs und Alarme je Value-Chain-Stufe

  Scenario: Jede Stufe hat eine Kennzahl
    Given ein abgeschlossener Lauf ueber alle Stufen der Wertschoepfungskette
    When die Messung ausgewertet wird
    Then traegt jede Stufe und jede Uebergabe mindestens eine Kennzahl

  Scenario: Die Kennzahl nennt ihre Stufe
    Given eine Kennzahl aus der Auswertung
    When sie gelesen wird
    Then ist ohne Zusatzwissen erkennbar, zu welcher Stufe und welcher Uebergabe sie gehoert

  Scenario: Eine stillgelegte Stufe faellt auf
    Given eine Stufe liefert ueber mehrere Zyklen kein Ergebnis mehr
    When die Auswertung laeuft
    Then schlaegt ein Alarm an und nennt die Stufe und den Zeitraum

  Scenario: Eine Kennzahl, die gar nicht gebildet wird, faellt ebenfalls auf
    Given eine Stufe liefert keinen Messwert, weil ihre Berechnung abgeschaltet ist
    When die Auswertung laeuft
    Then unterscheidet der Alarm "Wert nicht gebildet" von "Wert unterschritten"

  Scenario: Die bisherigen Zahlen bleiben unveraendert
    Given dieselben Eingaben vor und nach der Zusammenfuehrung
    When die vorhandenen Messpunkte gelesen werden
    Then liefern sie dieselben Werte wie zuvor

  Scenario: Zielwerte sind zunaechst nicht bindend
    Given eine Stufe unterschreitet ihren Zielwert
    When der Zyklus laeuft
    Then wird der Vorfall berichtet
    And der Zyklus wird nicht angehalten, solange der Owner-Entscheid aussteht

  Scenario: Die Messung kann den Handel nicht anhalten
    Given die Messschicht wirft einen Fehler
    When ein Zyklus laeuft
    Then laeuft der Zyklus unveraendert weiter
    And der Fehler wird als Warnung protokolliert
