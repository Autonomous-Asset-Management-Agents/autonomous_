"""#4291 (H-5j+H-5k) — Bestand und Überzeugung leben in ``core/portfolio_bestand.py``.

Plan: ``docs/4291-*/implementation_plan.md`` §5. Schnitt-Entscheidung #4187:
``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md``, Abschnitte H-5j und H-5k.

Der Kern erbt die zehn Methoden von ``BestandMixin``. ``_holding_period_cfg`` und
``holding_period_score`` exportiert er wieder, weil ``test_holding_period_score_seam.py`` beide
über ``core.portfolio_manager`` importiert (Entscheidung §3). Den Kern liest der Test über
``inspect`` (Import), nicht über einen Dateipfad: ein Pfad-Leser wäre ein Befund von
``test_h5_schnitt_portfolio_manager.py``.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import inspect

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

BESTAND = "core.portfolio_bestand"
KERN = "core.portfolio_manager"
METHODEN = (
    "refresh_positions",
    "_calculate_position_scores",
    "_blend_conviction",
    "_conviction_target_pct",
    "clear_conviction",
    "set_live_consensus",
    "get_live_consensus",
    "update_position_conviction",
    "get_weakest_position",
    "get_strongest_position",
)
FUNKTIONEN = ("_holding_period_cfg", "holding_period_score")
SCHRITTE = (
    "_bestand_konto_abgleich",
    "_bestand_lot_uhr_aktiv",
    "_bestand_position_lesen",
    "_bestand_haltedauer",
    "_bestand_geschlossene_entfernen",
)
FUNKTION_GRENZE = 150  # Epic §8.2


def _laenge(knoten: ast.AST) -> int:
    """Zeilen ab Dekorator, wie ``regeln.py``."""
    start = min([knoten.lineno, *(d.lineno for d in knoten.decorator_list)])
    return knoten.end_lineno - start + 1


def _mixin_methoden() -> dict:
    baum = ast.parse(inspect.getsource(importlib.import_module(BESTAND)))
    (mixin,) = [
        k for k in baum.body if isinstance(k, ast.ClassDef) and k.name == "BestandMixin"
    ]
    return {k.name: k for k in mixin.body if isinstance(k, ast.FunctionDef)}


def test_bestand_lebt_in_portfolio_bestand():
    modul = importlib.import_module(BESTAND)
    mixin = modul.BestandMixin
    assert mixin.__module__ == BESTAND
    fehlt = [n for n in METHODEN if n not in vars(mixin)]
    assert not fehlt, f"BestandMixin definiert nicht selbst: {fehlt}"
    assert "__init__" not in vars(mixin)
    for name in FUNKTIONEN:
        assert getattr(modul, name).__module__ == BESTAND


def test_der_kern_erbt_den_bestand():
    mixin = importlib.import_module(BESTAND).BestandMixin
    kern = importlib.import_module(KERN).PortfolioManager
    assert mixin in kern.__mro__
    anders = [n for n in METHODEN if getattr(kern, n) is not getattr(mixin, n)]
    assert not anders, f"PortfolioManager löst nicht auf das Mixin auf: {anders}"


def test_der_kern_definiert_ihn_nicht_mehr():
    baum = ast.parse(inspect.getsource(importlib.import_module(KERN)))
    knoten = list(baum.body)
    for k in baum.body:
        if isinstance(k, ast.ClassDef) and k.name == "PortfolioManager":
            knoten += k.body
    definiert = {
        k.name for k in knoten if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    zwoelf = set(METHODEN) | set(FUNKTIONEN)
    assert not sorted(definiert & zwoelf), f"{KERN} definiert weiter selbst"


def test_re_export_ist_dasselbe_objekt():
    kern = importlib.import_module(KERN)
    bestand = importlib.import_module(BESTAND)
    for name in FUNKTIONEN:
        assert getattr(kern, name) is getattr(bestand, name), name


def test_freie_namen_sind_im_modul_gebunden():
    """Ein vergessener Import bräche als ``NameError`` — und den verschluckt der äußere
    ``except`` von ``refresh_positions``; sichtbar wäre nur ``_last_refresh_ok = False``.
    """
    modul = importlib.import_module(BESTAND)
    baum = ast.parse(inspect.getsource(modul))
    gebunden = set(dir(builtins)) | set(vars(modul))
    frei = set()
    for k in ast.walk(baum):
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # auch die Parameter eingebetteter lambdas (``key=lambda p: p.total_score``)
            lokal = {a.arg for a in ast.walk(k) if isinstance(a, ast.arg)}
            lokal |= {
                n.id
                for n in ast.walk(k)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
            }
            # späte Importe in den Rümpfen (#4187 §3) binden ihren Namen lokal
            lokal |= {
                (a.asname or a.name).split(".")[0]
                for n in ast.walk(k)
                if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in n.names
            }
            lokal |= {
                n.name
                for n in ast.walk(k)
                if isinstance(n, ast.ExceptHandler) and n.name
            }
            frei |= {
                n.id
                for n in ast.walk(k)
                if isinstance(n, ast.Name)
                and isinstance(n.ctx, ast.Load)
                and n.id not in lokal
            }
    assert not sorted(frei - gebunden), "freie Namen ohne Bindung im Modul"


def test_kein_import_aus_dem_kern():
    baum = ast.parse(inspect.getsource(importlib.import_module(BESTAND)))
    importiert = {n.module for n in ast.walk(baum) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(baum) if isinstance(n, ast.Import) for a in n.names
    }
    assert KERN not in importiert


# --- Netz für die Zerlegung (Plan §5 Schritt 1) ----------------------------------------------


def _position(symbol="AAPL"):
    return {
        "symbol": symbol,
        "qty": 10.0,
        "avg_entry_price": 100.0,
        "current_price": 101.0,
        "market_value": 1010.0,
        "unrealized_pl": 10.0,
    }


def _broker_pm():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from core.portfolio_manager import PortfolioManager

    client = MagicMock()
    client.get_account.return_value = SimpleNamespace(
        equity=100000.0, account_number="acct-1"
    )
    client.get_all_positions.return_value = []
    return PortfolioManager(client=client, total_capital=100000.0), client


def test_lot_uhr_rueckfall_warnt_und_gilt_als_aus(monkeypatch, caplog):
    """Die drei Anweisungen, die vor der Zerlegung kein Test ausführte (Entscheidung §4,
    68/71): Lässt sich ``ROTATION_MIN_HOLD_CURRENT_LOT`` nicht lesen, warnt der Abgleich (§5.6)
    und behandelt die Lot-Uhr als aus — ein Wiedereinstieg bekommt keinen Stempel."""
    import logging

    import config

    pm, client = _broker_pm()
    pm.refresh_positions()  # erster Abgleich (Rehydrierung)

    def _kaputt():
        raise RuntimeError("config kaputt")

    monkeypatch.setattr(config, "get_config", _kaputt)
    client.get_all_positions.return_value = [_position()]
    with caplog.at_level(logging.WARNING):
        pm.refresh_positions()  # AAPL abwesend → anwesend: bei Lot-Uhr AN gestempelt

    assert pm._last_refresh_ok is True
    assert "AAPL" in pm._position_scores
    assert pm._position_opened_at == {}
    assert any(
        r.levelno == logging.WARNING
        and "could not read ROTATION_MIN_HOLD_CURRENT_LOT" in r.getMessage()
        for r in caplog.records
    )


# --- H-5k: refresh_positions in benannten Schritten ------------------------------------------


def test_refresh_positions_ist_hoechstens_150_zeilen():
    methoden = _mixin_methoden()
    fehlt = [n for n in SCHRITTE if n not in methoden]
    assert not fehlt, f"Schritte fehlen: {fehlt}"
    zu_lang = {
        n: _laenge(methoden[n])
        for n in ("refresh_positions", *SCHRITTE)
        if _laenge(methoden[n]) > FUNKTION_GRENZE
    }
    assert not zu_lang, f"über {FUNKTION_GRENZE} Zeilen: {zu_lang}"


def test_refresh_positions_ruft_die_schritte_in_reihenfolge():
    """Die Reihenfolge der Seiteneffekte bleibt: Kontoabgleich, Broker-Abruf, Lot-Uhr; das
    Entfernen geschlossener Positionen nach der Schleife und vor dem Frische-Merker."""
    dirigent = _mixin_methoden()["refresh_positions"]
    zeile = {}
    for n in ast.walk(dirigent):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            zeile.setdefault(n.func.attr, n.lineno)
    for name in ("_bestand_konto_abgleich", "_bestand_lot_uhr_aktiv"):
        assert name in zeile, f"der Dirigent ruft {name} nicht"
    assert (
        zeile["_bestand_konto_abgleich"]
        < zeile["get_all_positions"]
        < zeile["_bestand_lot_uhr_aktiv"]
    )
    (schleife,) = [n for n in ast.walk(dirigent) if isinstance(n, ast.For)]
    frisch = [
        n.lineno
        for n in ast.walk(dirigent)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(z, ast.Attribute) and z.attr == "_last_refresh_ok"
            for z in n.targets
        )
        and isinstance(n.value, ast.Constant)
        and n.value.value is True
    ]
    assert frisch, "self._last_refresh_ok = True fehlt im Dirigenten"
    entfernen = zeile.get("_bestand_geschlossene_entfernen")
    assert entfernen and schleife.end_lineno < entfernen < frisch[0]


def test_signatur_und_rueckgabe_unveraendert():
    from typing import Dict, get_type_hints

    from core.portfolio_bestand import BestandMixin
    from core.portfolio_typen import PositionScore

    sig = inspect.signature(BestandMixin.refresh_positions)
    assert list(sig.parameters) == ["self"]
    hinweise = get_type_hints(BestandMixin.refresh_positions)
    assert hinweise["return"] == Dict[str, PositionScore]


def test_abruffehler_markiert_veraltet():
    pm, client = _broker_pm()
    client.get_all_positions.return_value = [_position()]
    pm.refresh_positions()
    vorher = dict(pm._position_scores)
    assert pm._last_refresh_ok is True

    client.get_all_positions.side_effect = RuntimeError("transient APIError")
    ergebnis = pm.refresh_positions()

    assert pm._last_refresh_ok is False
    assert pm._position_scores == vorher
    assert ergebnis is pm._position_scores


def test_equity_fehler_allein_markiert_nicht_veraltet():
    pm, client = _broker_pm()
    pm.total_capital = 123456.0
    client.get_account.side_effect = RuntimeError("equity flap")
    client.get_all_positions.return_value = [_position()]
    pm.refresh_positions()

    assert pm._last_refresh_ok is True
    assert pm.total_capital == 123456.0
    assert "AAPL" in pm._position_scores
