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
    # #4570: von 25,800 auf 27,412 Mrd (+6,25 %). NEE ist der EINZIGE der neun Titel,
    # der sich durch die Groessenregel aendert — die anderen acht bleiben Zeichen fuer
    # Zeichen gleich, und genau das macht diese Tabelle zum Waechter dafuer.
    #
    # Beide Kennzeichen stammen aus DERSELBEN Einreichung (2026-02-13), Point-in-Time
    # trennt sie also nicht; bisher entschied allein der Rang:
    #   Rang 2  RevenueFromContractWithCustomerIncludingAssessedTax   25,800 Mrd
    #   Rang 6  RegulatedAndUnregulatedOperatingRevenue               27,412 Mrd
    # Der erste ist der Umsatz aus Kundenvertraegen nach ASC 606. Bei einem Versorger
    # ist die Oberkante der regulierte PLUS unregulierte Betriebsumsatz; die Differenz
    # von 1,612 Mrd ist der Teil, den ASC 606 nicht abdeckt. #4567 hat NEE eine Zahl
    # gegeben, #4570 gibt ihm die richtige.
    "NEE": (27_412_000_000, "2025-12-31"),
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


# ---------------------------------------------------------------------------
# #4570 — Teilumsatz bei Ueberlappung: der groessere Betrag gewinnt, NUR beim Umsatz.
#
# Gemessen am 20./21.10.2026 ueber 492 Titel mit den Produktionsfiltern: 1.943
# Jahresperioden tragen zwei Kennzeichen; in 469 davon ist der Rang-Sieger um mindestens
# 1 % kleiner als der groesste gemeldete Wert. 61 Titel melden ihre juengste Periode zu
# klein, 10 davon sind heute bewertet. Haertester Fall ESS: 0,009 statt 1,887 Mrd (201x).
#
# `RevenueFromContractWithCustomerExcludingAssessedTax` steht auf Rang 0, ist aber nur
# der ASC-606-Umsatz — bei Verpaechtern, Banken und Versorgern fehlen Leasing- (ASC 842)
# und Zinsertraege. Melden beide Kennzeichen DIESELBE Periode, entschied bisher allein
# der Rang, und der Teilumsatz gewann.
# ---------------------------------------------------------------------------


def _ueberlappung_facts(rang0, rang1, end="2025-12-31", filed="2026-02-01"):
    """Das gemessene URI-Muster: Rang 0 klein, Rang 1 gross, dieselbe Periode."""
    return _facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": (
                "USD",
                [_pt(rang0, 2026, filed, end)],
            ),
            "Revenues": ("USD", [_pt(rang1, 2026, filed, end)]),
            "NetIncomeLoss": ("USD", [_pt(rang1 // 10, 2026, filed, end)]),
        }
    )


@pytest.mark.vc1
def test_4570_umsatz_bei_ueberlappung_nimmt_den_groesseren_betrag():
    """URI: 3,695 Mrd aus ASC 606 gegen 16,099 Mrd Gesamtumsatz, dieselbe Einreichung."""
    rows = sec.rows_from_companyfacts(
        _ueberlappung_facts(3_695_000_000, 16_099_000_000)
    )
    wert = rows[0]["financials"]["income_statement"]["revenues"]["value"]
    assert wert == 16_099_000_000, f"Umsatz {wert!r} statt 16,099 Mrd"


@pytest.mark.vc1
def test_4570_gewinn_bleibt_beim_rang_sieger():
    """Die Fessel: beim GEWINN waere die Regel falsch.

    `ProfitLoss` schliesst Minderheitenanteile ein, `NetIncomeLoss` ist der
    zurechenbare Betrag. Gemessen wuerde die Regel hier in 14 von 14 geprueften
    Titeln mit Ueberlappung den falschen Wert nehmen (GM +17,9 %, EQT +14,0 %,
    CBRE +10,4 %, JCI +6,7 %). Dieser Test ist gegen `main` gruen und muss es
    bleiben — er verhindert, dass sich die neue Regel ueber alle Kennwerte
    ausbreitet.
    """
    f = _facts(
        {
            "Revenues": (
                "USD",
                [_pt(100_000_000_000, 2026, "2026-02-01", "2025-12-31")],
            ),
            "NetIncomeLoss": (
                "USD",
                [_pt(2_697_000_000, 2026, "2026-02-01", "2025-12-31")],
            ),
            "ProfitLoss": (
                "USD",
                [_pt(3_180_000_000, 2026, "2026-02-01", "2025-12-31")],
            ),
        }
    )
    rows = sec.rows_from_companyfacts(f)
    wert = rows[0]["financials"]["income_statement"]["net_income_loss"]["value"]
    assert wert == 2_697_000_000, f"Gewinn {wert!r} — ProfitLoss hat gewonnen"


@pytest.mark.vc1
def test_4570_gleichstand_entscheidet_der_rang():
    """Determinismus: bei gleichem Betrag bleibt das vordere Kennzeichen Sieger."""
    rows = sec.rows_from_companyfacts(_ueberlappung_facts(5_000, 5_000))
    assert rows[0]["financials"]["income_statement"]["revenues"]["value"] == 5_000


@pytest.mark.vc1
def test_4570_total_revenues_and_other_income_gewinnt_keinen_groessenvergleich():
    """Es schliesst sonstige Ertraege ein, ist also kein belastbarer Umsatz-Sieger."""
    f = _facts(
        {
            "Revenues": (
                "USD",
                [_pt(10_000_000_000, 2026, "2026-02-01", "2025-12-31")],
            ),
            "TotalRevenuesAndOtherIncome": (
                "USD",
                [_pt(12_000_000_000, 2026, "2026-02-01", "2025-12-31")],
            ),
            "NetIncomeLoss": ("USD", [_pt(1_000, 2026, "2026-02-01", "2025-12-31")]),
        }
    )
    rows = sec.rows_from_companyfacts(f)
    wert = rows[0]["financials"]["income_statement"]["revenues"]["value"]
    assert wert == 10_000_000_000, f"Umsatz {wert!r} — sonstige Ertraege sind drin"


@pytest.mark.vc1
def test_4570_total_revenues_and_other_income_bleibt_allein_nutzbar():
    """Aus dem Vergleich genommen heisst nicht abgeschaltet."""
    f = _facts(
        {
            "TotalRevenuesAndOtherIncome": (
                "USD",
                [_pt(12_000_000_000, 2026, "2026-02-01", "2025-12-31")],
            ),
            "NetIncomeLoss": ("USD", [_pt(1_000, 2026, "2026-02-01", "2025-12-31")]),
        }
    )
    rows = sec.rows_from_companyfacts(f)
    wert = rows[0]["financials"]["income_statement"]["revenues"]["value"]
    assert (
        wert == 12_000_000_000
    ), f"Umsatz {wert!r} — das Kennzeichen wurde stillgelegt"


@pytest.mark.vc1
def test_4570_point_in_time_ueberlebt_den_groessenvergleich():
    """Die Stelle, an der diese Aenderung still falsch werden koennte.

    Der Vergleich greift zwischen den je Kennzeichen bestimmten Werten — NICHT
    ueber alle Rohpunkte. Sonst verdraengte eine spaetere Korrektur mit hoeherem
    Betrag die urspruengliche Meldung und haette die Point-in-Time-Regel aus
    #4567 ausgehebelt.
    """
    end = "2025-12-31"
    f = _facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": (
                "USD",
                [_pt(3_695_000_000, 2026, "2026-02-01", end)],
            ),
            "Revenues": (
                "USD",
                [
                    _pt(16_099_000_000, 2026, "2026-02-01", end),
                    # Spaetere Korrektur, hoeherer Betrag: darf NICHT gewinnen.
                    _pt(20_000_000_000, 2026, "2026-05-01", end),
                ],
            ),
            "NetIncomeLoss": ("USD", [_pt(1_000, 2026, "2026-02-01", end)]),
        }
    )
    rows = sec.rows_from_companyfacts(f)
    wert = rows[0]["financials"]["income_statement"]["revenues"]["value"]
    assert wert == 16_099_000_000, f"Umsatz {wert!r} — die Korrektur hat gewonnen"


@pytest.mark.vc1
def test_4570_zwei_laeufe_liefern_identische_zeilen():
    f = _ueberlappung_facts(3_695_000_000, 16_099_000_000)
    assert sec.rows_from_companyfacts(f) == sec.rows_from_companyfacts(f)


# ---------------------------------------------------------------------------
# #4570 — die gemessenen Faelle an aufgezeichneten Daten.
#
# Keine Netzabfrage. Herkunft und Reduktion: tests/fixtures/sec_companyfacts_4570/
# manifest.json (gleiches Verfahren wie der #4567-Satz, Werte unveraendert).
#
# Die Tabelle enthaelt ABSICHTLICH beide Richtungen. Drei Faelle behebt die Regel, drei
# darf sie nicht verschlechtern — und ETN/GIS sind genau die, an denen die verworfene
# Gegenrichtung (``Revenues`` nach vorn sortieren) gescheitert waere: ETN 2016 waere auf
# exakt 0,000 Mrd gefallen, GIS 2023 auf 1,957 Mrd. Ein Test nur ueber die Behebungen
# haelt die beiden Fehlerrichtungen nicht auseinander.
# ---------------------------------------------------------------------------

_FX_4570 = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "sec_companyfacts_4570"
)

#: symbol -> (periodenende, umsatz, gewinn, rolle)
_ERWARTET_4570 = {
    # Behebt: der Rang-Sieger war der ASC-606-Teilumsatz.
    "ESS": ("2025-12-31", 1_887_345_000, 702_773_000, "behebt"),
    "URI": ("2025-12-31", 16_099_000_000, 2_494_000_000, "behebt"),
    "AXP": ("2025-12-31", 72_229_000_000, 10_833_000_000, "behebt"),
    # Darf sich NICHT verschlechtern — und genau hier waere die Gegenrichtung
    # gescheitert: ETN 2016 auf 0,000 Mrd, GIS 2023 auf 1,957 Mrd.
    "ETN": ("2016-12-31", 19_747_000_000, 1_925_000_000, "haelt"),
    "GIS": ("2023-05-28", 20_094_200_000, 2_593_900_000, "haelt"),
    # GM ist BEIDES: der Umsatz steigt von 167,971 auf 185,019 Mrd (+10,2 %), waehrend
    # der Gewinn bei 2,697 Mrd bleiben MUSS — ProfitLoss traegt dort 3,180 Mrd. Der
    # eine Titel, an dem beide Richtungen der Regel gleichzeitig geprueft werden.
    "GM": ("2025-12-31", 185_019_000_000, 2_697_000_000, "behebt Umsatz, haelt Gewinn"),
}


def _companyfacts_4570(symbol):
    with open(os.path.join(_FX_4570, f"{symbol}.json"), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.vc1
@pytest.mark.parametrize("symbol", sorted(_ERWARTET_4570))
def test_4570_gemessene_faelle(symbol):
    ende, umsatz, gewinn, rolle = _ERWARTET_4570[symbol]
    rows = sec.rows_from_companyfacts(_companyfacts_4570(symbol))
    nach_ende = {r["end_date"]: r for r in rows}
    assert ende in nach_ende, f"{symbol}: Periode {ende} fehlt"
    fin = nach_ende[ende]["financials"]["income_statement"]
    assert (
        fin["revenues"]["value"] == umsatz
    ), f"{symbol} ({rolle}) {ende}: Umsatz {fin['revenues']['value']!r} statt {umsatz}"
    assert fin["net_income_loss"]["value"] == gewinn, (
        f"{symbol} ({rolle}) {ende}: Gewinn "
        f"{fin['net_income_loss']['value']!r} statt {gewinn}"
    )


@pytest.mark.vc1
def test_4570_gm_gewinn_nimmt_nicht_profitloss():
    """Die Fessel am echten Fall: GM meldet beide Kennzeichen, 17,9 % auseinander."""
    f = _companyfacts_4570("GM")
    gaap = f["facts"]["us-gaap"]
    assert "ProfitLoss" in gaap, "ohne ProfitLoss prueft dieser Fall nichts"
    rows = sec.rows_from_companyfacts(f)
    juengste = max(r["end_date"] for r in rows)
    nach_ende = {r["end_date"]: r for r in rows}
    wert = nach_ende[juengste]["financials"]["income_statement"]["net_income_loss"][
        "value"
    ]
    assert wert == 2_697_000_000, f"Gewinn {wert!r} — ProfitLoss hat gewonnen"
