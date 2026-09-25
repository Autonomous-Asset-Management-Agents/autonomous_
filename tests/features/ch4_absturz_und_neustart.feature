@allure.label.feature:VC-3_Trading_&_Execution
@allure.label.story:CH-4_Crash_And_Restart
@chain @h4
Feature: CH-4 — Absturz und Neustart
  Stirbt der Prozess zwischen Absenden und Bestaetigung, darf beim Neustart keine
  zweite Order fuer dieselbe Entscheidung entstehen.

  Heute ist das Szenario rot, und zwar aus einem anderen Grund, als der Plan
  annahm. #3387 hat den Zufall aus dem Schluessel entfernt: der client_order_id
  wird aus der Entscheidung abgeleitet (order_executor.py:56-79), und der
  Wiederholungspfad benutzt next_attempt (:1113-1123). Rot ist es eine Ebene
  tiefer: cloud_logger.py:82 gibt JEDEM DecisionContext eine frische decision_id.
  Der Neustart faehrt einen neuen Zyklus, faellt eine neue Entscheidung, leitet
  daraus einen anderen Schluessel ab — und der Broker nimmt beide Orders an.

  Ein stabiler Schluessel nuetzt nichts, wenn die Entscheidung, aus der er stammt,
  den Neustart nicht ueberlebt. Gruen wird das Szenario darum erst mit der Outbox
  aus #3388, die den Intent samt seiner decision_id festschreibt, BEVOR gesendet
  wird.

  Scenario: Der Neustart erzeugt keine zweite Order fuer dieselbe Entscheidung
    Given ein Order-Intent, der bis zum Broker gelangt
    When der Prozess zwischen Absenden und Bestaetigung stirbt und neu startet
    Then haelt der Broker genau eine Order
    And beide Sendungen tragen denselben Idempotenz-Schluessel

  Scenario: Der Neustart kauft nicht ein zweites Mal nach einem Fill
    Given ein Order-Intent, der bis zum Broker gelangt
    When der Prozess nach dem Fill vor der Persistenz stirbt und neu startet
    Then haelt der Broker genau eine Order

  Scenario: Ein Tod vor dem Absenden hinterlaesst keine Order
    Given ein Order-Intent, der bis zum Broker gelangt
    When der Prozess vor dem Absenden stirbt und neu startet
    Then haelt der Broker genau eine Order
