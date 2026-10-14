@allure.label.feature:VC-3_Trading_&_Execution
@allure.label.story:Single_Writer_Lease
@chain @h4
Feature: Genau ein aktiver Schreiber je Konto
  Laufen zwei Engine-Instanzen auf demselben Konto, handelt nur eine.

  Heute ist das Szenario rot: Eine Schreibberechtigung je Konto gibt es nicht. Der
  einzige Lock im Order-Pfad greift je Nutzer UND Symbol fuer zwoelf Sekunden
  (order_executor.py:997, :1002) — zwei Zyklen, die dasselbe Symbol mehr als zwoelf
  Sekunden auseinander bearbeiten, laufen beide durch. Auf dem Desktop ist der Lock
  gar keiner: local_state_client.py:94-95 gibt bedingungslos True zurueck.

  Gruen wird das Szenario mit der Engine-Sperre aus #3390. Solange es rot ist, muss
  max-instances auf 1 stehen bleiben (#3385) — die beiden Einstellungen gehoeren
  zusammen betrachtet.

  Scenario: Von zwei Instanzen handelt nur eine
    Given zwei Engine-Instanzen auf demselben Konto
    When beide einen Zyklus starten
    Then setzt genau eine Instanz eine Order ab
    And der Broker haelt genau eine Order

  Scenario: Die Vorrichtung laeuft deterministisch
    Given zwei Engine-Instanzen auf demselben Konto
    When beide einen Zyklus starten und der Lauf wiederholt wird
    Then ist das Ergebnis bei Wiederholung identisch
