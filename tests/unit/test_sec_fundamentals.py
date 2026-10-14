"""#3145 — SEC EDGAR -> financials_cache adapter (pure normalizer).

The adapter turns a SEC ``companyfacts`` payload into the same Polygon-vX ``results``
rows the existing ``financials_feed.derive_fundamentals`` already consumes, so the
FundamentalsAgent/ValuationAgent read real numbers with NO agent/reader change. Free,
no API key, public-domain source. These tests pin the row shape + the tag-synonym /
20-F / short-history / restatement handling against the real derivation.
"""

import json
import os
from datetime import date

import pytest

from core.report import sec_fundamentals as sec
from core.report.financials_feed import derive_fundamentals


def _pt(val, fy, filed, end, form="10-K", fp="FY"):
    return {"val": val, "fy": fy, "fp": fp, "form": form, "filed": filed, "end": end}


def _facts(concepts):
    """concepts: {tag: (unit, [points])} -> companyfacts skeleton."""
    return {
        "cik": 789019,
        "entityName": "TESTCO",
        "facts": {
            "us-gaap": {
                tag: {"units": {unit: pts}} for tag, (unit, pts) in concepts.items()
            }
        },
    }


def _two_year_msft():
    return _facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": (
                "USD",
                [
                    _pt(245_000, 2025, "2025-07-30", "2025-06-30"),
                    _pt(289_000, 2026, "2026-07-29", "2026-06-30"),
                ],
            ),
            "NetIncomeLoss": (
                "USD",
                [
                    _pt(88_000, 2025, "2025-07-30", "2025-06-30"),
                    _pt(116_000, 2026, "2026-07-29", "2026-06-30"),
                ],
            ),
            "EarningsPerShareDiluted": (
                "USD/shares",
                [
                    _pt(13.5, 2025, "2025-07-30", "2025-06-30"),
                    _pt(17.95, 2026, "2026-07-29", "2026-06-30"),
                ],
            ),
            "Liabilities": ("USD", [_pt(315_000, 2026, "2026-07-29", "2026-06-30")]),
            "StockholdersEquity": (
                "USD",
                [_pt(442_000, 2026, "2026-07-29", "2026-06-30")],
            ),
        }
    )


def test_rows_feed_derive_fundamentals_end_to_end():
    rows = sec.rows_from_companyfacts(_two_year_msft())
    d = derive_fundamentals(rows, as_of=date(2026, 9, 1), close=502.50)
    assert d is not None
    assert d["revenue_yoy"] == pytest.approx(289_000 / 245_000 - 1, rel=1e-6)
    assert d["net_margin"] == pytest.approx(116_000 / 289_000, rel=1e-6)
    assert d["eps_yoy"] == pytest.approx(17.95 / 13.5 - 1, rel=1e-6)
    assert d["pe"] == pytest.approx(502.50 / 17.95, rel=1e-6)
    assert d["total_liabilities"] == 315_000
    assert d["total_equity"] == 442_000
    assert d["filing_date"] == "2026-07-29"


def test_revenue_tag_synonym_is_resolved():
    """Revenue only under the plain ``Revenues`` tag (not the contract tag) is still read."""
    f = _facts(
        {
            "Revenues": (
                "USD",
                [
                    _pt(100, 2025, "2025-02-01", "2024-12-31"),
                    _pt(130, 2026, "2026-02-01", "2025-12-31"),
                ],
            ),
            "NetIncomeLoss": ("USD", [_pt(26, 2026, "2026-02-01", "2025-12-31")]),
        }
    )
    d = derive_fundamentals(
        sec.rows_from_companyfacts(f), as_of=date(2026, 9, 1), close=None
    )
    assert d["revenue_yoy"] == pytest.approx(0.30, rel=1e-6)
    assert d["net_margin"] == pytest.approx(0.20, rel=1e-6)


def test_foreign_filer_20f_is_covered():
    f = _facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": (
                "USD",
                [
                    _pt(10, 2025, "2025-03-01", "2024-12-31", form="20-F"),
                    _pt(12, 2026, "2026-03-01", "2025-12-31", form="20-F"),
                ],
            ),
            "NetIncomeLoss": (
                "USD",
                [_pt(3, 2026, "2026-03-01", "2025-12-31", form="20-F")],
            ),
        }
    )
    rows = sec.rows_from_companyfacts(f)
    assert len(rows) == 2
    d = derive_fundamentals(rows, as_of=date(2026, 9, 1), close=None)
    assert d["revenue_yoy"] == pytest.approx(0.20, rel=1e-6)


def test_equity_synonym_including_noncontrolling():
    f = _facts(
        {
            "Revenues": ("USD", [_pt(100, 2026, "2026-02-01", "2025-12-31")]),
            "Liabilities": ("USD", [_pt(80, 2026, "2026-02-01", "2025-12-31")]),
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": (
                "USD",
                [_pt(40, 2026, "2026-02-01", "2025-12-31")],
            ),
        }
    )
    d = derive_fundamentals(
        sec.rows_from_companyfacts(f), as_of=date(2026, 9, 1), close=None
    )
    assert d["total_liabilities"] == 80
    assert d["total_equity"] == 40


def test_same_period_uses_as_reported_earliest_filed():
    """Two filings for the SAME period end -> the earliest-filed (as-originally-
    reported) value wins, and the row's filing_date is that original date. This is
    point-in-time correct: a later comparative / 10-K/A restatement must not rewrite
    what was knowable at the original filing time."""
    f = _facts(
        {
            "Revenues": (
                "USD",
                [
                    _pt(100, 2026, "2026-02-01", "2025-12-31"),
                    _pt(
                        105, 2026, "2026-08-01", "2025-12-31", form="10-K/A"
                    ),  # later restatement
                ],
            ),
        }
    )
    rows = sec.rows_from_companyfacts(f)
    assert len(rows) == 1
    assert rows[0]["financials"]["income_statement"]["revenues"]["value"] == 100
    assert rows[0]["filing_date"] == "2026-02-01"


def test_short_history_one_year_still_yields_margin():
    f = _facts(
        {
            "Revenues": ("USD", [_pt(100, 2026, "2026-02-01", "2025-12-31")]),
            "NetIncomeLoss": ("USD", [_pt(25, 2026, "2026-02-01", "2025-12-31")]),
        }
    )
    d = derive_fundamentals(
        sec.rows_from_companyfacts(f), as_of=date(2026, 9, 1), close=None
    )
    assert d["revenue_yoy"] is None  # no prior year
    assert d["net_margin"] == pytest.approx(
        0.25, rel=1e-6
    )  # FundamentalsAgent can still vote


def test_no_gaap_facts_yields_no_rows():
    assert sec.rows_from_companyfacts({"facts": {}}) == []
    assert (
        derive_fundamentals(
            sec.rows_from_companyfacts({"facts": {}}), as_of=date(2026, 9, 1)
        )
        is None
    )


def test_build_cache_payload_shape():
    payload = sec.build_cache_payload(_two_year_msft())
    assert set(payload.keys()) == {"results"}
    assert isinstance(payload["results"], list) and len(payload["results"]) == 2


# ---------------------------------------------------------------------------
# #4567 — Das Umsatzkennzeichen wird PRO PERIODE gewaehlt.
#
# Gemessen am 12.10.2026: Bei acht Titeln (NEE, MS, CVS, O, BA, TER, FIX, PNC)
# gewinnt ein Synonym, dessen Jahresreihe 2012 bis 2021 endet, waehrend ein
# Synonym WEITER HINTEN in derselben Liste bis FY 2025 reicht. Ursache war
# "first synonym with data wins" — einmal je Titel, fuer alle Perioden.
# ---------------------------------------------------------------------------

_SWITCH_UMSATZ = {
    "2016-12-31": 100,
    "2017-12-31": 110,
    "2018-12-31": 120,
    "2019-12-31": 130,
    "2020-12-31": 140,
    "2021-12-31": 150,
    "2022-12-31": 160,
    "2023-12-31": 170,
    "2024-12-31": 180,
    "2025-12-31": 190,
}


def _kennzeichenwechsel_facts():
    """Das gemessene Muster: Kennzeichen 1 bis 2018, Kennzeichen 2 ab 2019.

    ``Revenues`` steht in ``SEC_TAG_MAP`` VOR
    ``RevenueFromContractWithCustomerIncludingAssessedTax`` — genau die
    Reihenfolge, an der NEE heute scheitert.
    """
    alt = [
        _pt(v, int(end[:4]) + 1, f"{int(end[:4]) + 1}-02-01", end)
        for end, v in _SWITCH_UMSATZ.items()
        if end < "2019"
    ]
    neu = [
        _pt(v, int(end[:4]) + 1, f"{int(end[:4]) + 1}-02-01", end)
        for end, v in _SWITCH_UMSATZ.items()
        if end >= "2019"
    ]
    gewinn = [
        _pt(v // 10, int(end[:4]) + 1, f"{int(end[:4]) + 1}-02-01", end)
        for end, v in _SWITCH_UMSATZ.items()
    ]
    return _facts(
        {
            "Revenues": ("USD", alt),
            "RevenueFromContractWithCustomerIncludingAssessedTax": ("USD", neu),
            "NetIncomeLoss": ("USD", gewinn),
        }
    )


@pytest.mark.vc1
def test_kennzeichenwechsel_jede_periode_traegt_ihren_umsatz():
    """Jede Periode traegt den Umsatz aus dem Kennzeichen, das SIE meldet."""
    rows = sec.rows_from_companyfacts(_kennzeichenwechsel_facts())
    nach_ende = {r["end_date"]: r for r in rows}
    assert set(nach_ende) == set(_SWITCH_UMSATZ)
    for end, erwartet in _SWITCH_UMSATZ.items():
        wert = nach_ende[end]["financials"]["income_statement"]["revenues"]["value"]
        assert wert == erwartet, f"{end}: Umsatz {wert!r} statt {erwartet}"


@pytest.mark.vc1
def test_kennzeichenwechsel_juengstes_jahr_stimmt_ab():
    """Der Kern des Befunds: FY 2025 traegt Umsatz UND Gewinn, der Agent kann stimmen."""
    d = derive_fundamentals(
        sec.rows_from_companyfacts(_kennzeichenwechsel_facts()),
        as_of=date(2026, 9, 1),
        close=None,
    )
    assert d is not None
    assert d["net_margin"] == pytest.approx(19 / 190, rel=1e-6)
    assert d["revenue_yoy"] == pytest.approx(190 / 180 - 1, rel=1e-6)


@pytest.mark.vc1
def test_listenreihenfolge_gilt_je_periode():
    """Bei Ueberlappung gewinnt das Synonym, das in der Liste weiter vorn steht —
    und zwar je Periode, ohne die hinteren Synonyme fuer andere Perioden zu sperren."""
    f = _facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": (
                "USD",
                [_pt(200, 2026, "2026-02-01", "2025-12-31")],
            ),
            "Revenues": (
                "USD",
                [
                    _pt(100, 2026, "2026-02-01", "2025-12-31"),
                    _pt(90, 2025, "2025-02-01", "2024-12-31"),
                ],
            ),
        }
    )
    nach_ende = {r["end_date"]: r for r in sec.rows_from_companyfacts(f)}

    def umsatz(end):
        return nach_ende[end]["financials"]["income_statement"]["revenues"]["value"]

    assert umsatz("2025-12-31") == 200  # vorderes Synonym gewinnt die Ueberlappung
    assert umsatz("2024-12-31") == 90  # hinteres Synonym traegt seine eigene Periode


@pytest.mark.vc1
def test_point_in_time_bleibt_innerhalb_der_periode():
    """Frueheste Einreichung gewinnt weiter — auch wenn die Nachbarperiode aus
    einem anderen Kennzeichen kommt."""
    f = _facts(
        {
            "Revenues": (
                "USD",
                [
                    _pt(100, 2019, "2019-02-01", "2018-12-31"),
                    _pt(107, 2021, "2021-02-01", "2018-12-31", form="10-K/A"),
                ],
            ),
            "RevenueFromContractWithCustomerIncludingAssessedTax": (
                "USD",
                [_pt(130, 2020, "2020-02-01", "2019-12-31")],
            ),
        }
    )
    nach_ende = {r["end_date"]: r for r in sec.rows_from_companyfacts(f)}
    assert (
        nach_ende["2018-12-31"]["financials"]["income_statement"]["revenues"]["value"]
        == 100
    )
    assert nach_ende["2018-12-31"]["filing_date"] == "2019-02-01"
    assert (
        nach_ende["2019-12-31"]["financials"]["income_statement"]["revenues"]["value"]
        == 130
    )


@pytest.mark.vc1
def test_zeilen_sind_deterministisch_und_neueste_periode_zuerst():
    """Deterministisch — und die neueste Periode steht vorn.

    `financials_feed._pit_select` sortiert nur nach `filing_date` und stabil. Teilen
    mehrere Perioden ein Einreichungsdatum, entscheidet die Reihenfolge, die der
    Adapter liefert, welche Periode als „aktuell" gilt. Vorher war das die
    Hash-Reihenfolge einer Menge, also je Prozess verschieden.
    """
    f = _kennzeichenwechsel_facts()
    assert sec.rows_from_companyfacts(f) == sec.rows_from_companyfacts(f)
    enden = [r["end_date"] for r in sec.rows_from_companyfacts(f)]
    assert enden == sorted(enden, reverse=True)


# ---------------------------------------------------------------------------
# #4567 — Nur JAHRESdauern zaehlen als Jahresperiode.
#
# Gemessen an D und URI: Deren 10-K kennzeichnet auch Quartalsdauern mit `fp=FY`;
# ueber die 19 geprueften Titel passieren 11.154 solcher Punkte den Filter aus
# `fp` + `form`. Alle vier Perioden teilen dann ein Einreichungsdatum, und der
# Ableiter nimmt die erste als „aktuell" — bei D waere das ein Quartal (4,1 Mrd)
# statt des Jahres (16,5 Mrd), und `revenue_yoy` verglich Quartal gegen Quartal.
# ---------------------------------------------------------------------------


@pytest.mark.vc1
def test_quartalsdauer_mit_fp_fy_wird_nicht_zur_jahresperiode():
    """Ein 10-K kennzeichnet auch Quartale mit `fp=FY` — die duerfen keine Zeile werden."""
    f = _facts(
        {
            "Revenues": (
                "USD",
                [
                    dict(
                        _pt(16_523, 2026, "2026-02-23", "2025-12-31"),
                        start="2025-01-01",
                    ),
                    dict(
                        _pt(4_076, 2026, "2026-02-23", "2025-03-31"),
                        start="2025-01-01",
                    ),
                    dict(
                        _pt(3_900, 2026, "2026-02-23", "2025-06-30"),
                        start="2025-04-01",
                    ),
                    dict(
                        _pt(15_400, 2025, "2025-02-20", "2024-12-31"),
                        start="2024-01-01",
                    ),
                ],
            ),
        }
    )
    rows = sec.rows_from_companyfacts(f)
    assert [r["end_date"] for r in rows] == ["2025-12-31", "2024-12-31"]
    d = derive_fundamentals(rows, as_of=date(2026, 9, 1), close=None)
    assert d["revenue"] == 16_523
    assert d["revenue_yoy"] == pytest.approx(16_523 / 15_400 - 1, rel=1e-6)


@pytest.mark.vc1
def test_bilanzstichtage_ohne_start_bleiben_erhalten():
    """Instant-Konzepte (Bilanz) tragen kein `start` — der Dauerfilter darf sie nicht
    verwerfen."""
    f = _facts(
        {
            "Revenues": (
                "USD",
                [
                    dict(
                        _pt(100, 2026, "2026-02-01", "2025-12-31"),
                        start="2025-01-01",
                    )
                ],
            ),
            "Assets": ("USD", [_pt(900, 2026, "2026-02-01", "2025-12-31")]),
            "Liabilities": ("USD", [_pt(500, 2026, "2026-02-01", "2025-12-31")]),
            "StockholdersEquity": (
                "USD",
                [_pt(400, 2026, "2026-02-01", "2025-12-31")],
            ),
        }
    )
    d = derive_fundamentals(
        sec.rows_from_companyfacts(f), as_of=date(2026, 9, 1), close=None
    )
    assert d["total_assets"] == 900
    assert d["total_liabilities"] == 500
    assert d["total_equity"] == 400


# ---------------------------------------------------------------------------
# #4567 Abschnitt 3.3 — Abnahme am gemessenen Fall.
#
# Aufgezeichnete Ausschnitte aus SEC companyfacts (Public Domain), reduziert auf die
# Synonymlisten fuer revenues und net_income_loss. Keine Netzabfrage im Test.
# Herkunft und Reduktion: tests/fixtures/sec_companyfacts_4567/manifest.json.
#
# Gegen die alte Regel ("erstes Synonym mit Daten gewinnt") traegt bei jedem dieser
# neun Titel das juengste Geschaeftsjahr KEINEN Umsatz — die Reihe des gewinnenden
# Kennzeichens endete 2012 (NEE), 2014 (MS), 2018 (CVS, O, XEL), 2019 (BA),
# 2020 (TER, FIX) bzw. 2021 (PNC).
# ---------------------------------------------------------------------------

_FX_4567 = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "sec_companyfacts_4567"
)

# Umsatz des juengsten Geschaeftsjahres, wie von der SEC gemeldet (nicht gerundet).
_ERWARTET_4567 = {
    "NEE": (25_800_000_000, "2025-12-31"),
    "MS": (70_645_000_000, "2025-12-31"),
    "CVS": (402_067_000_000, "2025-12-31"),
    "O": (5_749_377_000, "2025-12-31"),
    "BA": (89_463_000_000, "2025-12-31"),
    "TER": (3_190_024_000, "2025-12-31"),
    "FIX": (9_101_641_000, "2025-12-31"),
    "PNC": (23_099_000_000, "2025-12-31"),
    "XEL": (14_669_000_000, "2025-12-31"),  # Klasse B: neues Kennzeichen
}


def _companyfacts_fixture(symbol):
    with open(os.path.join(_FX_4567, f"{symbol}.json"), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.vc1
@pytest.mark.parametrize("symbol", sorted(_ERWARTET_4567))
def test_gemessene_titel_tragen_umsatz_fuer_ihr_juengstes_geschaeftsjahr(symbol):
    erwarteter_umsatz, erwartetes_ende = _ERWARTET_4567[symbol]
    rows = sec.rows_from_companyfacts(_companyfacts_fixture(symbol))
    d = derive_fundamentals(rows, as_of=date(2026, 10, 12), close=None)
    assert d is not None, f"{symbol}: kein Ergebnis"
    assert d["end_date"] == erwartetes_ende, f"{symbol}: Periode {d['end_date']}"
    assert d["revenue"] == erwarteter_umsatz, f"{symbol}: Umsatz {d['revenue']!r}"
    assert d["net_income"] is not None, f"{symbol}: kein Gewinn"
    # Der Zweck: beide Kennzahlen des FundamentalsAgent sind belegt, er enthaelt sich
    # nicht mehr mit „no revenue or margin data available yet".
    assert d["net_margin"] is not None, f"{symbol}: keine Netto-Marge"
    assert d["revenue_yoy"] is not None, f"{symbol}: kein Umsatzwachstum"


@pytest.mark.vc1
@pytest.mark.parametrize("symbol", sorted(_ERWARTET_4567))
def test_gemessene_titel_liefern_nur_jahresperioden(symbol):
    """Keine Quartalsdauer wird zur Jahreszeile — und die neueste Periode steht vorn."""
    rows = sec.rows_from_companyfacts(_companyfacts_fixture(symbol))
    enden = [r["end_date"] for r in rows]
    assert enden == sorted(enden, reverse=True)
    assert len(enden) == len(set(enden))
    # Aufeinanderfolgende Geschaeftsjahresenden liegen mindestens 300 Tage auseinander.
    # Nicht „gleicher Monat": CVS fuehrt ein 52/53-Wochen-Jahr (2007-12-29) und Morgan
    # Stanley hat das Geschaeftsjahresende 2009 von November auf Dezember verlegt.
    for juenger, aelter in zip(enden, enden[1:]):
        abstand = (date.fromisoformat(juenger) - date.fromisoformat(aelter)).days
        assert abstand >= 300, f"{symbol}: {aelter} -> {juenger} nur {abstand} Tage"
