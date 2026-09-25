Feature: CH-6 Bericht aus Datensaetzen

  Um Ergebnisse auf Einzelentscheidungen zurueckfuehren zu koennen,
  duerfen Kennzahlen im Bericht nicht neu aus Rohdaten berechnet werden.

  @evidence @h5 @vc5 @vc6
  Scenario: Jede Kennzahl hat eine Herkunft
    Given eine abgeschlossene Sitzung mit Entscheidungen, Orders und Fills
    When der Bericht erzeugt wird
    Then traegt jede Kennzahl eine Verweiskette auf decision_id und Broker-Order-ID

  @evidence @h5 @vc5 @vc6
  Scenario: Keine Neuberechnung beim Abruf
    Given derselbe abgeschlossene Zeitraum
    When der Bericht zweimal abgerufen wird
    Then liefert er beide Male dieselbe Zahl aus demselben Datensatz
    And es findet keine Berechnung aus Rohdaten statt

  @evidence @h5 @vc5 @vc6
  Scenario: Papier- und Live-Konto bleiben getrennt
    Given Datensaetze aus einem Papier- und einem Live-Abschnitt desselben Kontos
    When der Live-Bericht aus dem neuen Leseweg erzeugt wird
    Then enthaelt er ausschliesslich Live-Datensaetze
    And die Einzahlungsbereinigung wirkt wie bisher

  @evidence @h5 @vc5 @vc6
  Scenario: Ersatzquelle wird ausgewiesen
    Given die eigenen Datensaetze sind fuer einen Zeitraum nicht lesbar
    When der Bericht ausweicht und die Broker-Historie verwendet
    Then kennzeichnet er diese Zahlen als Zahlen aus einer Ersatzquelle
    And der Bericht bleibt lieferfaehig

  @evidence @h5 @vc5 @vc6
  Scenario: Alt und neu stimmen ueberein
    Given der bisherige Rechenweg und der neue Weg aus Datensaetzen laufen parallel
    When beide denselben Zeitraum auswerten
    Then weichen ihre Ergebnisse nicht voneinander ab
