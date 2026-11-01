"""#5071: Die Simulation rechnet den Round Table wie live, Teil 1 (Epic #4625, ADR-027).

Alles in ``tmp_path``, ohne Netz. Plan:
``docs/5071-simulation-rechnet-den-round-table-wie-live/implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.vc0]

_WAECHTER = "DrawdownGuardAgent"


# --- 1: Drift-Waechter gegen den echten Agenten ------------------------------------


def _drawdown_echt(closes):
    frame = pd.DataFrame(
        {"Close": closes}, index=pd.bdate_range("2026-01-01", periods=len(closes))
    )
    provider = MagicMock()
    provider.get_data.return_value = frame
    registry = SimpleNamespace(
        get_active=lambda: SimpleNamespace(data_provider=provider)
    )
    from core.round_table import agents

    state = {
        "symbol": "X",
        "current_time": "2026-06-01T20:00:00+00:00",
        "ohlc": {"high": None, "low": None, "close": closes[-1]},
    }
    with patch("core.agent_registry.get_global_registry", return_value=registry):
        return asyncio.run(agents.DrawdownGuardAgent().vote(state))


@pytest.mark.parametrize(
    "closes",
    [
        [100.0] * 5,
        [100.0, 99.0, 98.0, 97.0, 96.0],
        [100.0, 95.0, 93.0, 92.9, 92.9],
        [100.0, 80.0, 70.0, 60.0, 50.0],
        [500.0] + [100.0] * 29 + [90.0],  # der Gipfel faellt aus dem Fenster
        list(np.linspace(100.0, 130.0, 60)),
    ],
)
def test_drawdown_stimme_wie_agent(closes):
    from core.sim.stimmen_rechner import drawdown_stimme

    echt = _drawdown_echt(closes)

    assert drawdown_stimme(closes) == (echt.score, echt.vetoed)


def test_drawdown_stimme_unter_fuenf_bars_keine():
    from core.sim.stimmen_rechner import drawdown_stimme

    assert drawdown_stimme([100.0, 99.0, 98.0, 97.0]) is None


# --- 2: Der Rechner ----------------------------------------------------------------


def _kurse(korpus, symbol, tage=520, bis="2025-06-30", fall=0.0):
    idx = pd.bdate_range(end=bis, periods=tage)
    i = np.arange(tage)
    basis = 100.0 + (sum(map(ord, symbol)) % 50)
    close = basis * np.exp(0.0007 * i) * (1.0 + 0.03 * np.sin(i / 9.0))
    if fall:
        close[-1] = close[-2] * (1.0 - fall)
    pd.DataFrame({"close": close}, index=idx).to_parquet(korpus / f"{symbol}.parquet")


@pytest.fixture
def korpus(tmp_path):
    ordner = tmp_path / "korpus"
    ordner.mkdir()
    for symbol in ("SPY", "AAA", "BBB"):
        _kurse(ordner, symbol)
    return ordner


def _stimmen(korpus, tag, symbole=("AAA", "BBB")):
    from core.sim.stimmen_rechner import OfflineStimmenRechner

    dec, vot = OfflineStimmenRechner().berechne(korpus, tag, list(symbole))
    return {(r.symbol, r.agent): r for r in vot.merge(dec, on="did").itertuples()}


def test_rechner_traegt_waechter(korpus):
    from core.sim.stimmen_rechner import GEWICHTE

    stimmen = _stimmen(korpus, "2025-06-27")

    assert {a for _s, a in stimmen} == {"MomentumAgent", "TrendAgent", _WAECHTER}
    assert GEWICHTE[_WAECHTER] == 0.60


def test_rechner_veto_steht_in_der_stimme(tmp_path):
    ordner = tmp_path / "fall"
    ordner.mkdir()
    _kurse(ordner, "AAA", fall=0.2)

    stimme = _stimmen(ordner, "2025-06-30", ("AAA",))[("AAA", _WAECHTER)]

    assert bool(stimme.vetoed) is True
    assert stimme.weight == 0.60


def test_rechner_ohne_lstm_news_specialist(korpus):
    assert not {
        "LSTMSignalAgent",
        "NewsSentimentAgent",
        "SpecialistAlphaAgent",
        "RegimeDetectionAgent",
        "VIXAwareRiskAgent",
    } & {a for _s, a in _stimmen(korpus, "2025-06-27")}


# --- 3: Der Stand des Korpus -------------------------------------------------------


def _vorbereitet(ordner):
    from core.sim import vorbereitung
    from core.sim.stimmen_rechner import OfflineStimmenRechner

    rechner = OfflineStimmenRechner()
    assert vorbereitung.berechne_stimmen(
        ordner, ["AAA", "BBB"], rechner, stand=rechner.stand()
    )
    return rechner


def test_stand_wechsel_rechnet_neu(korpus, caplog):
    from core.sim.stimmen_rechner import STIMMEN_STAND, lies_stand, stand

    ziel = korpus / "stimmen"
    (ziel / "teile").mkdir(parents=True)
    pd.DataFrame(
        {
            "symbol": ["AAA"],
            "ts": ["2025-06-30T20:00:00+00:00"],
            "agent": ["MomentumAgent"],
            "score": [0.9],
            "weight": [0.45],
            "vetoed": [False],
        }
    ).to_parquet(ziel / "teile" / "2025-06-30.parquet")
    (ziel / "votes.parquet").write_bytes(b"alt")

    with caplog.at_level("WARNING", logger="core.sim.vorbereitung"):
        _vorbereitet(korpus)

    # Die alte Teil-Datei ueberlebt den Stand-Wechsel nicht.
    neu = pd.read_parquet(ziel / "teile" / "2025-06-30.parquet")
    assert _WAECHTER in set(neu["agent"])
    assert lies_stand(ziel) == stand()
    assert stand()["stimmen_stand"] == STIMMEN_STAND
    assert "Stand None" in caplog.text and str(stand()) in caplog.text


def test_gleicher_stand_setzt_fort(korpus):
    from core.sim import vorbereitung

    rechner = _vorbereitet(korpus)
    gezaehlt = MagicMock(wraps=rechner)

    assert vorbereitung.berechne_stimmen(
        korpus, ["AAA", "BBB"], gezaehlt, stand=rechner.stand()
    )

    gezaehlt.berechne.assert_not_called()


# --- 4: Der abgeschaltete Waechter --------------------------------------------------

_KAUF = {"MomentumAgent": (0.9, 0.45, False), _WAECHTER: (0.2, 0.60, True)}


def test_tausche_gewichte_null_hebt_veto_auf():
    from core.sim.stimmen_sim import tausche_gewichte

    aus = {v.agent_name: v for v in tausche_gewichte(_KAUF, {_WAECHTER: 0.0})}
    assert aus[_WAECHTER].vetoed is False

    an = {v.agent_name: v for v in tausche_gewichte(_KAUF, {"MomentumAgent": 0.5})}
    assert an[_WAECHTER].vetoed is True
    assert an[_WAECHTER].weight == 0.60


def test_aufgezeichnetes_gewicht_null_behaelt_veto():
    """Live-Protokolle stempeln den Waechter mit Gewicht 0.0 (#3084); das Veto galt trotzdem."""
    from core.sim.stimmen_sim import tausche_gewichte

    (stimme,) = tausche_gewichte({_WAECHTER: (0.2, 0.0, True)}, {})

    assert stimme.vetoed is True


# --- 5: Der Ausweis im Ergebnis -----------------------------------------------------


def test_jeder_agent_ist_eingeordnet():
    """Ein neuer Agent im Gremium faellt hier auf, statt still zu fehlen."""
    from core.round_table import agents
    from core.sim import vergleich as v
    from core.sim.stimmen_rechner import GEWICHTE

    namen = {type(a).__name__ for a in agents.ALL_AGENTS}

    assert set(v._STIMMEN) == set(GEWICHTE)
    assert not set(v._STIMMEN) & set(v._NICHT_RECHENBAR)
    assert set(v._STIMMEN) | set(v._NICHT_RECHENBAR) | {"LSTMSignalAgent"} == namen


def test_nicht_abgebildet_je_strategie():
    from core.sim import vergleich as v

    assert v.nicht_abgebildete_agenten({}) == [
        "NewsSentimentAgent",
        "RegimeDetectionAgent",
        "SpecialistAlphaAgent",
        "VIXAwareRiskAgent",
    ]
    assert v.nicht_abgebildete_agenten(
        {
            "NEWS_SENTIMENT_AGENT_ENABLED": "false",
            "SPECIALIST_ALPHA_WEIGHT": "0.0",
            "REGIME_DETECTION_AGENT_ENABLED": "false",
            "VIX_RISK_AGENT_ENABLED": "false",
            "RL_CONFIDENCE_WEIGHT": "0.2",
            "UPSIDE_SKEW_AGENT_ENABLED": "true",
            "VOLUME_CONFIRM_AGENT_ENABLED": "true",
            "QUALITY_AGENT_ENABLED": "true",
        }
    ) == ["RLConfidenceAgent", "UpsideSkewAgent"]


# --- 6: Determinismus und Trennung ----------------------------------------------------

#: Titel des Mess-Korpus; ``SIM_5071_TITEL=500`` misst die Zusage aus dem Plan (§2.5).
_TITEL = int(os.environ.get("SIM_5071_TITEL", "10"))
_MESS_TAGE, _MESS_ENDE = 784, "2026-10-30"
_ABBILD = Path(__file__).resolve().parents[2] / "data/financials_snapshot.json.gz"


def _mess_symbole():
    # Nur die Namen, damit die Zufallspfade dieselben sind wie in Plan §2.5.
    from core.report.financials_snapshot import load_snapshot

    return sorted(load_snapshot(str(_ABBILD)))[:_TITEL]


def _mess_korpus(ordner, symbole):
    """Geseedete Zufallspfade je Symbol; nur Mechanik, keine Aussage ueber Renditen."""
    ordner.mkdir()
    index = pd.bdate_range(end=_MESS_ENDE, periods=_MESS_TAGE)
    for symbol in ("SPY", *symbole):
        zufall = np.random.default_rng(zlib.crc32(symbol.encode()))
        close = 100.0 * np.exp(np.cumsum(zufall.normal(0.0004, 0.015, _MESS_TAGE)))
        pd.DataFrame({"close": close}, index=index).to_parquet(
            ordner / f"{symbol}.parquet"
        )
    return ordner


def _rechne(ordner, symbole):
    from core.sim import vorbereitung
    from core.sim.stimmen_rechner import OfflineStimmenRechner

    rechner = OfflineStimmenRechner()
    start = time.perf_counter()
    assert vorbereitung.berechne_stimmen(
        ordner, symbole, rechner, stand=rechner.stand()
    )
    return time.perf_counter() - start


def test_bitgleich(tmp_path):
    symbole = _mess_symbole()
    a, b = _mess_korpus(tmp_path / "a", symbole), _mess_korpus(tmp_path / "b", symbole)

    sekunden = [_rechne(o, symbole) for o in (a, b)]

    votes = pd.read_parquet(a / "stimmen" / "votes.parquet")
    wachen = votes[votes.agent == _WAECHTER]
    print(
        f"\n#5071 Phase stimmen: {sekunden[0]:.1f} s und {sekunden[1]:.1f} s, "
        f"{len(symbole)} Titel, {len(votes)} Zeilen, {votes.agent.nunique()} Agenten, "
        f"{int(wachen.vetoed.sum())} Vetos von {len(wachen)} Waechter-Zeilen"
    )
    hashes = [
        hashlib.sha256((o / "stimmen" / "votes.parquet").read_bytes()).hexdigest()
        for o in (a, b)
    ]
    print(f"#5071 SHA-256 votes.parquet: {hashes[0][:16]}")
    assert hashes[0] == hashes[1]


def test_waechter_trennt(tmp_path):
    from core.sim import vergleich as v
    from core.sim.stimmen_sim import (
        StimmenSimStrategie,
        StimmenStore,
        lade_kurse,
        simuliere,
    )

    symbole = _mess_symbole()
    ordner = _mess_korpus(tmp_path / "korpus", symbole)
    _rechne(ordner, symbole)
    stimmen = ordner / "stimmen"
    store = StimmenStore.from_verzeichnis(str(stimmen))
    kurse = lade_kurse(str(stimmen / "kurse.parquet"))

    def lauf(name, **werte):
        felder, _ = v.sim_strategie(name, werte, 100_000.0)
        strategie = StimmenSimStrategie(**felder)
        return simuliere(store, kurse, strategie, "2024-10-01", "2026-10-30")

    # Ohne Fundamentals wie #5071 gemessen: Mit Gewicht 0,25 aendert das Veto auf diesen zehn
    # Pfaden keine Entscheidung (#5076).
    an = lauf("an", FUNDAMENTALS_AGENT_ENABLED="false")
    aus = lauf(
        "aus",
        FUNDAMENTALS_AGENT_ENABLED="false",
        DRAWDOWN_GUARD_AGENT_ENABLED="false",
    )

    assert _WAECHTER in {a for t in store._stimmen.values() for a in t}
    gefuellt = [sum(o["status"] == "filled" for o in r.orders) for r in (an, aus)]
    print(f"\n#5071 an {gefuellt[0]} Orders, Endwert {an.ending_equity:.2f}")
    print(f"#5071 aus {gefuellt[1]} Orders, Endwert {aus.ending_equity:.2f}")
    assert min(gefuellt) > 0
    gruende = [{e["grund"] for e in r.entscheidungen} for r in (an, aus)]
    assert "agent_veto" in gruende[0] and "agent_veto" not in gruende[1]
    assert an.daily_equity != aus.daily_equity
