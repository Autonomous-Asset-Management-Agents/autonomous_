@allure.label.feature:VC-5_Truth_&_Reconciliation
@allure.label.story:CH-1_Record_Completeness
@chain @h4
Feature: CH-1 — zu jedem Fill ein Datensatz
  Jeder Fill beim Broker hat auf unserer Seite genau einen Datensatz, und der traegt
  die Broker-Order-ID.

  Heute ist das Szenario rot: Die Broker-Order-ID wird erst NACH der Rueckmeldung
  gesetzt (order_executor.py:1955-1958). Stirbt der Prozess davor, gibt es keinerlei
  Spur des Absendens — der Fill ist beim Broker eingetreten, und auf unserer Seite
  existiert kein Datensatz, der ihn kennt. Der Fill ist damit Geld, das sich bewegt
  hat, ohne dass unsere Buchhaltung davon weiss.

  Gruen wird das Szenario mit der Outbox aus #3388 (Festschreiben VOR dem Absenden)
  und dem Nachtragen aus dem Reconciler #3389.

  Scenario: Der Fill aus dem abgestuerzten Lauf bleibt unbekannt
    Given ein Order-Intent, der bis zum Broker gelangt
    When der Prozess zwischen Absenden und Bestaetigung stirbt und neu startet
    Then gibt es zu jeder Order des Brokers genau einen Datensatz auf unserer Seite

  Scenario: Auch ein Tod nach dem Fill laesst keine Luecke
    Given ein Order-Intent, der bis zum Broker gelangt
    When der Prozess nach dem Fill vor der Persistenz stirbt und neu startet
    Then gibt es zu jeder Order des Brokers genau einen Datensatz auf unserer Seite

  Scenario: Ohne Absturz ist die Buchhaltung vollstaendig
    Given ein Order-Intent, der bis zum Broker gelangt
    When der Lauf ohne Absturz durchlaeuft
    Then gibt es zu jeder Order des Brokers genau einen Datensatz auf unserer Seite
    And traegt jeder Datensatz auf unserer Seite eine Broker-Order-ID
