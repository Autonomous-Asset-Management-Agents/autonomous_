"""Abnahme auf Epic-Ebene für #4983 (SIZE-UX) — scharf geschaltet in #5009.

Jede Probe fährt den Code ihres Sub-Issues. Ersetzt sind nur die Grenzen nach außen:
Engine-Auth, Broker-Client und Redis. Die Route, die WORM-Kette, Sizer, Bewertung,
Zulassung und der Mandanten-Orderpfad sind echt; das Geld läuft durch ``tests/unit/_geld_gate.py``.

**Neustart.** Die Route zeichnet nur auf, die Shell schreibt ``setup.json``, wirksam wird
der Wert beim nächsten Start (``steuerung.py::trading_settings_post``). ``_neustart`` steht
für diesen Weg und schreibt ``applied`` in die Laufzeit-Config. Dass die Shell den Key
überhaupt weitergibt, prüft der Schritt an der Liste in ``setup-manager.cjs``.

**Brücke ins Frontend.** Was nur die Console zeigt, prüft vitest. ``_bruecke`` verlangt,
dass der genannte Test dort als ``it(`` steht, also nicht übersprungen oder ``it.fails``
ist. Ob er grün ist, sagt der Vitest-Lauf; der BDD-Container hat kein Node (Muster
``test_epic_4625.py``). Die Feldtabelle selbst liest diese Datei direkt.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.unit import _geld_gate as netz

WURZEL = pathlib.Path(__file__).resolve().parents[4]
MESSUNG = WURZEL / "scripts" / "mess_einstell_ux.py"
SETUP_MANAGER = WURZEL / "desktop" / "electron" / "setup-manager.cjs"
VITEST_ABSCHNITT = WURZEL / "src" / "test" / "advancedTradingSection.test.tsx"
VITEST_DIALOG = WURZEL / "src" / "test" / "settingsApplyModal.test.tsx"
VERFAHREN = "CLEAN_WEIGHT_SIZING"
MINIMUM = "MIN_POSITION_PERCENT"
ORDERDECKEL = "COMPLIANCE_MAX_ORDER_VALUE"

scenarios("../epic_4983.feature")

pytestmark = [pytest.mark.vc4]

#: Die Lage aus der Abnahme. Der Vol-Faktor gibt dem Nachkauf einen echten Tilt, sonst wäre
#: „Totband-Ziel = Sizer-Ziel“ schon durch das Zielgewicht allein erfüllt.
LAGE_OFF = {
    VERFAHREN: "off",
    "SIGNAL_BUY_THRESHOLD": 0.60,
    MINIMUM: 0.05,
    "MAX_POSITION_PERCENT_SIZING": 0.30,
    "MAX_POSITION_PERCENT": 0.25,
    "VOL_TARGETING_SIZING_ENABLED": True,
    "VOL_TARGET_DAILY_VOL": 0.015,
    "VOL_SIZE_SCALER_LO": 0.5,
}
KAPITAL = 100_000.0
BESTAND = 1_000.0
VOL_PROGNOSE = 0.03  # das Doppelte des Ziels: Vol-Faktor 0,5
ZIEL_070 = 0.1125  # 5 % + 25 % × (0,70 − 0,60) / 0,40
ZIEL_070_MIT_TILT = ZIEL_070 * 0.5


@pytest.fixture(autouse=True)
def _event_loop_zuruecklassen():
    """``netz.fahre`` ruft ``asyncio.run``; das lässt die Loop auf ``None`` stehen
    (siehe ``test_epic_3738_verhalten.py``)."""
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture
def kontext() -> dict:
    return {}


@pytest.fixture
def pult(tmp_path, monkeypatch):
    """Paper im Verfahren ``b``: echte Routen und Kette in ``tmp_path``, Auth aus."""
    from fastapi.testclient import TestClient

    import config
    from core import hitl_gate
    from core.engine import api_routes
    from core.round_table.senate_log import LocalJSONAuditLogger

    log_dir = tmp_path / "senate"
    monkeypatch.setenv("SENATE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path / "udd"))
    echter = LocalJSONAuditLogger()
    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: echter)
    monkeypatch.setattr(config, "HITL_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "PAPER_TRADING", True, raising=False)
    monkeypatch.setattr(config.get_config(), VERFAHREN, "b")
    api_routes.app.dependency_overrides[api_routes.require_engine_key] = lambda: None
    try:
        yield SimpleNamespace(client=TestClient(api_routes.app), log_dir=log_dir)
    finally:
        api_routes.app.dependency_overrides.pop(api_routes.require_engine_key, None)


def _sende(pult, acknowledgment: str = "generisch bestaetigt", **einstellungen):
    return pult.client.post(
        "/api/trading-settings",
        json={
            "settings": einstellungen,
            "acknowledgment": acknowledgment,
            "nonce": f"abnahme-{uuid.uuid4()}",
        },
    )


def _wert(pult, key: str) -> str:
    antwort = pult.client.get("/api/trading-settings")
    assert antwort.status_code == 200, antwort.text
    return antwort.json()["settings"][key]


def _neustart(monkeypatch, antwort) -> None:
    """Steht für Shell und Neustart, siehe Moduldoku."""
    import config

    assert antwort.status_code == 201, antwort.text
    werte = antwort.json()["applied"]
    cfg = config.get_config()
    weitergegeben = SETUP_MANAGER.read_text(encoding="utf-8")
    for key in (c["key"] for c in antwort.json()["changes"]):
        assert f'"{key}"' in weitergegeben, f"{key} erreicht die Engine nie"
        monkeypatch.setattr(cfg, key, type(getattr(cfg, key))(werte[key]))


def _kette(log_dir: pathlib.Path) -> list[dict]:
    return [
        json.loads(zeile)
        for datei in sorted(log_dir.glob("audit_log_*.jsonl"))
        for zeile in datei.read_text(encoding="utf-8").splitlines()
        if zeile.strip()
    ]


def _letzter_wechsel(pult) -> dict:
    """Der letzte ``settings_change``, nachdem die ganze Kette nachgerechnet ist (dieselbe
    Rechnung wie ``test_epic_4625.py::_pruefe_kette``)."""
    zeilen = _kette(pult.log_dir)
    for vorher, jetzt in zip(zeilen, zeilen[1:]):
        assert jetzt["prev_hash"] == vorher["hash"], "Kette gebrochen"
    for zeile in zeilen:
        ohne_hash = {k: v for k, v in zeile.items() if k != "hash"}
        neu = hashlib.sha256(json.dumps(ohne_hash, sort_keys=True).encode()).hexdigest()
        assert neu == zeile["hash"], f"Hash stimmt nicht: {zeile['event_type']}"
    return [z for z in zeilen if z["event_type"] == "settings_change"][-1]


def _bruecke(datei: pathlib.Path, testname: str) -> None:
    quelle = datei.read_text(encoding="utf-8")
    assert re.search(
        r"\bit\(\s*[\"']" + re.escape(testname), quelle
    ), f"Brücke gebrochen: {testname!r} steht nicht als it( in {datei.name}"


def _feld(monkeypatch, label: str) -> dict[str, str]:
    """Das Feld mit diesem Label aus der Tabelle, gelesen vom Parser des Messwerkzeugs."""
    monkeypatch.syspath_prepend(str(MESSUNG.parent))
    import mess_einstell_ux

    felder = mess_einstell_ux.lies_felder(
        mess_einstell_ux.TABELLE.read_text(encoding="utf-8")
    )
    return next(f for f in felder.values() if f.get("label") == f'"{label}"')


def _gesperrt_in(monkeypatch, label: str) -> set[str]:
    return set(re.findall(r'"(\w+)"', _feld(monkeypatch, label).get("inertIn", "")))


def _kauf(konviktion: float, **werte):
    """Ein Szenario im Verfahren ``off``; ``netz.fahre`` trägt es durch den Mandantenpfad."""
    return netz.Szenario(
        "abnahme_4983",
        netz.KEIN_DECKEL,
        equity=KAPITAL,
        cash=1_000_000.0,
        konviktion=konviktion,
        config=LAGE_OFF,
        laufzeit={"MAX_POSITION_PERCENT_SIZING": 0.30},
        **werte,
    )


def _nachkauf(monkeypatch, lstm: float) -> dict:
    """Ein Nachkauf durch den Mandantenpfad, mit echter Bewertung und Zulassung.

    Das Geld-Gate ersetzt beide durch Mocks und setzt die LSTM-Vorhersage fest; hier liefern
    sie die Hälfte der Antwort. Die Ziele werden während des Laufs gelesen, ``netz.fahre``
    nimmt die Lage danach zurück.
    """
    from core.risk_manager import RiskManager

    lauf: dict = {}
    echter_pm, echtes_ereignis = netz._echter_pm, netz._ereignis
    echte_bemessung = RiskManager.calculate_position_size

    def pm_mit_zulassung(sz, client):
        pm = echter_pm(sz, client)
        del pm.score_opportunity, pm.should_open_new_position
        pm._conviction_ewma_enabled = True
        echte_zulassung = pm.should_open_new_position

        def zulassung(chance, *a, **k):
            lauf["chance"] = chance
            lauf["totband_ziel"] = pm._topup_target_pct(chance)
            lauf["totband_sperre"] = pm._topup_dead_band_reason(chance)
            return echte_zulassung(chance, *a, **k)

        pm.should_open_new_position = zulassung
        lauf["pm"] = pm
        return pm

    def ereignis(sz):
        event = echtes_ereignis(sz)
        event.decision_context.lstm_prediction = lstm
        # Die echte Bewertung vergleicht diese Werte; ungesetzt nimmt sie ihre Neutralwerte.
        for merkmal in ("rsi_14", "macd", "adx_14", "volatility_20d"):
            setattr(event.decision_context, merkmal, None)
        return event

    def bemessung_fang(rm_self, *args, **kwargs):
        menge = echte_bemessung(rm_self, *args, **kwargs)
        preis = kwargs.get("current_price", 100.0)
        lauf["sizer_wert"] = float(menge) * float(preis)
        return menge

    with monkeypatch.context() as m:
        m.setattr(netz, "_echter_pm", pm_mit_zulassung)
        m.setattr(netz, "_ereignis", ereignis)
        m.setattr(RiskManager, "calculate_position_size", bemessung_fang)
        lauf["ergebnis"] = netz.fahre(
            _kauf(0.70, bestand_gate=BESTAND, forecast_vol=VOL_PROGNOSE)
        )
    return lauf


# ── Szenario 1: wählbar und sichtbar (#4987) ─────────────────────────────────


@given('die Console-Seite Trading controls mit der Engine im Verfahren "b"')
def console_im_verfahren_b(pult):
    assert _wert(pult, VERFAHREN) == "b"


@then('zeigt der Bemessungsblock "b" als aktives Verfahren')
def block_zeigt_b(pult, monkeypatch):
    assert _wert(pult, VERFAHREN) == "b"
    assert '"off", "a", "b"' in _feld(monkeypatch, "Sizing method")["choices"]
    _bruecke(VITEST_ABSCHNITT, "M1: the active method comes from the engine")


@when('ich "Equal weight" wähle und bestätige')
def waehle_und_bestaetige(pult, kontext):
    _bruecke(VITEST_ABSCHNITT, "M8: the apply diff carries the chosen method")
    kontext["antwort"] = _sende(pult, **{VERFAHREN: "a"})


@then('liefert GET /api/trading-settings CLEAN_WEIGHT_SIZING = "a"')
def get_liefert_a(pult, kontext, monkeypatch):
    _neustart(monkeypatch, kontext["antwort"])
    assert _wert(pult, VERFAHREN) == "a"


@then("die Änderung steht auf der WORM-Audit-Kette")
def aenderung_auf_der_kette(pult):
    eintrag = _letzter_wechsel(pult)
    assert json.loads(eintrag["changes"]) == [
        {"key": VERFAHREN, "from": "b", "to": "a"}
    ]


# ── Szenario 2: erneute Bestätigung im Live-Betrieb (#4989) ──────────────────


@given('die Engine handelt live im Verfahren "b"')
def live_im_verfahren_b(pult, monkeypatch):
    import config

    monkeypatch.setattr(config, "PAPER_TRADING", False, raising=False)
    assert _wert(pult, VERFAHREN) == "b"


@when('ich "Equal weight" wähle und die Änderung anwende')
def waehle_und_wende_an(pult, kontext):
    kontext["kette_vorher"] = _kette(pult.log_dir)
    kontext["antwort"] = _sende(pult, **{VERFAHREN: "a"})


@then(
    "verlangt die Console eine erneute Live-Bestätigung, "
    "die das alte und das neue Verfahren nennt"
)
def verlangt_live_bestaetigung(kontext):
    from core.trading_settings import verfahrenswechsel_text

    antwort = kontext["antwort"]
    assert antwort.status_code == 409, antwort.text
    assert antwort.json()["code"] == "sizing_change_unconfirmed"
    assert antwort.json()["params"] == {"from": "b", "to": "a"}
    assert 'from "b" to "a"' in verfahrenswechsel_text("b", "a")
    _bruecke(VITEST_DIALOG, "M13: names the old and the new method")


@then('ohne Bestätigung bleibt CLEAN_WEIGHT_SIZING = "b"')
def ohne_bestaetigung_bleibt_b(pult, kontext):
    assert _wert(pult, VERFAHREN) == "b"
    assert _kette(pult.log_dir) == kontext["kette_vorher"]


@then("mit Bestätigung stehen Wechsel und Bestätigung auf der WORM-Audit-Kette")
def bestaetigung_auf_der_kette(pult):
    from core.trading_settings import verfahrenswechsel_text

    text = verfahrenswechsel_text("b", "a")
    antwort = _sende(pult, text, **{VERFAHREN: "a"})
    assert antwort.status_code == 201, antwort.text
    eintrag = _letzter_wechsel(pult)
    assert eintrag["acknowledgment"] == text
    assert json.loads(eintrag["changes"]) == [
        {"key": VERFAHREN, "from": "b", "to": "a"}
    ]


@then("im Paper-Betrieb gilt die normale Bestätigung der Änderung")
def paper_normale_bestaetigung(pult, monkeypatch):
    import config

    monkeypatch.setattr(config, "PAPER_TRADING", True, raising=False)
    antwort = _sende(pult, "normale Bestaetigung", **{VERFAHREN: "a"})
    assert antwort.status_code == 201, antwort.text
    assert _letzter_wechsel(pult)["acknowledgment"] == "normale Bestaetigung"
    _bruecke(VITEST_DIALOG, "M15: paper mode keeps the normal confirmation")


# ── Szenario 3: kein wirkungsloser Regler (#4987) ────────────────────────────


@given(parsers.re(r'das Verfahren "(?P<verfahren>off|a|b)"'))
def das_verfahren(kontext, verfahren):
    kontext["verfahren"] = verfahren


@then(
    'sind "Minimum position size" und "Sizing cap" gesperrt '
    "und nennen das Verfahren, in dem sie wirken"
)
def beide_gesperrt(kontext, monkeypatch):
    for label in ("Minimum position size", "Sizing cap"):
        gesperrt = _gesperrt_in(monkeypatch, label)
        assert kontext["verfahren"] in gesperrt, label
        assert "off" not in gesperrt, f"{label} wirkt in Conviction"
    _bruecke(VITEST_ABSCHNITT, "M2: in b, minimum and cap are locked and name")


@then('ist zusätzlich "Coverage sizing strength" gesperrt')
def coverage_gesperrt(kontext, monkeypatch):
    assert _gesperrt_in(monkeypatch, "Coverage sizing strength") == {
        kontext["verfahren"]
    }
    assert kontext["verfahren"] in _gesperrt_in(monkeypatch, "Minimum position size")
    _bruecke(VITEST_ABSCHNITT, "M3: in a, coverage is locked as well")


# ── Szenario 4: eine Quelle, die Round Table (#4991, #4993) ──────────────────


@given(
    "eine gehaltene Position mit Round-Table-Conviction 0,70 und LSTM-Vorhersage 0,0"
)
def gehaltene_position(kontext):
    assert kontext["verfahren"] == "off"


@when("ein Nachkauf geprüft und bemessen wird")
def nachkauf(kontext, monkeypatch):
    kontext["lauf"] = _nachkauf(monkeypatch, lstm=0.0)


@then("rechnen Sizer, Lückendeckel, Totband und Positions-EWMA mit 0,70")
def alle_rechnen_mit_070(kontext):
    lauf = kontext["lauf"]
    assert lauf["ergebnis"]["zielgewicht"] == pytest.approx(ZIEL_070)  # Sizer
    assert lauf["ergebnis"]["deckel"] == "topup_gap"
    ziel_wert = ZIEL_070_MIT_TILT * KAPITAL
    assert lauf["ergebnis"]["notional"] == pytest.approx(ziel_wert - BESTAND)
    assert lauf["chance"].bemessungs_conviction == 0.70
    assert lauf["chance"].model_confidence == 0.0
    assert lauf["totband_sperre"] is None
    assert lauf["pm"]._conviction_ewma["GATE"] == pytest.approx(0.70)


@then("das Totband-Ziel ist gleich dem Ziel, das der Sizer anwendet")
def totband_ist_sizer_ziel(kontext):
    lauf = kontext["lauf"]
    assert lauf["totband_ziel"] / 100.0 * KAPITAL == pytest.approx(lauf["sizer_wert"])


@then("eine Änderung der LSTM-Vorhersage ändert weder Ziel noch Ordergröße")
def lstm_aendert_nichts(kontext, monkeypatch):
    for lstm in (-1.0, 0.9):
        lauf = _nachkauf(monkeypatch, lstm=lstm)
        assert lauf["chance"].model_confidence == lstm
        assert lauf["ergebnis"] == kontext["lauf"]["ergebnis"]
        assert lauf["totband_ziel"] == kontext["lauf"]["totband_ziel"]


# ── Szenario 5: Spanne ab der Kaufschwelle (#4993) ───────────────────────────


@given(
    'das Verfahren "off", Kaufschwelle 0,60, Minimum 5 %, '
    "Sizing cap 30 %, Maximum 25 %"
)
def spanne_lage():
    assert LAGE_OFF["SIGNAL_BUY_THRESHOLD"] == 0.60


@when("ein Kauf mit Conviction 0,60 bemessen wird")
def kauf_060(kontext):
    kontext["ergebnis"] = netz.fahre(_kauf(0.60))


@then("ist das Ziel vor den Risiko-Tilts 5 %")
def ziel_5(kontext):
    assert kontext["ergebnis"]["zielgewicht"] == pytest.approx(0.05)
    assert kontext["ergebnis"]["notional"] == pytest.approx(5_000.0)


@when("ein Kauf mit Conviction 1,0 bemessen wird")
def kauf_100(kontext):
    kontext["ergebnis"] = netz.fahre(_kauf(1.0))


@then("ist das Ziel vor den Risiko-Tilts 25 %")
def ziel_25(kontext):
    assert kontext["ergebnis"]["zielgewicht"] == pytest.approx(0.25)


# ── Szenario 6: Werte in ihrer Einheit (#4999) ───────────────────────────────


@given("MIN_POSITION_PERCENT = 0.05 und COMPLIANCE_MAX_ORDER_VALUE = 10000")
def werte_gesetzt(pult, monkeypatch):
    import config

    monkeypatch.setattr(config.get_config(), MINIMUM, 0.05)
    monkeypatch.setattr(config.get_config(), ORDERDECKEL, 10_000.0)
    monkeypatch.setattr(config.get_config(), VERFAHREN, "off")  # sonst gesperrt


@then('zeigt die Console "5%" und "$10,000"')
def console_zeigt_einheiten(pult, monkeypatch):
    assert float(_wert(pult, MINIMUM)) == 0.05
    assert float(_wert(pult, ORDERDECKEL)) == 10_000.0
    assert _feld(monkeypatch, "Minimum position size")["unit"] == '"fraction"'
    assert _feld(monkeypatch, "Maximum order value")["unit"] == '"usd"'
    _bruecke(VITEST_ABSCHNITT, "U1: 0.05 reads 5% and 10000 reads $10,000")


@when("ich für Minimum position size 4 eingebe und bestätige")
def gebe_vier_ein(pult, kontext):
    _bruecke(VITEST_ABSCHNITT, "U4: 4 entered for the minimum in off is stored as 0.04")
    kontext["antwort"] = _sende(pult, **{MINIMUM: 0.04})


@then("speichert die Engine MIN_POSITION_PERCENT = 0.04")
def engine_speichert(pult, kontext, monkeypatch):
    _neustart(monkeypatch, kontext["antwort"])
    assert _wert(pult, MINIMUM) == kontext["antwort"].json()["applied"][MINIMUM]
    assert float(_wert(pult, MINIMUM)) == 0.04


# ── Szenario 7: der Default bleibt (#3392) ───────────────────────────────────


@given('das Verfahren "b" und unveränderte Einstellungen')
def default_b():
    from core.trading_settings import settings_meta

    meta = next(m for m in settings_meta() if m["key"] == VERFAHREN)
    assert meta["default"] == "b"


@when("das Geld-Gate läuft")
def geld_gate(kontext):
    kontext["ist"] = netz.messe_alle()


@then("sind alle Ordergrößen byte-identisch zum Stand vor dem Epic")
def ordergroessen_gleich(kontext):
    assert kontext["ist"], "Das Gate hat nichts gemessen"
    assert netz.befunde(netz.lade_referenz(), kontext["ist"]) == []


# ── Szenario 8: Zweck-Kennzahl (#4985) ───────────────────────────────────────


@when("python scripts/mess_einstell_ux.py läuft")
def messung(kontext):
    kontext["messungen"] = [
        subprocess.run(
            [sys.executable, str(MESSUNG), "--verfahren", verfahren],
            capture_output=True,
            text=True,
            timeout=60,
        )
        for verfahren in ("b", "a", "off")
    ]


@then("meldet es 0")
def meldet_null(kontext):
    for lauf in kontext["messungen"]:
        assert lauf.returncode == 0, lauf.stderr
        assert lauf.stdout.splitlines()[-1] == "0", lauf.stdout
