"""Abnahme auf Epic-Ebene für #5169 — beim Anlegen rot (#3785).

Diese Datei ist vom Maker erzeugt. Sie beschreibt, woran man erkennt, dass das
Epic erfüllt ist, und nicht, wie es umgesetzt wird. Wer ein Sub-Issue umsetzt,
ersetzt hier die Schritte, die sein Teil betrifft, durch echte Prüfungen.

Der xfail-Marker fällt erst, wenn alle Schritte echt sind — bis dahin hält er
die Suite grün, ohne die offene Abnahme zu verstecken.
"""

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("../epic_5169.feature")

pytestmark = pytest.mark.xfail(
    reason="Epic #5169 ist nicht umgesetzt — die Abnahme ist offen.",
    strict=True,
)

_OFFEN = (
    "Schritt aus der Abnahme von #5169 ist noch nicht hinterlegt. "
    "Wer das zugehörige Sub-Issue umsetzt, ersetzt ihn durch eine "
    "echte Prüfung."
)


# --- Szenario 1: Alle Konten an einer Stelle ---


@given("Paper-Keys aus dem Setup, Live-Keys und ein verbundener " "Paper account 2")
def _schritt_pa2_keys():
    raise NotImplementedError(_OFFEN)


@when("der Betreiber Settings → System öffnet")
def _schritt_open_settings_system():
    raise NotImplementedError(_OFFEN)


@then(
    'zeigt "Trading keys" Paper account 1, Paper account 2, ' "Paper account 3 und Live"
)
def _schritt_trading_keys_all_accounts():
    raise NotImplementedError(_OFFEN)


@then("jedes verbundene Konto zeigt seine maskierte Kontonummer")
def _schritt_masked_account_number():
    raise NotImplementedError(_OFFEN)


# --- Szenario 2: Live-Nummer nach dem Speichern ---


@when("der Betreiber gültige Live-Keys prüft und speichert")
def _schritt_save_valid_live_keys():
    raise NotImplementedError(_OFFEN)


@then("zeigt der Bereich Live die maskierte Nummer des Live-Kontos")
def _schritt_live_shows_masked_account():
    raise NotImplementedError(_OFFEN)


@then("die volle Nummer erreicht die Console nicht")
def _schritt_full_number_not_in_console():
    raise NotImplementedError(_OFFEN)


# --- Szenario 3: Live-Keys aus der Zeit vor dem Update ---


@given("Live-Keys, die vor diesem Epic gespeichert wurden")
def _schritt_legacy_live_keys():
    raise NotImplementedError(_OFFEN)


@then(
    'zeigt der Bereich Live "—" und "Re-enter the live keys to show '
    'the account number."'
)
def _schritt_legacy_live_keys_hint():
    raise NotImplementedError(_OFFEN)


# --- Szenario 4: Engine aus ---


@given("die Haupt-Engine läuft nicht")
def _schritt_engine_stopped():
    raise NotImplementedError(_OFFEN)


@then('zeigt "Trading keys" alle Konten mit Nummer und gespeicherten Keys')
def _schritt_trading_keys_shows_stored():
    raise NotImplementedError(_OFFEN)


# --- Szenario 5: Strategien aus ---


@given("Strategien sind aus")
def _schritt_strategies_disabled():
    raise NotImplementedError(_OFFEN)


@then("sind Paper account 2 und 3 unter Trading keys sichtbar, " "aber nicht bedienbar")
def _schritt_pa2_pa3_disabled():
    raise NotImplementedError(_OFFEN)


@then('ein Link führt zu "Enable strategies" auf Trading → Strategies')
def _schritt_link_to_enable_strategies():
    raise NotImplementedError(_OFFEN)


# --- Szenario 6: Live-Wechsel aus dem Bereich Live ---


@given("die Haupt-Engine handelt Paper account 1 und Live-Keys sind " "gespeichert")
def _schritt_pa1_active_live_keys_stored():
    raise NotImplementedError(_OFFEN)


@when('der Betreiber im Bereich Live "ENABLE LIVE TRADING" wählt')
def _schritt_click_enable_live():
    raise NotImplementedError(_OFFEN)


@then("öffnet derselbe Freigabe-Dialog wie über die Seitenleiste")
def _schritt_same_consent_dialog():
    raise NotImplementedError(_OFFEN)


@then(
    "die Aktion trägt die kritische Pill wie das Referenz-Control in "
    "der Seitenleiste"
)
def _schritt_critical_pill():
    raise NotImplementedError(_OFFEN)


# --- Szenario 7: Zurück zu Paper ---


@given("die Haupt-Engine handelt Live")
def _schritt_engine_trades_live():
    raise NotImplementedError(_OFFEN)


@when('der Betreiber im Bereich Paper account 1 "Switch main engine ' 'to paper" wählt')
def _schritt_switch_to_paper():
    raise NotImplementedError(_OFFEN)


@then("läuft der heutige Wechsel zurück zu Paper")
def _schritt_runs_paper_switch():
    raise NotImplementedError(_OFFEN)


# --- Szenario 8: Strategie je Konto an einer Stelle ---


@when("der Betreiber Trading → Strategies öffnet")
def _schritt_open_trading_strategies():
    raise NotImplementedError(_OFFEN)


@then('zeigt "Strategy per account" eine Zeile je Konto (Live, Paper 1–3)')
def _schritt_strategy_per_account_rows():
    raise NotImplementedError(_OFFEN)


@then(
    "Live wird über den heutigen Prüfdialog zugewiesen, Paper über die "
    "Platz-Zuweisung"
)
def _schritt_assignment_dialogs():
    raise NotImplementedError(_OFFEN)


@then('Paper account 1 zeigt "Set in the setup"')
def _schritt_pa1_shows_setup():
    raise NotImplementedError(_OFFEN)


@then("es gibt keine zweite Karte für die Live-Zuweisung")
def _schritt_no_duplicate_live_card():
    raise NotImplementedError(_OFFEN)


# --- Szenario 9: Dialoge nach dem Design-Guide ---


@when("ein Dialog auf Trading → Strategies offen ist")
def _schritt_dialog_open_strategies():
    raise NotImplementedError(_OFFEN)


@then("tragen alle Knöpfe die .btn-Gestaltung der Konsole mit weißer Schrift")
def _schritt_btn_styling():
    raise NotImplementedError(_OFFEN)
