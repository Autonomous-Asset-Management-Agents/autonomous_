@allure.label.feature:VC-3_Trading_&_Execution
@allure.label.story:CH-3_Protection_Under_Halt
@chain @h3
Feature: CH-3 — Schutz bei Stoerung
  Der Schutz einer offenen Position ueberlebt den Ausfall der Engine und den Halt.

  Heute ist das Szenario rot, und zwar aus dem richtigen Grund: im Kern liegt keine
  einzige Stop-Order beim Broker. Kontrollgrep StopOrderRequest ueber ai_trading_bot/
  liefert keinen Treffer, MarketOrderRequest dagegen sechzehn. Der Stop ist heute die
  Meinung eines laufenden Python-Prozesses, kein Auftrag beim Broker.

  Scenario: Der Broker fuehrt den hinterlegten Stop aus, ohne dass die Engine laeuft
    Given eine offene Position mit hinterlegtem Schutz-Stop
    When die Engine nicht laeuft und der Kurs die Stop-Schwelle erreicht
    Then fuehrt der Broker den Stop aus
    And die Position ist geschlossen

  Scenario: Stop-Pflege laeuft auch im Halt
    Given eine offene Position und ein ausgeloester Kill-Switch
    When der Zyklus laeuft
    Then wird der Schutz der Position weiter gepflegt
    And es entsteht kein neuer Einstieg
