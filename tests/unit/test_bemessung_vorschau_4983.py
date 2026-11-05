"""#5005 (Epic #4983) — Wirkungsanzeige: Modellrechnung des Sizers vor dem Kauf.

Plan: docs/5005-wirkungsanzeige-zeigt-ziel-cash-je-platz-und-bin/implementation_plan.md

Die Frage vom 29.10. („warum unter 5 %?") war nur über den Decision-Log zu beantworten. Die
Vorschau ruft den echten Sizer mit Referenzpreis und ohne Einzeltitel-Signale und liest die Spur.
Sie rechnet nichts nach; deshalb prüfen diese Tests gegen den echten ``RiskManager``.
"""

from __future__ import annotations

import ast
import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core import bemessung_vorschau as bv
from core.risk_manager import RiskManager

pytestmark = [pytest.mark.unit, pytest.mark.vc4]

KAPITAL = 20_000.0


@pytest.fixture(autouse=True)
def _lage():
    """Zehn Plätze, keine Kelly-Kappe, Cash-Puffer 1 $, Maximum 25 %.

    ``patch.object`` statt ``monkeypatch``: Am ``config``-Modul laufen die Werte über das
    PEP-562-``__getattr__``. ``monkeypatch`` schriebe beim Aufräumen einen echten Modulwert
    zurück, der jede spätere Neuladung verdeckt; ``patch`` entfernt das Attribut wieder.
    """
    import config

    cfg = config.get_config()
    lage = {
        "CLEAN_WEIGHT_SIZING": "b",
        "ENABLE_DYNAMIC_SIZING": True,
        "FULL_UNIVERSE_TRADING_ENABLED": False,
        "MAX_POSITIONS": 10,
        "CASH_AWARE_SLOTS_ENABLED": True,
        "SIGNAL_BUY_THRESHOLD": 0.60,
        "MIN_POSITION_PERCENT": 0.05,
        "MAX_POSITION_PERCENT_SIZING": 0.30,
        "MAX_POSITION_PERCENT": 0.25,
        "MAX_TOTAL_EXPOSURE_PCT": 0.95,
        "KELLY_FRACTION_CAP": None,
        "RISK_CASH_BUFFER_USD": 1.0,
        "RISK_FORBID_LEVERAGE": True,
        "COMPLIANCE_MAX_ORDER_VALUE": 10_000.0,
    }
    wechsel = [
        patch.object(ziel, name, wert, create=True)
        for ziel in (config, cfg)
        for name, wert in lage.items()
    ]
    for w in wechsel:
        w.start()
    yield
    for w in reversed(wechsel):
        w.stop()


def _rm(cash: float = 50_000.0, positionen=()) -> RiskManager:
    client = MagicMock()
    client.get_account.return_value = SimpleNamespace(cash=str(cash))
    client.get_all_positions.return_value = list(positionen)
    uhr = MagicMock()
    uhr.now.return_value = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)
    return RiskManager(client=client, total_capital=KAPITAL, clock=uhr)


def _pm(gehalten: int = 0, bestaetigt: bool = True):
    return SimpleNamespace(
        _position_scores={f"S{i}": 1.0 for i in range(gehalten)},
        _last_refresh_ok=bestaetigt,
    )


def _vorschau(rm=None, pm=None):
    with patch("core.kill_switch.kill_switch") as ks:
        ks.is_halted.return_value = False
        return bv.bemessung_vorschau(rm or _rm(), pm or _pm())


class TestWirkungJeVerfahren:
    def test_b_cash_unter_dem_ziel_nennt_cash_als_deckel(self):
        """Abnahme: Cash je Platz unter 1/N, das Ziel bleibt 10 %, „cash" bindet."""
        ergebnis = _vorschau(_rm(cash=1_001.0))
        assert ergebnis["available"] is True
        assert ergebnis["sizing_mode"] == "b"
        assert ergebnis["target_weight"] == pytest.approx(0.10)
        assert ergebnis["target_value"] == pytest.approx(2_000.0)
        assert ergebnis["cash_per_slot"] == pytest.approx(100.0)
        assert ergebnis["slots"] == 10
        assert ergebnis["binding_limit"] == "cash"
        assert ergebnis["order_value"] == pytest.approx(100.0)

    def test_cash_je_platz_ist_der_wert_den_der_sizer_als_deckel_meldet(self):
        """Die Anzeige rechnet den Platz nicht anders als der Sizer."""
        ergebnis = _vorschau(_rm(cash=1_234.0), _pm(gehalten=3))
        assert ergebnis["binding_limit"] == "cash"
        assert ergebnis["binding_cap_value"] == pytest.approx(
            ergebnis["cash_per_slot"], abs=0.01
        )

    def test_a_genug_cash_bindet_nichts(self):
        import config

        with patch.object(config, "CLEAN_WEIGHT_SIZING", "a"):
            ergebnis = _vorschau()
        assert ergebnis["sizing_mode"] == "a"
        assert ergebnis["target_weight"] == pytest.approx(0.10)
        assert ergebnis["binding_limit"] is None
        assert ergebnis["order_value"] == pytest.approx(2_000.0)

    def test_off_ohne_einzeltitel_conviction_zeigt_das_minimum(self):
        import config

        with patch.object(config, "CLEAN_WEIGHT_SIZING", "off"):
            ergebnis = _vorschau()
        assert ergebnis["sizing_mode"] == "conviction"
        assert ergebnis["target_weight"] == pytest.approx(0.05)

    def test_b_ohne_einzeltitel_vol_ist_genau_eins_durch_n(self):
        """Die ehrliche Grenze aus Plan §7: ohne Vol-Signal nur 1/N."""
        with patch.object(bv._rm, "vol_targeting_scaler", return_value=0.5) as vol:
            ergebnis = _vorschau()
        vol.assert_called_once_with(None)
        assert ergebnis["target_weight"] == pytest.approx(0.05)
        assert ergebnis["sizing_mode"] == "b"

    def test_freie_plaetze_wie_im_orderpfad(self):
        """Gehaltene Titel aus dem PM-Stand, kein neuer Broker-Abruf für den Bestand."""
        assert _vorschau(_rm(), _pm(gehalten=8))["slots"] == 2

    def test_unbestaetigter_bestand_teilt_durch_den_buchdeckel(self):
        ergebnis = _vorschau(_rm(), _pm(gehalten=8, bestaetigt=False))
        assert ergebnis["slots"] == 10

    def test_exposure_deckel_kommt_aus_dem_sizer(self):
        rm = _rm(positionen=[{"market_value": 18_500.0}])
        ergebnis = _vorschau(rm)
        assert ergebnis["binding_limit"] == "total_exposure_cap"
        assert ergebnis["binding_cap_value"] == pytest.approx(500.0)


class TestNurLesend:
    def test_broker_sieht_nur_die_beiden_lesezugriffe(self):
        rm = _rm()
        _vorschau(rm)
        namen = {aufruf[0] for aufruf in rm.client.method_calls}
        assert namen == {"get_account", "get_all_positions"}

    def test_der_risk_manager_bleibt_unveraendert(self):
        rm = _rm(cash=1_001.0)
        vorher = {
            k: copy.copy(v)
            for k, v in vars(rm).items()
            if isinstance(v, (int, float, str, bool, list, dict, type(None)))
        }
        _vorschau(rm)
        nachher = {k: v for k, v in vars(rm).items() if k in vorher}
        assert nachher == vorher

    def test_keine_datei_und_kein_risiko_ereignis(self):
        """Echter Kill-Switch, kein ``open``: Jede Kette schriebe in eine Datei."""
        with (
            patch("builtins.open", side_effect=AssertionError("Schreibzugriff")),
            patch("core.risk_manager.cloud_log_risk_event") as ereignis,
        ):
            ergebnis = bv.bemessung_vorschau(_rm(), _pm())
        assert ergebnis["available"] is True
        ereignis.assert_not_called()

    def test_das_modul_kennt_weder_tor_noch_kette(self):
        baum = ast.parse(Path(bv.__file__).read_text(encoding="utf-8"))
        module = {n.module for n in ast.walk(baum) if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(baum) if isinstance(n, ast.Import) for a in n.names
        }
        assert module == {"__future__", "math", "typing", "config", "core"}


class TestFehlerfall:
    def test_ohne_risk_manager_nicht_verfuegbar(self):
        assert bv.bemessung_vorschau(None, None)["available"] is False

    def test_ohne_broker_nicht_verfuegbar_statt_cash_null(self):
        rm = _rm()
        rm.client = None
        ergebnis = bv.bemessung_vorschau(rm, _pm())
        assert ergebnis["available"] is False
        assert "binding_limit" not in ergebnis

    def test_halt_ist_nicht_verfuegbar_und_nennt_den_grund(self):
        rm = _rm()
        rm.trading_halted = True
        ergebnis = bv.bemessung_vorschau(rm, _pm())
        assert ergebnis == {"available": False, "zero_reason": "trading_halted"}

    def test_ohne_dynamische_bemessung_keine_modellrechnung(self):
        import config

        with patch.object(config, "ENABLE_DYNAMIC_SIZING", False):
            assert _vorschau()["available"] is False

    def test_kein_endlicher_kontostand_wird_nicht_gerechnet(self):
        with pytest.raises(ValueError):
            _vorschau(_rm(cash=float("nan")))


class TestRoute:
    def _rufe(self, engine):
        from core.engine.routes.health import sizing_preview_get

        with patch("core.engine.api_routes.engine", engine):
            return asyncio.run(sizing_preview_get(None))

    def test_liefert_die_vorschau_der_engine(self):
        engine = SimpleNamespace(
            live_risk_manager=_rm(cash=1_001.0),
            active_strategy=SimpleNamespace(portfolio_manager=_pm()),
        )
        with patch("core.kill_switch.kill_switch") as ks:
            ks.is_halted.return_value = False
            antwort = self._rufe(engine)
        assert antwort["status"] == "success"
        assert antwort["binding_limit"] == "cash"

    def test_engine_fehlt_liefert_neutrale_antwort(self):
        antwort = self._rufe(None)
        assert antwort["status"] == "success"
        assert antwort["available"] is False

    def test_brokerfehler_wird_warning_und_nie_500(self, caplog):
        rm = _rm()
        rm.client.get_account.side_effect = RuntimeError("broker weg")
        engine = SimpleNamespace(live_risk_manager=rm, active_strategy=None)
        with caplog.at_level("WARNING"):
            antwort = self._rufe(engine)
        assert antwort == {"status": "error", "available": False}
        assert any("sizing-preview" in r.getMessage() for r in caplog.records)
