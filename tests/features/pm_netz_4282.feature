Feature: Netz fuer Zulassung, Verdraengung und Bericht (H-5a, #4282)
  Als Betreiber
  Moechte ich Zulassung, Verdraengung und Bericht des PortfolioManager gegen eine eingecheckte Referenz gefahren sehen
  Damit die Zerlegung H-5c bis H-5k kein Verhalten an Kauf und Verdraengungs-Verkauf unbemerkt aendert

  Background:
    Given ein echter PortfolioManager ueber einen Broker-Client mit festem Bestand
    And eine feste Sim-Uhr

  @vc3
  Scenario Outline: Zulassung entspricht der Referenz
    Given die Lage <fall>
    When should_open_new_position laeuft
    Then entsprechen erlaubt, Grund und zu schliessende Position der Referenz fuer <fall>
    And die neue Debattenzeile entspricht der Referenz fuer <fall>

    Examples:
      | fall                           |
      | slot_frei_kauf                 |
      | slot_frei_tageslimit           |
      | slot_frei_score_zu_niedrig     |
      | nachkauf_im_totband            |
      | voll_cooldown                  |
      | voll_debatte_gewonnen          |
      | voll_debatte_verloren          |
      | voll_mindesthaltedauer         |
      | voll_sitzungsdeckel            |
      | verdraengung_aus               |
      | refresh_gescheitert_buchdeckel |

  @vc3
  Scenario: Bericht ueber denselben Bestand
    When get_portfolio_summary, get_strongest_position und get_weakest_position laufen
    Then entsprechen die Rueckgaben der Referenz

  @vc3
  Scenario: Verkaufssignale und Kapital
    When Verkaufssignale gezaehlt, zurueckgesetzt und geloescht werden und update_total_capital laeuft
    Then entsprechen Zaehlerstaende, can_sell_position und total_capital der Referenz

  @vc3
  Scenario: Das Netz ist deterministisch
    When alle Szenarien dreimal hintereinander laufen
    Then sind die drei Messungen bitgleich

  @vc3
  Scenario: Eine veraenderte Zulassung macht das Netz rot
    Given das gemessene "erlaubt" im Fall voll_debatte_gewonnen ist falsch
    When das Netz gegen die Referenz vergleicht
    Then meldet es einen Befund

  @vc3
  Scenario: Eine veraenderte Verdraengung macht das Netz rot
    Given die gemessene zu schliessende Position im Fall voll_debatte_gewonnen ist eine andere
    When das Netz gegen die Referenz vergleicht
    Then meldet es einen Befund
