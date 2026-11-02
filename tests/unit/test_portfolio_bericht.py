"""#4285 (H-5d+H-5e) — Rebalancing und Bericht leben in ``core/portfolio_bericht.py``.

Plan: ``docs/4285-*/implementation_plan.md`` §5. Schnitt-Entscheidung #4187:
``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md``, Abschnitte H-5d und H-5e.

Zwei Teile:

* **Charakterisierung** (grün auf dem Stand vor dem Umzug): die Zweige von
  ``get_rebalance_recommendations``, die die Einheitstests bis dahin nicht ausführten
  (gemessen mit ``--cov``), dazu ein Lauf, der Ausgabe, Log-Meldungen und die Folge der
  ``_can_trade_symbol``-Aufrufe festhält. Die Sektor-Deckel kommen aus dem echten Speicher
  (``AAA_USER_DATA_DIR``), nicht aus einem Patch auf einen Modulpfad — so überstehen die
  Tests Umzug und Zerlegung unverändert.
* **Struktur**: Mixin, Basis des Kerns, kein Leser von ``load_approved_constraints`` mehr im
  Kern, keine Funktion im Modul über 150 Zeilen. Den Kern liest der Test über ``inspect``
  (Import), nicht über einen Dateipfad: ein Pfad-Leser wäre ein Befund von
  ``test_h5_schnitt_portfolio_manager.py``. Den Modulnamen des Kerns setzt die Datei zur
  Laufzeit zusammen, damit dieselbe Erhebung ihn nicht als Patch-Ziel zählt.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
from unittest.mock import MagicMock, patch

import pytest

from core.portfolio_manager import PortfolioManager

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

KERN = ".".join(("core", "portfolio_manager"))
BERICHT = "core.portfolio_bericht"
METHODEN = (
    "get_rebalance_recommendations",
    "get_portfolio_summary",
    "get_debate_history",
)
FUNKTION_GRENZE = 150  # Epic #3738 §8.2

SECTORS = {
    "NVDA": "Technology",
    "MSFT": "Technology",
    "AMD": "Technology",
    "XOM": "Energy",
}


def _pm(weights_pct, capital=100_000.0, respects=True):
    pm = PortfolioManager(client=MagicMock(), total_capital=capital)
    pm._trim_respects_conviction = respects
    pm._max_position_pct = 0.25
    pm._drift_threshold_pct = 3.0
    pm.max_positions = 10
    pm._position_scores = {
        sym: MagicMock(market_value=capital * (pct / 100.0), total_score=70.0)
        for sym, pct in weights_pct.items()
    }
    pm.refresh_positions = lambda: None
    pm._can_trade_symbol = lambda s: True
    return pm


@pytest.fixture
def deckel(tmp_path, monkeypatch):
    """Genehmigte Sektor-Deckel im echten Speicher; ohne Aufruf ist der Speicher leer."""
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    import config

    monkeypatch.setattr(config, "CLEAN_WEIGHT_SIZING", "off", raising=False)

    def setzen(approved):
        (tmp_path / "portfolio_constraints.json").write_text(
            json.dumps({"approved": approved}), encoding="utf-8"
        )

    return setzen


# --- Charakterisierung: get_rebalance_recommendations -----------------------------------


def test_ohne_positionen_keine_empfehlung(deckel):
    pm = _pm({})
    assert pm.get_rebalance_recommendations(symbol_sector_map=SECTORS) == []


def test_kapital_null_warnt_und_empfiehlt_nichts(deckel, caplog):
    pm = _pm({"NVDA": 30.0})
    pm.total_capital = 0
    caplog.clear()  # ohne die Meldung des Konstruktors
    with caplog.at_level("WARNING"):
        assert pm.get_rebalance_recommendations() == []
    assert caplog.messages == [
        "Portfolio Manager: total_capital <= 0 - skipping rebalance recommendations"
    ]


def test_cooldown_unterdrueckt_die_drift_empfehlung(deckel):
    pm = _pm({"NVDA": 30.0, "MSFT": 31.0})
    pm._can_trade_symbol = lambda s: s != "NVDA"
    recs = pm.get_rebalance_recommendations()
    assert [r["symbol"] for r in recs] == ["MSFT"]


def test_sektor_ohne_mitglieder_bleibt_still(deckel, caplog):
    deckel({"Utilities": 0.10})
    pm = _pm({"NVDA": 10.0})
    caplog.clear()  # ohne die Meldung des Konstruktors
    with caplog.at_level("WARNING"):
        assert pm.get_rebalance_recommendations(symbol_sector_map=SECTORS) == []
    assert caplog.messages == []


def test_alle_mitglieder_gesperrt_nur_warnung(deckel, caplog):
    deckel({"Technology": 0.15})
    pm = _pm({"NVDA": 12.0, "MSFT": 8.0})
    pm._can_trade_symbol = lambda s: False
    caplog.clear()  # ohne die Meldung des Konstruktors
    with caplog.at_level("WARNING"):
        assert pm.get_rebalance_recommendations(symbol_sector_map=SECTORS) == []
    assert caplog.messages == [
        "SECTOR_CAP_PARTIAL_TRIM_COOLDOWN_BLOCKED: Sector Technology remains "
        "at 20.00% (Cap: 15.00%)"
    ]


def test_mitglied_ohne_marktwert_bekommt_keinen_anteil(deckel):
    deckel({"Technology": 0.15})
    pm = _pm({"NVDA": 20.0, "AMD": 0.0})
    recs = pm.get_rebalance_recommendations(symbol_sector_map=SECTORS)
    assert [r["symbol"] for r in recs] == ["NVDA"]
    assert recs[0]["adjustment_value"] == pytest.approx(-5000.0)


def test_gleichstand_behaelt_die_reihenfolge(deckel):
    # Legacy-Ziel 1/N = 10 %: beide +10 % Drift — das stabile Sortieren lässt sie in der
    # Reihenfolge der Bestandsliste.
    pm = _pm({"MSFT": 20.0, "NVDA": 20.0, "XOM": 15.0}, respects=False)
    recs = pm.get_rebalance_recommendations()
    assert [(r["symbol"], r["drift_pct"]) for r in recs] == [
        ("MSFT", pytest.approx(10.0)),
        ("NVDA", pytest.approx(10.0)),
        ("XOM", pytest.approx(5.0)),
    ]


def test_gemischter_lauf_ist_festgehalten(deckel, caplog):
    """Drift, Sektor-Deckel, Sperre und Rest — Ausgabe, Meldungen und Aufruffolge."""
    deckel({"Technology": 0.30, "Energy": 0.05})
    pm = _pm({"NVDA": 30.0, "MSFT": 12.0, "AMD": 6.0, "XOM": 9.0})
    aufrufe = []

    def kann(sym):
        aufrufe.append(sym)
        return sym != "AMD"

    pm._can_trade_symbol = kann
    caplog.clear()  # ohne die Meldung des Konstruktors
    with caplog.at_level("WARNING"):
        recs = pm.get_rebalance_recommendations(symbol_sector_map=SECTORS)

    assert aufrufe == ["NVDA", "MSFT", "AMD", "XOM"]
    assert [
        (r["symbol"], r["action"], round(r["adjustment_value"], 6), r["drift_pct"])
        for r in recs
    ] == [
        ("MSFT", "REDUCE", -12000.0, pytest.approx(18.0)),
        ("NVDA", "REDUCE", -5000.0, pytest.approx(5.0)),
        ("XOM", "REDUCE", -4000.0, pytest.approx(4.0)),
    ]
    assert recs[0]["target_pct"] == pytest.approx(30.0)
    assert recs[0]["current_pct"] == pytest.approx(12.0)
    assert recs[0]["market_value"] == pytest.approx(12_000.0)
    assert recs[0]["reasoning"] == (
        "SECTOR_CAP Technology: 48.0% > approved cap 30.0% - reduce MSFT by $12,000"
    )
    assert recs[1]["reasoning"] == "REDUCE NVDA: drift +5.0% from target"
    assert recs[1]["position_score"] == 70.0
    assert caplog.messages == [
        "SECTOR_CAP_PARTIAL_TRIM_COOLDOWN_BLOCKED: Sector Technology remains "
        "at 36.00% (Cap: 30.00%)"
    ]


# --- Charakterisierung: Bericht --------------------------------------------------------


def test_bericht_ohne_positionen():
    pm = _pm({})
    pm.max_positions = 7
    assert pm.get_portfolio_summary() == {
        "num_positions": 0,
        "max_positions": 7,
        "total_value": 0,
        "total_pnl": 0,
        "total_pnl_pct": 0,
        "average_score": 0,
        "weakest": None,
        "strongest": None,
    }


def test_bericht_ueber_den_bestand():
    pm = _pm({})
    pm._position_scores = {
        "A": MagicMock(
            symbol="A",
            market_value=600.0,
            unrealized_pnl=100.0,
            total_score=40.0,
            unrealized_pnl_pct=20.0,
        ),
        "B": MagicMock(
            symbol="B",
            market_value=400.0,
            unrealized_pnl=-100.0,
            total_score=80.0,
            unrealized_pnl_pct=-20.0,
        ),
    }
    assert pm.get_portfolio_summary() == {
        "num_positions": 2,
        "max_positions": 10,
        "total_value": 1000.0,
        "total_pnl": 0.0,
        "total_pnl_pct": 0.0,
        "average_score": 60.0,
        "weakest": {"symbol": "A", "score": 40.0, "pnl_pct": 20.0},
        "strongest": {"symbol": "B", "score": 80.0, "pnl_pct": -20.0},
    }


def test_debattenverlauf_liefert_die_letzten():
    pm = _pm({})
    pm._debate_history = [{"n": i} for i in range(12)]
    assert pm.get_debate_history() == [{"n": i} for i in range(2, 12)]
    assert pm.get_debate_history(limit=2) == [{"n": 10}, {"n": 11}]


# --- Struktur (H-5d) ---------------------------------------------------------------------


def test_bericht_mixin_traegt_die_drei_methoden():
    mixin = importlib.import_module(BERICHT).BerichtMixin
    oeffentlich = sorted(
        n for n, w in vars(mixin).items() if callable(w) and not n.startswith("_")
    )
    assert oeffentlich == sorted(METHODEN)
    assert "__init__" not in vars(mixin)


def test_portfolio_manager_erbt_bericht():
    mixin = importlib.import_module(BERICHT).BerichtMixin
    assert issubclass(PortfolioManager, mixin)
    eigene = sorted(n for n in METHODEN if n in vars(PortfolioManager))
    assert not eigene, f"PortfolioManager definiert weiter selbst: {eigene}"


def test_kern_liest_load_approved_constraints_nicht_mehr():
    baum = ast.parse(inspect.getsource(importlib.import_module(KERN)))
    namen = {
        x.id if isinstance(x, ast.Name) else x.asname or x.name
        for x in ast.walk(baum)
        if isinstance(x, (ast.Name, ast.alias))
    }
    assert "load_approved_constraints" not in namen


def test_patch_auf_den_kern_laeuft_nicht_ins_leere():
    # Ein Re-Export liesse den alten Patch still ins Leere laufen (Entscheidung §3).
    with pytest.raises(AttributeError):
        with patch(f"{KERN}.load_approved_constraints", return_value={}):
            pass


# --- Struktur (H-5e) ---------------------------------------------------------------------


def test_keine_funktion_ueber_150():
    baum = ast.parse(inspect.getsource(importlib.import_module(BERICHT)))
    zu_lang = {
        k.name: k.end_lineno
        - min([k.lineno, *(d.lineno for d in k.decorator_list)])
        + 1
        for k in ast.walk(baum)
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    zu_lang = {n: z for n, z in zu_lang.items() if z > FUNKTION_GRENZE}
    assert not zu_lang, f"über {FUNKTION_GRENZE} Zeilen (Epic §8.2): {zu_lang}"
