"""#3337: `total_liabilities` aus der Bilanzidentitaet, wenn der XBRL-Tag fehlt.

`sec_fundamentals.SEC_TAG_MAP` bildet die Verbindlichkeiten auf EINEN Tag ab
(``"Liabilities"``), ohne Rueckfallvariante — waehrend ``revenues`` sechs hat. Viele
Emittenten weisen keine Summenzeile aus, sondern melden ``Assets`` und
``StockholdersEquity``. Gemessen ueber den vollstaendigen SEC-Cache (476 Titel,
Stichtag 2026-06-15): 158 Titel (33,2 %) ohne ``total_liabilities``, davon 128 aus
``assets - equity`` ableitbar.

Wirkung des Defekts: ``_debt_to_equity`` (agents.py:1997) liefert ``None``, und der
Aufrufer wertet ``debt is None`` als BESTANDENE Pruefung (agents.py:2261) — der
Verschuldungs-Waechter des live armierten ``ValuationAgent`` greift fuer ein Drittel
des Universums nie.

``Liabilities = Assets - Equity`` ist eine Bilanzidentitaet, keine Schaetzung.
"""

from datetime import date

from core.report.financials_feed import derive_fundamentals

_AS_OF = date(2026, 6, 15)


def _row(filing, end, *, assets=None, equity=None, liabilities=None, revenue=None):
    """Ein PIT-Ergebnissatz in der Form, die der Cache liefert."""
    bs = {}
    if assets is not None:
        bs["assets"] = {"value": assets}
    if equity is not None:
        bs["equity"] = {"value": equity}
    if liabilities is not None:
        bs["liabilities"] = {"value": liabilities}
    inc = {}
    if revenue is not None:
        inc["revenues"] = {"value": revenue}
    return {
        "filing_date": filing,
        "end_date": end,
        "fiscal_period": "FY",
        "timeframe": "annual",
        "financials": {"income_statement": inc, "balance_sheet": bs},
    }


def test_identity_fills_missing_liabilities():
    """assets + equity ohne liabilities -> Differenz (ADI-Fall)."""
    res = [
        _row("2026-01-15", "2025-11-01", assets=47_992_712_000, equity=33_815_755_000)
    ]
    f = derive_fundamentals(res, _AS_OF)
    assert f["total_liabilities"] == 47_992_712_000 - 33_815_755_000, (
        "Bilanzidentitaet nicht angewandt — total_liabilities: "
        f"{f.get('total_liabilities')!r}"
    )


def test_identity_applies_to_prior_period():
    """Der Vorjahressatz braucht dieselbe Ableitung (Piotroski-Delta-Leverage)."""
    res = [
        _row("2026-01-15", "2025-11-01", assets=47_992_712_000, equity=33_815_755_000),
        _row("2025-01-15", "2024-11-02", assets=45_000_000_000, equity=32_000_000_000),
    ]
    f = derive_fundamentals(res, _AS_OF)
    assert f["prior_total_liabilities"] == 45_000_000_000 - 32_000_000_000, (
        "Vorjahres-Identitaet nicht angewandt — prior_total_liabilities: "
        f"{f.get('prior_total_liabilities')!r}"
    )


def test_negative_equity_is_handled():
    """Negatives Eigenkapital ist kein Sonderfall (ABBV: 133,96 - (-3,27) = 137,23)."""
    res = [
        _row("2026-02-20", "2025-12-31", assets=133_960_000_000, equity=-3_270_000_000)
    ]
    f = derive_fundamentals(res, _AS_OF)
    assert (
        f["total_liabilities"] == 137_230_000_000
    ), f"negatives Eigenkapital falsch behandelt: {f.get('total_liabilities')!r}"


def test_reported_value_wins():
    """Byte-Identitaets-Wache: ein gemeldeter Wert schlaegt die Differenz."""
    res = [
        _row(
            "2026-02-20",
            "2025-12-31",
            assets=100_000,
            equity=40_000,
            liabilities=55_000,  # bewusst != assets - equity
        )
    ]
    f = derive_fundamentals(res, _AS_OF)
    assert f["total_liabilities"] == 55_000


def test_no_equity_stays_none():
    """Ohne Eigenkapital wird nichts erfunden — der Fail-open bleibt."""
    res = [_row("2026-02-20", "2025-12-31", assets=100_000)]
    f = derive_fundamentals(res, _AS_OF)
    assert f["total_liabilities"] is None
    assert f["total_equity"] is None


def test_valuation_debt_guard_engages():
    """Ende-zu-Ende: der Verschuldungs-Waechter sieht die abgeleitete Zahl.

    Ohne Fix liefert `_debt_to_equity` None, und `debt is None` gilt als gesund.
    """
    from core.round_table.agents import _debt_to_equity

    res = [
        _row("2026-02-20", "2025-12-31", assets=133_960_000_000, equity=-3_270_000_000)
    ]
    f = derive_fundamentals(res, _AS_OF)
    debt = _debt_to_equity(f)
    assert debt is not None, (
        "Verschuldungs-Waechter bleibt blind — debt_to_equity ist None, "
        "und der Aufrufer wertet das als gesunde Verschuldung (agents.py:2261)"
    )
