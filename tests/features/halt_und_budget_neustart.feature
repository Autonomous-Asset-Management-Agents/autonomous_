Feature: Halt und Tagesbudget ueberleben den harten Neustart
  Abnahme ARC-E2.11 (Issue 3487) fuer Epic 3367. Prueft Schritt 5 aus Issue 3449 an echten Prozessen: Ein Prozess
  loest einen Halt aus oder verbraucht Budget und stirbt hart; der neu gestartete Prozess muss
  beides wieder kennen. Ablage ist die echte des Desktops (SQLite unter AAA_USER_DATA_DIR).

  Scenario: Ein Sicherheits-Trip ueberlebt den harten Neustart
    Given ein Engine-Prozess hat einen Trip ausgeloest und ist hart gestorben
    When der Prozess neu startet und einen Einstieg versucht
    Then geht keine Order zum Broker
    And der Einstieg wurde wegen des Halts abgewiesen

  Scenario: Ein Halt ohne Trip-Satz bleibt beim Start stehen
    Given die Ablage traegt einen Halt ohne Trip-Satz
    When die Engine ihre Startpruefung durchlaeuft und einen Einstieg versucht
    Then geht keine Order zum Broker
    And der Einstieg wurde wegen des Halts abgewiesen

  Scenario: Das verbrauchte Tagesbudget ueberlebt den Neustart
    Given ein Engine-Prozess hat alle erlaubten Trades des Tages bis auf einen verbraucht
    When der Prozess neu startet und zwei Einstiege versucht
    Then geht genau eine Order zum Broker
    And der zweite Einstieg wurde wegen des Tagesbudgets abgewiesen
