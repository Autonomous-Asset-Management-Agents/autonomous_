"""#4447 — Daily vol target: bestaetigt, geprueft, mit einer Grenze.

Vola-Ziel und Vola-Schalter gehoeren ins bestaetigte Register
(``core/trading_settings.py::REGISTRY``). Damit laufen sie ueber denselben Pfad wie
alle Handelseinstellungen (WORM-Eintrag -> setup.json -> Neustart), und es gibt genau
eine Grenze: Register, Struktur-Endpunkt und Engine-Klemme lesen sie aus derselben
Quelle.
"""

import logging

import pytest

from core import trading_settings as ts

# VC-2 Groesse: das Vola-Ziel skaliert die Positionsgroesse.
pytestmark = pytest.mark.vc2

_VOL = "VOL_TARGET_DAILY_VOL"
_SCHALTER = "VOL_TARGETING_SIZING_ENABLED"


class TestRegister:
    def test_beide_schluessel_stehen_im_register(self):
        assert ts.REGISTRY[_VOL].kind == "float"
        assert ts.REGISTRY[_SCHALTER].kind == "bool"

    def test_grenze_ist_0_007_bis_0_075(self):
        s = ts.REGISTRY[_VOL]
        assert (s.lo, s.hi) == (0.007, 0.075)

    def test_auslieferungswerte(self):
        assert ts.REGISTRY[_VOL].default == 0.015
        assert ts.REGISTRY[_SCHALTER].default is True

    def test_meta_liefert_die_grenze(self):
        meta = {m["key"]: m for m in ts.settings_meta()}
        assert meta[_VOL]["min"] == 0.007
        assert meta[_VOL]["max"] == 0.075
        assert meta[_VOL]["default"] == "0.0150"
        assert meta[_SCHALTER]["kind"] == "bool"

    def test_text_hat_vier_nachkommastellen(self):
        """0,0125 darf nicht auf 0,01 gerundet in der Kette landen."""
        assert ts.canonical_text(_VOL, 0.0125) == "0.0125"


class TestApplyUpdates:
    @pytest.mark.parametrize(
        "roh,erwartet",
        [("0.03", "0.0300"), ("0.5", "0.0750"), ("0.001", "0.0070"), (0.07, "0.0700")],
    )
    def test_klemmt_auf_die_grenze(self, roh, erwartet):
        assert ts.apply_updates({_VOL: roh})[_VOL] == erwartet

    def test_schalter_aus(self):
        assert ts.apply_updates({_SCHALTER: "false"})[_SCHALTER] == "false"

    def test_aenderung_wird_beschrieben(self):
        current = ts.apply_updates({_VOL: "0.015"})
        new = ts.apply_updates({_VOL: "0.03"})
        assert ts.describe_changes(current, new) == [
            {"key": _VOL, "from": "0.0150", "to": "0.0300"}
        ]


class TestEngineKlemme:
    """Ein Wert ausserhalb der Grenze in setup.json (Env) wirkt nie ungeprueft."""

    def test_zu_hoch_wird_geklemmt_mit_warning(self, monkeypatch, caplog):
        from settings import RuntimeConfigState

        monkeypatch.setenv(_VOL, "0.5")
        with caplog.at_level(logging.WARNING):
            cfg = RuntimeConfigState()
        assert cfg.VOL_TARGET_DAILY_VOL == 0.075
        assert any(_VOL in r.getMessage() for r in caplog.records)

    def test_zu_niedrig_wird_geklemmt(self, monkeypatch):
        from settings import RuntimeConfigState

        monkeypatch.setenv(_VOL, "0.0001")
        assert RuntimeConfigState().VOL_TARGET_DAILY_VOL == 0.007

    def test_wert_innerhalb_bleibt(self, monkeypatch):
        from settings import RuntimeConfigState

        monkeypatch.setenv(_VOL, "0.03")
        assert RuntimeConfigState().VOL_TARGET_DAILY_VOL == 0.03


class TestStruktur:
    def test_struktur_traegt_kein_vol_ziel_mehr(self):
        from core.portfolio_shape import ENGINE_KEYS, clamp_portfolio_shape

        assert "vol_target" not in ENGINE_KEYS
        assert "vol_target" not in clamp_portfolio_shape(positions=12)

    def test_struktur_anfrage_kennt_kein_vol_ziel(self):
        from core.contracts.portfolio import PortfolioShapeRequest

        assert "vol_target" not in PortfolioShapeRequest.model_fields

    def test_reset_ausnahmeliste_ohne_vol_schluessel(self):
        """Die Vola-Schluessel sind jetzt Register-Schluessel, keine Altlast mehr."""
        import inspect

        from core.engine.routes import steuerung

        quelle = inspect.getsource(steuerung.settings_deviation_ack)
        block = quelle.split("_LEGACY_SETUP_KEYS = frozenset(")[1].split(")")[0]
        assert _VOL not in block and _SCHALTER not in block
