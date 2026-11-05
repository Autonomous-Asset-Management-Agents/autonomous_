"""#5076: Fundamental-Stimmen im Korpus, Teil 1 (Epic #4625, ADR-027).

Alles in ``tmp_path``, ohne Netz. Plan:
``docs/5076-fundamental-stimmen-im-korpus-epic-abnahme-round/implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from tests.unit.test_5071_round_table_sim import _kurse, _mess_korpus, _rechne

pytestmark = [pytest.mark.vc0]


# --- 1: Drift-Waechter gegen die echten Agenten ------------------------------------

_LESER = "core.round_table.agenten._fundamentaldaten._read_pit_fundamentals"


def _vote(agent, fund, close=180.0):
    with patch(_LESER, return_value=fund):
        return asyncio.run(agent.vote({"symbol": "X", "ohlc": {"close": close}}))


@pytest.mark.parametrize("wachstum", [None, -0.1, 0.0, 0.05, 0.15, 0.4])
@pytest.mark.parametrize("marge", [None, -0.1, 0.0, 0.1, 0.2, 0.5])
def test_fundamentals_score_wie_agent(wachstum, marge):
    from core.round_table import agents
    from core.sim.fundament_stimmen import fundamentals_score

    fund = {"revenue_yoy": wachstum, "net_margin": marge}
    echt = _vote(agents.FundamentalsAgent(), fund)

    assert fundamentals_score(fund) == echt.score


def test_fundamentals_score_ohne_daten_wie_agent():
    from core.round_table import agents
    from core.sim.fundament_stimmen import fundamentals_score

    assert _vote(agents.FundamentalsAgent(), None).score is None
    assert fundamentals_score(None) is None


@pytest.mark.parametrize("eps", [None, 0, -2.0, 1.0, 5.0, 40.0])
@pytest.mark.parametrize("wachstum", [None, -0.2, 0.0, 0.1, 0.5, 2.0])
@pytest.mark.parametrize("schuld", [None, 0.5, 3.0])
def test_valuation_score_wie_agent(eps, wachstum, schuld):
    from core.report.financials_feed import _ratio
    from core.round_table import agents
    from core.sim.fundament_stimmen import valuation_score

    close = 180.0
    fund = {
        "eps_diluted": eps,
        "eps_yoy": wachstum,
        "total_liabilities": schuld,
        "total_equity": None if schuld is None else 1.0,
    }
    # Der Feed rechnet ``pe`` aus dem Kurs; der Spiegel bekommt den Kurs des Tages.
    fuer_agent = {**fund, "pe": _ratio(close, eps) if eps not in (None, 0) else None}
    try:
        erwartet = _vote(agents.ValuationAgent(), fuer_agent, close).score
    except TypeError:
        # Band „neither cheap nor stretched": ``_clamp(None)`` wirft, run_round_table
        # verwirft die Stimme (Plan §6 Punkt 5). Das Ergebnis ist keine Stimme.
        erwartet = None

    assert valuation_score(fund, close) == erwartet


# --- 2-3: Der Rechner --------------------------------------------------------------


def _einreichung(tag, jahr, faktor=1.0, eps=5.0):
    """Eine Jahreszeile im Feed-Format; ``faktor`` verschiebt Umsatz und Gewinn."""

    def posten(**werte):
        return {k: {"value": v} for k, v in werte.items()}

    return {
        "filing_date": tag,
        "timeframe": "annual",
        "fiscal_year": jahr,
        "financials": {
            "income_statement": posten(
                revenues=1000.0 * faktor,
                net_income_loss=250.0 * faktor,
                diluted_earnings_per_share=eps,
            ),
            "balance_sheet": posten(liabilities=800.0, equity=1200.0),
        },
    }


ABBILD = {
    "AAA": {
        "results": [
            _einreichung("2024-02-01", 2023, 1.0),
            _einreichung("2025-02-03", 2024, 1.3),
        ]
    },
    "CCC": {
        "results": [
            _einreichung("2024-02-01", 2023, 1.0),
            _einreichung("2025-02-03", 2024, 0.5),
        ]
    },
}


def _schreibe_abbild(pfad, inhalt=None):
    with gzip.open(pfad, "wt", encoding="utf-8") as f:
        json.dump(ABBILD if inhalt is None else inhalt, f)
    return pfad


def _rechner(pfad):
    from core.sim.stimmen_rechner import OfflineStimmenRechner

    return OfflineStimmenRechner(abbild=pfad)


@pytest.fixture
def korpus(tmp_path):
    ordner = tmp_path / "korpus"
    ordner.mkdir()
    for symbol in ("SPY", "AAA", "CCC"):
        _kurse(ordner, symbol)
    return ordner


def _stimmen(rechner, korpus, tag, symbole=("AAA", "CCC")):
    dec, vot = rechner.berechne(korpus, tag, list(symbole))
    return {(r.symbol, r.agent): r for r in vot.merge(dec, on="did").itertuples()}


def _abbild_mit_gewinn(pfad, vorjahr, aktuell):
    """Ein Titel mit dieser Gewinn-je-Aktie-Folge; Verschuldung 0,67 aus ``_einreichung``."""
    zeilen = [
        _einreichung("2024-02-01", 2023, eps=vorjahr),
        _einreichung("2025-02-03", 2024, eps=aktuell),
    ]
    return _schreibe_abbild(pfad, {"AAA": {"results": zeilen}})


def test_rechner_traegt_fundamentals_und_valuation(tmp_path, korpus):
    # EPS 100 auf +100 % Gewinnwachstum bei einem Kurs um 150: PEG weit unter 1.
    # Umsatz flach (-0,2), Nettomarge 25 % (+0,2): Fundamentals bleibt bei 0,5.
    abbild = _abbild_mit_gewinn(tmp_path / "b.json.gz", 50.0, 100.0)

    stimmen = _stimmen(_rechner(abbild), korpus, "2025-06-27", ("AAA",))

    assert stimmen[("AAA", "FundamentalsAgent")].score == pytest.approx(0.5)
    assert stimmen[("AAA", "FundamentalsAgent")].weight == 0.25
    assert stimmen[("AAA", "ValuationAgent")].score == 0.7
    assert stimmen[("AAA", "ValuationAgent")].weight == 0.10
    # Diese Zeilen tragen keinen vollen Piotroski: Quality enthaelt sich (#5083).
    assert not any(a == "QualityAgent" for _s, a in stimmen)


def test_rechner_valuation_zum_kurs_des_tages(tmp_path):
    ordner = tmp_path / "sprung"
    ordner.mkdir()
    index = pd.bdate_range(end="2025-06-30", periods=61)
    pd.DataFrame({"close": [20.0] * 60 + [200.0]}, index=index).to_parquet(
        ordner / "AAA.parquet"
    )
    # Gewinnwachstum 10 %, EPS 5,5: KGV 3,6 ergibt PEG 0,36, KGV 36 ergibt PEG 3,6.
    rechner = _rechner(_abbild_mit_gewinn(tmp_path / "s.json.gz", 5.0, 5.5))

    guenstig = _stimmen(rechner, ordner, "2025-06-27", ("AAA",))
    gestreckt = _stimmen(rechner, ordner, "2025-06-30", ("AAA",))

    assert guenstig[("AAA", "ValuationAgent")].score == 0.7
    assert gestreckt[("AAA", "ValuationAgent")].score == 0.3


def test_einreichung_nach_stichtag_unsichtbar(tmp_path, korpus):
    from core.report.financials_feed import derive_fundamentals
    from core.sim.fundament_stimmen import fundamentals_score

    rechner = _rechner(_schreibe_abbild(tmp_path / "a.json.gz"))

    for tag in ("2025-01-31", "2025-02-03"):
        stimmen = _stimmen(rechner, korpus, tag)
        for symbol in ("AAA", "CCC"):
            fund = derive_fundamentals(ABBILD[symbol]["results"], tag)
            score = stimmen[(symbol, "FundamentalsAgent")].score
            assert score == fundamentals_score(fund)
    # Die Einreichung vom 03.02.2025 aendert das Urteil erst ab diesem Tag.
    assert (
        _stimmen(rechner, korpus, "2025-01-31")[("CCC", "FundamentalsAgent")].score
        != _stimmen(rechner, korpus, "2025-02-03")[("CCC", "FundamentalsAgent")].score
    )
    # Vor der ersten Einreichung gibt es keine Fundamentalstimme.
    frueh = _stimmen(rechner, korpus, "2024-01-15")
    assert not {a for _s, a in frueh} & {"FundamentalsAgent", "ValuationAgent"}


def test_abbild_fehlt_exit_1(tmp_path, monkeypatch):
    from core.sim import vorbereitung
    from tests.unit.test_4634_datenvorbereitung import FakeClient

    monkeypatch.setattr(vorbereitung, "lade_keys", lambda: ("k", "s"))
    kaputt = tmp_path / "kaputt.json.gz"
    kaputt.write_bytes(b"kein gzip")

    for i, abbild in enumerate((tmp_path / "fehlt.json.gz", kaputt)):
        ordner = tmp_path / f"k{i}"
        rc = vorbereitung.main(
            ["--korpus", str(ordner), "--symbols", "AAPL"],
            client=FakeClient(),
            rechner=_rechner(abbild),
            heute=date(2026, 1, 12),
        )

        assert rc == vorbereitung.EXIT_FEHLER
        assert not (ordner / "stimmen" / "votes.parquet").exists()


# --- 4: Der Stand des Korpus -------------------------------------------------------


def _vorbereitet(ordner, abbild):
    from core.sim import vorbereitung

    rechner = _rechner(abbild)
    assert vorbereitung.berechne_stimmen(
        ordner, ["AAA", "CCC"], rechner, stand=rechner.stand()
    )
    return rechner


def test_neues_abbild_rechnet_neu(tmp_path, korpus):
    ziel = korpus / "stimmen"
    _vorbereitet(korpus, _schreibe_abbild(tmp_path / "a.json.gz"))
    vorher = pd.read_parquet(ziel / "votes.parquet")

    anders = {"AAA": ABBILD["CCC"], "CCC": ABBILD["AAA"]}
    _vorbereitet(korpus, _schreibe_abbild(tmp_path / "b.json.gz", anders))

    assert not vorher.equals(pd.read_parquet(ziel / "votes.parquet"))


def test_abbild_hash_im_stand(tmp_path, monkeypatch, caplog):
    from core.sim import stimmen_rechner
    from core.sim import vergleich as v

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")
    ordner = tmp_path / v.KORPUS
    stimmen = ordner / "stimmen"
    stimmen.mkdir(parents=True)
    manifest = {"zeitraum": {"von": "2023-10-28", "bis": "2026-10-27"}}
    (ordner / "korpus.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name in ("decisions", "votes", "kurse"):
        (stimmen / f"{name}.parquet").write_bytes(b"")
    beginn, ende = date(2026, 1, 2), date(2026, 6, 30)

    def schreibe(stand):
        (stimmen / "stand.json").write_text(json.dumps(stand), encoding="utf-8")

    aktuell = stimmen_rechner.stand()
    assert (
        aktuell["abbild_sha256"]
        == hashlib.sha256(stimmen_rechner.ABBILD.read_bytes()).hexdigest()
    )
    schreibe(aktuell)
    v.pruefe_korpus(beginn, ende)

    schreibe({**aktuell, "abbild_sha256": "0" * 64})
    with pytest.raises(v.KeinKorpus):
        v.pruefe_korpus(beginn, ende)

    schreibe(aktuell)
    monkeypatch.setattr(stimmen_rechner, "ABBILD", tmp_path / "fehlt.json.gz")
    with caplog.at_level("WARNING", logger="core.sim.vergleich"):
        with pytest.raises(v.KeinKorpus):
            v.pruefe_korpus(beginn, ende)
    assert "Abbild" in caplog.text


# --- 5: Vergleich und Messung ---------------------------------------------------------


def test_sim_strategie_mappt_fundamentals_und_valuation():
    from core.sim import vergleich as v

    felder, nicht = v.sim_strategie(
        "A",
        {"FUNDAMENTALS_AGENT_WEIGHT": "0.5", "VALUATION_AGENT_WEIGHT": "0.2"},
        1000.0,
    )
    assert felder["gewichte"]["FundamentalsAgent"] == 0.5
    assert felder["gewichte"]["ValuationAgent"] == 0.2
    assert nicht == []

    aus, _ = v.sim_strategie("B", {"FUNDAMENTALS_AGENT_ENABLED": "false"}, 1000.0)
    assert aus["gewichte"]["FundamentalsAgent"] == 0.0


#: Titel des Mess-Korpus; ``SIM_5076_TITEL=500`` misst die Zusage aus dem Plan (§2.4).
_TITEL = int(os.environ.get("SIM_5076_TITEL", "10"))


def _mess_symbole():
    from core.report.financials_snapshot import load_snapshot
    from core.sim.stimmen_rechner import ABBILD as AUSGELIEFERT

    return sorted(load_snapshot(str(AUSGELIEFERT)))[:_TITEL]


@pytest.fixture(scope="module")
def mess_lauf(tmp_path_factory):
    symbole = _mess_symbole()
    ordner = _mess_korpus(tmp_path_factory.mktemp("mess") / "korpus", symbole)
    sekunden = _rechne(ordner, symbole)
    print(f"\n#5076 Phase stimmen: {sekunden:.1f} s, {len(symbole)} Titel")
    return ordner, symbole


def test_bitgleich(mess_lauf, tmp_path):
    ordner, symbole = mess_lauf
    zweiter = _mess_korpus(tmp_path / "zweiter", symbole)

    _rechne(zweiter, symbole)

    a, b = [
        hashlib.sha256((o / "stimmen" / "votes.parquet").read_bytes()).hexdigest()
        for o in (ordner, zweiter)
    ]
    print(f"\n#5076 SHA-256 votes.parquet: {a[:16]}")
    assert a == b


def test_kurven_trennen_und_orders(mess_lauf):
    from core.sim import vergleich as v
    from core.sim.stimmen_sim import (
        StimmenSimStrategie,
        StimmenStore,
        lade_kurse,
        simuliere,
    )

    stimmen = mess_lauf[0] / "stimmen"
    votes = pd.read_parquet(stimmen / "votes.parquet")
    print(f"\n#5076 {len(votes)} Zeilen: {votes.agent.value_counts().to_dict()}")
    assert {"FundamentalsAgent", "ValuationAgent"} <= set(votes.agent)
    store = StimmenStore.from_verzeichnis(str(stimmen))
    kurse = lade_kurse(str(stimmen / "kurse.parquet"))

    def lauf(**werte):
        felder, _ = v.sim_strategie("S", werte, 100_000.0)
        strategie = StimmenSimStrategie(**felder)
        r = simuliere(store, kurse, strategie, "2024-10-01", "2026-10-30")
        gefuellt = sum(o["status"] == "filled" for o in r.orders)
        print(f"#5076 {werte}: {gefuellt} Orders, Endwert {r.ending_equity:.2f}")
        assert gefuellt > 0, werte
        return r.daily_equity

    null = lauf(FUNDAMENTALS_AGENT_WEIGHT="0.0")
    aus = lauf(FUNDAMENTALS_AGENT_ENABLED="false")
    vorgabe = lauf()
    lauf(FUNDAMENTALS_AGENT_WEIGHT="1.0")
    val = lauf(VALUATION_AGENT_WEIGHT="0.3")
    lauf(VALUATION_AGENT_WEIGHT="0.6")

    # Schalter aus heisst Gewicht 0 (``sim_strategie``); Vorgabe ist Valuation 0.0.
    assert null == aus
    assert null != vorgabe
    assert vorgabe != val
