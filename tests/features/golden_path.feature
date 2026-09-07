@allure.label.feature:VC-4_Risk_Management_&_Compliance
@allure.label.story:Golden_Path
Feature: Golden Path and Compliance
  As a regulated Trading Bot
  I want to process trades through the ComplianceGuardian, RiskManager, and SmartExit
  So that I can safely execute trades, take profits, cut losses, and prevent wash trades

  Scenario: STORY-01 Profitable Trade -- BUY -> HOLD -> TAKE-PROFIT
    Given the system has 100000 capital and VIX is 15
    When a BUY order for 5 shares of "AAPL" at 150.0 is placed
    Then the order is approved by compliance
    And the RiskManager allocates a position size greater than 0
    When the price of "AAPL" rises to 189.0 after 5 hours
    Then the SmartExit module triggers a "SELL" action with reason "Take-profit"

  Scenario: STORY-02 Stop-Loss bei Kursverlust
    Given a position in "AAPL" was entered at 150.0
    When the price of "AAPL" drops to 138.5 after 3 hours
    Then the SmartExit module triggers a "SELL" action with reason "Stop-loss"

  Scenario: STORY-03 Compliance blockiert Wash-Trade
    Given a BUY order for "AAPL" at 150.0 was just approved
    When a SELL order for "AAPL" at 150.0 is placed immediately
    Then the order is rejected by compliance
    And the daily trade count is not increased

  Scenario: STORY-04 Iron Dome -- Drawdown-Block
    Given the system has a peak daily equity of 100000.0 and a limit of 17500.0
    When the account equity drops to 82000.0
    Then the RiskManager halts trading
    And new orders receive a position size of 0.0
    When the daily limit is reset to 100000.0
    Then trading is resumed

  Scenario: STORY-05 Anti-Churn blockiert voreiligen Verkauf
    Given the system has 100000 capital and VIX is 15
    And a position in "AAPL" was entered at 150.0
    And the holding time is only 1 minute
    When a SELL signal is generated for "AAPL"
    Then the order is rejected by anti-churn
