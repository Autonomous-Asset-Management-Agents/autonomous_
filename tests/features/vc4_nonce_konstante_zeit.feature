Feature: Wiederholungssperre beim Scharfschalten

  Scenario: Eine benutzte Nonce wird abgelehnt, unabhaengig von der Kettengroesse
    Given eine Nonce steht als live_enablement in der Kette
    When der Bediener mit derselben Nonce scharfschalten will
    Then antwortet der Endpunkt mit 409
    And es wird kein zweiter WORM-Datensatz geschrieben

  Scenario: Eine neue Nonce wird angenommen
    Given eine Nonce steht nirgends in der Kette
    When der Bediener damit scharfschaltet
    Then wird der Vorgang angenommen

  Scenario: Der Aufwand waechst nicht mit der Kette
    Given die Kette enthaelt viele Eintraege vor dem Siegel
    When die Pruefung laeuft
    Then liest sie nur die Eintraege hinter dem Siegel
    And die Zahl gelesener Zeilen ist unabhaengig von der Gesamtgroesse

  Scenario: Ein gebrochener Anker wird nicht geglaubt
    Given das Siegel nennt einen Hash, der in der Kette nicht mehr an dieser Stelle steht
    When die Pruefung laeuft
    Then verwirft sie das Siegel und liest die ganze Kette
    And eine benutzte Nonce wird weiterhin abgelehnt

  Scenario: Ein fehlendes Siegel schwaecht die Sperre nicht
    Given es gibt kein Siegel
    When die Pruefung laeuft
    Then liest sie die ganze Kette
    And eine benutzte Nonce wird weiterhin abgelehnt

  Scenario: Das Siegel liegt nie in der Kette selbst
    Given das Siegel wird geschrieben
    When die Kette gelesen wird
    Then enthaelt keine audit_log-Datei einen Siegel-Eintrag
