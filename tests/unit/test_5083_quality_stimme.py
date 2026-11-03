"""#5083: QualityAgent in der Simulation, Teil 2 von #5076 (Epic #4625, ADR-027).

Alles in ``tmp_path``, ohne Netz. Plan:
``docs/5083-qualityagent-epic-abnahme-round-table-und-t29-te/implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import patch

import pandas as pd
import pytest

from tests.unit.test_5071_round_table_sim import _kurse, _mess_korpus, _rechne
from tests.unit.test_5076_fundament_stimmen import (
    _mess_symbole,
    _rechner,
    _schreibe_abbild,
    _stimmen,
)

pytestmark = [pytest.mark.vc0]


def _einreichung(tag, jahr, faktor=1.0):
    """Eine Jahreszeile mit allen Feldern fuer den vollen Piotroski und ``gross_profit``."""

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
                gross_profit=500.0 * faktor,
                diluted_earnings_per_share=5.0 * faktor,
                diluted_average_shares=100.0,
            ),
            "balance_sheet": posten(
                assets=2000.0,
                liabilities=800.0,
                equity=1200.0,
                current_assets=600.0,
                current_liabilities=300.0,
            ),
            "cash_flow_statement": posten(
                net_cash_flow_from_operating_activities=300.0 * faktor,
            ),
        },
    }


ABBILD = {
    "AAA": {
        "results": [
            _einreichung("2024-02-01", 2023, 1.0),
            _einreichung("2025-02-03", 2024, 1.3),
        ]
    },
    "BBB": {
        "results": [
            _einreichung("2024-02-01", 2023, 1.0),
            _einreichung("2025-02-03", 2024, 0.9),
        ]
    },
    "CCC": {
        "results": [
            _einreichung("2024-02-01", 2023, 1.0),
            _einreichung("2025-02-03", 2024, 0.5),
        ]
    },
}
SYMBOLE = ("AAA", "BBB", "CCC")


@pytest.fixture
def korpus(tmp_path):
    return _korpus_ordner(tmp_path / "korpus")


def _korpus_ordner(ordner):
    ordner.mkdir()
    for symbol in ("SPY", *SYMBOLE):
        _kurse(ordner, symbol)
    return ordner


# --- 1: Drift-Waechter gegen den echten Agenten -------------------------------------


def _vote(score, referenz):
    from core.round_table import agents
    from core.round_table.agenten import quality

    state = {"symbol": "X", "quality_score": score, "quality_reference": referenz}
    with patch.object(quality, "_quality_agent_enabled", return_value=True):
        return asyncio.run(agents.QualityAgent().vote(state))


def test_quality_stimme_wie_agent(tmp_path, korpus):
    from core.quality_score import composite_quality
    from core.report.financials_feed import derive_fundamentals

    pfad = _schreibe_abbild(tmp_path / "a.json.gz", ABBILD)
    tag, vortag = "2025-06-27", "2025-06-26"
    stimmen = _stimmen(_rechner(pfad), korpus, tag, SYMBOLE)

    def composite(symbol, stichtag):
        fund = derive_fundamentals(ABBILD[symbol]["results"], stichtag)
        return composite_quality(fund)

    referenz = [composite(s, vortag) for s in SYMBOLE]
    for symbol in SYMBOLE:
        echt = _vote(composite(symbol, tag), referenz)
        assert stimmen[(symbol, "QualityAgent")].score == echt.score


def test_quality_enthaelt_sich_unter_zwei_distinkten_werten(tmp_path, korpus):
    gleich = {s: {"results": ABBILD["AAA"]["results"]} for s in SYMBOLE}
    pfad = _schreibe_abbild(tmp_path / "g.json.gz", gleich)

    stimmen = _stimmen(_rechner(pfad), korpus, "2025-06-27", SYMBOLE)

    assert _vote(0.5, [0.5, 0.5]).score is None
    assert not any(a == "QualityAgent" for _s, a in stimmen)


# --- 2: Der Rechner ---------------------------------------------------------------


def test_rechner_traegt_quality(tmp_path, korpus):
    from core.sim.stimmen_rechner import GEWICHTE, STIMMEN_STAND

    pfad = _schreibe_abbild(tmp_path / "a.json.gz", ABBILD)

    stimmen = _stimmen(_rechner(pfad), korpus, "2025-06-27", SYMBOLE)

    assert GEWICHTE["QualityAgent"] == 0.30
    assert stimmen[("AAA", "QualityAgent")].weight == 0.30
    assert STIMMEN_STAND == 4


def test_quality_referenz_ohne_lookahead(tmp_path, korpus):
    """Eine Einreichung vom Stichtag wirkt erst am Folgetag auf die Referenz."""
    from core.quality_score import composite_quality, quality_percentile
    from core.report.financials_feed import derive_fundamentals

    # Am 03.02.2025 reicht nur CCC ein. Der eigene Composite von CCC sieht das noch am
    # selben Tag (PIT wie live), die Referenz der anderen erst am 04.02.
    abbild = {k: {"results": list(v["results"])} for k, v in ABBILD.items()}
    abbild["AAA"]["results"][1] = _einreichung("2025-01-15", 2024, 1.3)
    abbild["BBB"]["results"][1] = _einreichung("2025-01-15", 2024, 1.1)
    pfad = _schreibe_abbild(tmp_path / "q.json.gz", abbild)

    def rang(tag):
        s = _stimmen(_rechner(pfad), korpus, tag, SYMBOLE)
        return s[("AAA", "QualityAgent")].score, s[("BBB", "QualityAgent")].score

    def composite(symbol, tag):
        return composite_quality(derive_fundamentals(abbild[symbol]["results"], tag))

    def erwartet(symbol, tag_ref, tag_eigen):
        ref = [composite(s, tag_ref) for s in abbild]
        return quality_percentile(
            composite(symbol, tag_eigen), [c for c in ref if c is not None]
        )

    stichtag, vortag, folgetag = "2025-02-03", "2025-02-02", "2025-02-04"
    assert composite("CCC", vortag) is None and composite("CCC", stichtag) is not None
    assert rang(stichtag) == (
        erwartet("AAA", vortag, stichtag),
        erwartet("BBB", vortag, stichtag),
    )
    assert rang(folgetag) == (
        erwartet("AAA", stichtag, folgetag),
        erwartet("BBB", stichtag, folgetag),
    )
    assert rang(stichtag) != rang(folgetag)


def test_fortsetzen_bitgleich(tmp_path, korpus):
    """Abbruch mitten im Lauf ergibt dieselbe ``votes.parquet`` wie ein Lauf am Stueck."""
    from core.sim import vorbereitung

    pfad = _schreibe_abbild(tmp_path / "f.json.gz", ABBILD)

    def lauf(ordner, abbruch=lambda: False):
        return vorbereitung.berechne_stimmen(
            ordner, list(SYMBOLE), _rechner(pfad), abbruch
        )

    zweiter = _korpus_ordner(tmp_path / "zweiter")
    assert lauf(korpus)

    aufrufe = iter(range(1_000_000))
    assert not lauf(zweiter, lambda: next(aufrufe) == 150)
    assert lauf(zweiter)

    a, b = [pd.read_parquet(o / "stimmen" / "votes.parquet") for o in (korpus, zweiter)]
    assert "QualityAgent" in set(a.agent)
    pd.testing.assert_frame_equal(a, b)


# --- 3: Vergleich und Ausweis -------------------------------------------------------


def test_sim_strategie_mappt_quality():
    from core.sim import vergleich as v

    felder, nicht = v.sim_strategie(
        "A", {"QUALITY_AGENT_ENABLED": "true", "QUALITY_AGENT_WEIGHT": "0.4"}, 1000.0
    )
    assert felder["gewichte"]["QualityAgent"] == 0.4
    assert nicht == []

    aus, _ = v.sim_strategie("B", {}, 1000.0)
    assert aus["gewichte"]["QualityAgent"] == 0.0


def test_drei_fundamental_agenten_nicht_ausgewiesen():
    from core.sim import vergleich as v

    agenten = v.nicht_abgebildete_agenten(
        {
            "FUNDAMENTALS_AGENT_ENABLED": "true",
            "VALUATION_AGENT_ENABLED": "true",
            "VALUATION_AGENT_WEIGHT": "0.3",
            "QUALITY_AGENT_ENABLED": "true",
        }
    )

    assert not {"FundamentalsAgent", "ValuationAgent", "QualityAgent"} & set(agenten)


# --- 4: Titel ohne Fundamentaldaten (Owner-Bestaetigung zu Plan §6 Punkt 4) -----------


def _store(udd, zeilen):
    """``zeilen``: ``(symbol, tag, agenten)``; jede Zeile eine Entscheidung."""
    from core.sim import vergleich as v

    ziel = udd / v.KORPUS / "stimmen"
    ziel.mkdir(parents=True)
    dec = [
        {"did": i, "symbol": s, "ts": f"{t}T20:00:00+00:00"}
        for i, (s, t, _a) in enumerate(zeilen)
    ]
    vot = [
        {"did": i, "agent": a}
        for i, (_s, _t, agenten) in enumerate(zeilen)
        for a in agenten
    ]
    pd.DataFrame(dec).to_parquet(ziel / "decisions.parquet")
    pd.DataFrame(vot).to_parquet(ziel / "votes.parquet")


VOLL = ("MomentumAgent", "FundamentalsAgent", "QualityAgent")


def test_titel_ohne_fundamentaldaten_im_zeitraum(tmp_path, monkeypatch):
    from core.sim import vergleich as v

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    _store(
        tmp_path,
        [
            ("AAPL", "2026-01-05", VOLL),
            ("JPM", "2026-01-05", ("MomentumAgent", "FundamentalsAgent")),
            ("XOM", "2026-01-05", ("MomentumAgent",)),
            ("SPY", "2026-01-05", ("MomentumAgent",)),  # der Benchmark hat keine
            ("KO", "2026-01-05", VOLL),
            ("KO", "2026-01-06", ("MomentumAgent",)),  # nur an einem Tag: zaehlt
            ("MSFT", "2025-12-30", ("MomentumAgent",)),  # ausserhalb des Zeitraums
            ("MSFT", "2026-01-05", VOLL),
        ],
    )

    titel = v.titel_ohne_fundamentaldaten("2026-01-02", "2026-06-30")

    assert titel == ["JPM", "KO", "XOM"]


def test_valuation_enthaltung_ist_kein_datenloch(tmp_path, monkeypatch):
    """Valuation enthaelt sich auch mit Daten im PEG-Band (``fundament_stimmen``)."""
    from core.sim import vergleich as v

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    _store(tmp_path, [("AAPL", "2026-01-05", VOLL)])

    assert v.titel_ohne_fundamentaldaten("2026-01-02", "2026-06-30") == []


def test_store_unlesbar_warnt(tmp_path, monkeypatch, caplog):
    from core.sim import vergleich as v

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    with caplog.at_level("WARNING", logger="core.sim.vergleich"):
        assert v.titel_ohne_fundamentaldaten("2026-01-02", "2026-06-30") == []

    assert "Fundamentaldaten" in caplog.text


# --- 5: Determinismus und Trennung ----------------------------------------------------


@pytest.fixture(scope="module")
def mess_lauf(tmp_path_factory):
    symbole = _mess_symbole()
    ordner = _mess_korpus(tmp_path_factory.mktemp("mess") / "korpus", symbole)
    sekunden = _rechne(ordner, symbole)
    print(f"\n#5083 Phase stimmen: {sekunden:.1f} s, {len(symbole)} Titel")
    return ordner, symbole


def test_bitgleich(mess_lauf, tmp_path):
    ordner, symbole = mess_lauf
    zweiter = _mess_korpus(tmp_path / "zweiter", symbole)

    _rechne(zweiter, symbole)

    a, b = [
        hashlib.sha256((o / "stimmen" / "votes.parquet").read_bytes()).hexdigest()
        for o in (ordner, zweiter)
    ]
    print(f"\n#5083 SHA-256 votes.parquet: {a[:16]}")
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
    print(f"\n#5083 {len(votes)} Zeilen: {votes.agent.value_counts().to_dict()}")
    assert "QualityAgent" in set(votes.agent)
    store = StimmenStore.from_verzeichnis(str(stimmen))
    kurse = lade_kurse(str(stimmen / "kurse.parquet"))

    def lauf(**werte):
        felder, _ = v.sim_strategie("S", werte, 100_000.0)
        r = simuliere(
            store, kurse, StimmenSimStrategie(**felder), "2024-10-01", "2026-10-30"
        )
        gefuellt = sum(o["status"] == "filled" for o in r.orders)
        print(f"#5083 {werte}: {gefuellt} Orders, Endwert {r.ending_equity:.2f}")
        assert gefuellt > 0, werte
        return r.daily_equity

    vorgabe = lauf()
    aus = lauf(QUALITY_AGENT_ENABLED="false")
    an = [
        lauf(QUALITY_AGENT_ENABLED="true", QUALITY_AGENT_WEIGHT=gewicht)
        for gewicht in ("0.1", "0.3", "1.0")
    ]

    assert vorgabe == aus
    assert an[0] != an[1] and an[1] != an[2]
