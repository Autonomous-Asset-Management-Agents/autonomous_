"""#4985 (SIZE-UX Teil 1) — die Zweck-Kennzahl wird gezaehlt, nicht von Hand geschaetzt.

Plan: ``docs/4985-mess-einstell-ux-py-zaehlt-die-zweck-kennzahl-of/implementation_plan.md``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
SKRIPT = _REPO / "scripts" / "mess_einstell_ux.py"

pytestmark = pytest.mark.vc0

#: #4999: die Einheit je Feld, unabhaengig von der Tabelle. `fraction` ist gespeichert ein
#: Anteil (0.05), `percent` schon Prozent (-6.0), `lossPercent` ein Abstand ohne Minus (7.0).
_UNIT_REFERENZ = {
    "ROTATION_PANEL_MAX_AGE_DAYS": "days",
    "LOSS_CUT_PCT": "percent",
    "LOSS_WATCH_PCT": "percent",
    "LOSS_ESCALATION_PCT": "percent",
    "STOP_LOSS_PCT": "lossPercent",
    "MIN_POSITION_PERCENT": "fraction",
    "MAX_POSITION_PERCENT": "fraction",
    "MAX_POSITION_PERCENT_SIZING": "fraction",
    "MAX_TOTAL_EXPOSURE_PCT": "fraction",
    "REGIME_RISKOFF_PERCENTILE": "percentile",
    "COMPLIANCE_MAX_ORDER_VALUE": "usd",
    "GLOBAL_BUY_COOLDOWN_MINUTES": "minutes",
    "NO_BUY_OPENING_MINUTES": "minutes",
    "REENTRY_LOCKOUT_DAYS": "tradingDays",
    "STOP_EXIT_SLOT_HOLD_DAYS": "tradingDays",
    "POSITION_TOPUP_DEAD_BAND_REL": "fraction",
}


@pytest.fixture
def messung():
    sys.path.insert(0, str(_REPO / "scripts"))
    try:
        import mess_einstell_ux

        yield mess_einstell_ux
    finally:
        sys.path.remove(str(_REPO / "scripts"))


def _text(messung) -> str:
    return messung.TABELLE.read_text(encoding="utf-8")


def _setze_ein(text: str, key: str, zeile: str) -> str:
    """Traegt `zeile` als Eigenschaft in das Feld `key` ein."""
    alt = f'key: "{key}",'
    assert alt in text
    return text.replace(alt, f"{alt} {zeile}", 1)


def _ohne_unit(text: str, key: str) -> str:
    """Streicht die Einheit des Felds `key`, wie vor #4999."""
    anfang = text.index(f'key: "{key}",')
    ende = text.index("},", anfang)
    feld = re.sub(r'\s*unit: "\w+",', "", text[anfang:ende], count=1)
    assert feld != text[anfang:ende]
    return text[:anfang] + feld + text[ende:]


def test_kennzahl_auf_der_echten_tabelle_ist_0():
    lauf = subprocess.run(
        [sys.executable, str(SKRIPT)], capture_output=True, text=True, cwd=_REPO
    )
    assert lauf.returncode == 0, lauf.stderr
    assert lauf.stdout.strip().splitlines()[-1] == "0"


def test_feld_ohne_unit_hebt_die_zahl_um_eins(messung):
    text = _ohne_unit(_text(messung), "LOSS_CUT_PCT")
    assert messung.zaehle(text) == (["LOSS_CUT_PCT"], [])
    assert messung.zaehle(_setze_ein(text, "LOSS_CUT_PCT", 'unit: "percent",')) == (
        [],
        [],
    )


@pytest.mark.parametrize("key", _UNIT_REFERENZ)
def test_unit_jedes_feldes_stimmt_mit_der_referenz_ueberein(messung, key):
    """#4999: Eine falsch deklarierte Einheit sieht die Zahl nicht, nur dieser Abgleich."""
    feld = messung.lies_felder(_text(messung))[key]
    assert feld["unit"] == f'"{_UNIT_REFERENZ[key]}"'


def test_unit_referenz_deckt_die_felder_mit_einheit(messung):
    """Ein Feld fiele sonst still aus dem Abgleich."""
    assert set(messung.FELDER_MIT_EINHEIT) == set(_UNIT_REFERENZ)


def test_inert_in_senkt_die_zahl_um_eins(messung):
    # Vor #5019 war MIN_POSITION_PERCENT wirkungslos. Ohne inertIn steigt die Zahl um eins:
    text = _text(messung).replace('inertIn: ["a", "b"],', "", 1)
    ohne_einheit, wirkungslos = messung.zaehle(text)
    assert len(ohne_einheit) + len(wirkungslos) == 1
    assert wirkungslos == ["MIN_POSITION_PERCENT"]


def _ohne_feld(text: str, key: str) -> str:
    """Nimmt das Feld `key` aus der Tabelle (der Key verschwindet aus dem Code)."""
    alt = f'key: "{key}",'
    assert alt in text
    return text.replace(alt, 'key: "UMBENANNT_IM_TEST",', 1)


def test_fehlender_key_endet_mit_exit_2_und_nennt_ihn(
    messung, tmp_path, monkeypatch, capsys
):
    tabelle = tmp_path / "tabelle.ts"
    tabelle.write_text(_ohne_feld(_text(messung), "LOSS_WATCH_PCT"), encoding="utf-8")
    monkeypatch.setattr(messung, "TABELLE", tabelle)
    assert messung.main([]) == 2
    assert "LOSS_WATCH_PCT" in capsys.readouterr().err


def test_key_in_umzuege_ist_kein_fehler_und_zaehlt_nicht(messung, monkeypatch):
    monkeypatch.setitem(messung.UMZUEGE, "LOSS_WATCH_PCT", "zieht in die Stops-Karte")
    text = _ohne_feld(_text(messung), "LOSS_WATCH_PCT")
    ohne_einheit, wirkungslos = messung.zaehle(text)
    assert len(ohne_einheit) + len(wirkungslos) == 0
    assert "LOSS_WATCH_PCT" not in ohne_einheit


def test_umzug_ohne_begruendung_zaehlt_nicht_als_umzug(messung, monkeypatch):
    monkeypatch.setitem(messung.UMZUEGE, "LOSS_WATCH_PCT", "  ")
    with pytest.raises(messung.MessAbbruch, match="LOSS_WATCH_PCT"):
        messung.zaehle(_ohne_feld(_text(messung), "LOSS_WATCH_PCT"))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "export const ANDERE_TABELLE = [];",
        "export const ADVANCED_GROUPS: AdvancedGroup[] = [];",
        "export const ADVANCED_GROUPS: AdvancedGroup[] = [{ fields: [{ key: 1 }] }];",
        "export const ADVANCED_GROUPS: AdvancedGroup[] = [{ fields: [{ ...BASIS }] }];",
        'export const ADVANCED_GROUPS: AdvancedGroup[] = [{ fields: [{ key: "A"',
    ],
)
def test_unbrauchbares_format_endet_mit_exit_2(
    messung, tmp_path, monkeypatch, capsys, text
):
    tabelle = tmp_path / "tabelle.ts"
    tabelle.write_text(text, encoding="utf-8")
    monkeypatch.setattr(messung, "TABELLE", tabelle)
    assert messung.main([]) == 2
    # Der Bruch selbst wird genannt, nicht als Folge eine Liste fehlender Keys.
    fehler = capsys.readouterr().err
    assert fehler and "Fehlt in der Tabelle" not in fehler


@pytest.mark.parametrize(
    "feld", ["{ key: NAME },", "{ ...BASIS },", '{ key: "X", ...BASIS },']
)
def test_unlesbares_feld_in_vollstaendiger_tabelle_ist_ein_abbruch(messung, feld):
    text = _text(messung).replace("fields: [", f"fields: [ {feld}", 1)
    with pytest.raises(messung.MessAbbruch):
        messung.zaehle(text)


def test_abgeschnittene_tabelle_ist_ein_abbruch(messung):
    text = _text(messung)
    with pytest.raises(messung.MessAbbruch):
        messung.zaehle(text[: text.rindex("];")])


def test_fehlende_tabelle_endet_mit_exit_2(messung, tmp_path, monkeypatch):
    monkeypatch.setattr(messung, "TABELLE", tmp_path / "gibt_es_nicht.ts")
    assert messung.main([]) == 2


def test_auskommentiertes_feld_gilt_als_verschwunden(messung):
    alt = 'key: "LOSS_WATCH_PCT",'
    text = _text(messung).replace(alt, f"// {alt}", 1)
    with pytest.raises(messung.MessAbbruch):
        messung.zaehle(text)


def test_unit_im_kommentar_oder_erklaertext_senkt_nichts(messung):
    text = _ohne_unit(_text(messung), "LOSS_CUT_PCT").replace(
        'label: "Loss cut",', '/* unit: "%", */ label: "unit: Loss cut",', 1
    )
    assert messung.zaehle(text) == (["LOSS_CUT_PCT"], [])


@pytest.mark.parametrize("wert", ['""', "undefined", "null"])
def test_leere_unit_senkt_nichts(messung, wert):
    text = _setze_ein(
        _ohne_unit(_text(messung), "LOSS_CUT_PCT"), "LOSS_CUT_PCT", f"unit: {wert},"
    )
    assert "LOSS_CUT_PCT" in messung.zaehle(text)[0]


def test_inert_in_fuer_ein_anderes_verfahren_senkt_nichts(messung):
    # Ohne inertIn fuer b taucht MIN_POSITION_PERCENT in b wieder als wirkungslos auf:
    text = _text(messung).replace('inertIn: ["a", "b"],', 'inertIn: ["a"],', 1)
    assert "MIN_POSITION_PERCENT" in messung.zaehle(text, "b")[1]
    assert "MIN_POSITION_PERCENT" not in messung.zaehle(text, "a")[1]


def test_4997_referenz_kennt_die_neuen_felder(messung):
    assert "POSITION_TOPUP_DEAD_BAND_REL" in messung.FELDER_MIT_EINHEIT
    assert "PROPORTIONAL_SLOT_CASH_ENABLED" in messung.OHNE_WIRKUNG["a"]
    assert "PROPORTIONAL_SLOT_CASH_ENABLED" in messung.OHNE_WIRKUNG["b"]
    assert "PROPORTIONAL_SLOT_CASH_ENABLED" not in messung.OHNE_WIRKUNG["off"]


@pytest.mark.parametrize("verfahren, zahl", [("off", 0), ("a", 0), ("b", 0)])
def test_zahl_je_verfahren(messung, capsys, verfahren, zahl):
    assert messung.main(["--verfahren", verfahren]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == str(zahl)
