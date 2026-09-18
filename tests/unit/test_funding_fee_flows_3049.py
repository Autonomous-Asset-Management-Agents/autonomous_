# tests/unit/test_funding_fee_flows_3049.py
# #3049 Defekt A (+ Klein-Overshoot von B) — Transfer-/FX-Gebühr darf nicht als
# Anlageverlust in die TWR-Kette laufen.
#
# Live-Beweis 28.08. (0.4.9-rc3, Echtgeld-Konto): Activities-Flows 11.691,72 vs
# Equity-wirksame Gutschriften 11.659,36 → 32,36 $ Gebühren/FX-Differenz. Der
# bestehende #3049-Carry-Guard fängt nur market_equity <= 0; die KLEINE
# Über-Subtraktion (Brutto-Flow > Netto-Gutschrift) rutscht durch und wird als
# Kursverlust verkettet — auf Mini-Basis explodiert sie (32,36/350,90 = −9,2 %
# Fake-Tagesrendite → Engine twr −5,79 % statt +3,6 %, Console −9,18 %,
# Drawdown-Karte −96,79 %).
#
# Fix-Kontrakt (Owner-Direktive 28.08.: Kennzahl = Maschinenleistung, unabhängig
# von Einzahlungshöhe UND Transfergebühren):
#   - Positiver Flow, dessen Betrag den Equity-Sprung des Tages um einen
#     GEBÜHREN-PLAUSIBLEN Rest übersteigt (Alpaca international: 1,5 % je
#     Transfer, Cap 40 $ — ADR im Code), wird auf den Equity-wirksamen Betrag
#     begrenzt; der Rest wird als ``funding_costs`` ausgewiesen, NICHT als
#     Subperioden-Verlust. Withdrawals symmetrisch.
#   - Über-Subtraktion JENSEITS der Gebühren-Plausibilität bleibt (ehrlicher)
#     Handels-/Marktverlust — keine stille Schönung echter Verluste.
#   - ``pnl_net_of_deposits`` weist Strategie-P&L OHNE Funding-Kosten aus;
#     ``funding_costs`` kommt additiv ins Ergebnis-Dict (0.0 ohne Flows).
#   - Der bestehende Settlement-Lag-Carry (market_equity <= 0 → defer) bleibt
#     unverändert (Regression R4).

from __future__ import annotations

import pytest

from core.engine.perf_metrics import compute_cashflow_adjusted_returns

# Echte Live-Kurve 28.08. auf die tragenden Punkte verdichtet:
# (Datum, Equity, Flow lt. Activities am selben Punkt)
REAL_FIXTURE = [
    ("2026-07-23", 224.78, 0.0),
    ("2026-08-07", 224.84, 0.0),
    ("2026-08-08", 338.91, 115.66),  # Netto-Sprung 114.07 → Gebühr 1.59 (1.5 %)
    ("2026-08-25", 350.90, 0.0),
    ("2026-08-26", 6143.64, 5833.00),  # Netto-Sprung 5792.74 → Gebühr 40.26 (Cap 40)
    ("2026-08-27", 6144.19, 0.0),
    ("2026-08-28", 11896.74, 5743.06),  # Netto-Sprung 5752.55 → kein Overshoot
]


def _call(fixture):
    dates = [d for d, _, _ in fixture]
    equity = [e for _, e, _ in fixture]
    flows = [f for _, _, f in fixture]
    return compute_cashflow_adjusted_returns(dates, equity, flows), equity, flows


def test_r1_real_curve_fee_not_booked_as_loss():
    """Die drei echten Einzahlungen: TWR wird positiv (Maschinenleistung),
    die 41.85 $ Overshoots landen in funding_costs, nicht in der Kette."""
    result, equity, flows = _call(REAL_FIXTURE)

    # Erwartete Kette: Nicht-Flow-Tage normal; Flow-Tage mit Gebühren-Overshoot
    # neutral (eff. Flow = Equity-Sprung); Tag 3 (kein Overshoot) behält seinen
    # echten Restertrag (11896.74-5743.06)/6144.19.
    expected_growth = (
        (224.84 / 224.78)
        * 1.0  # 08-08: Overshoot 1.59 = Gebühr → neutralisiert
        * (350.90 / 338.91)
        * 1.0  # 08-26: Overshoot 40.26 = Gebühr (Cap 40) → neutralisiert
        * (6144.19 / 6143.64)
        * ((11896.74 - 5743.06) / 6144.19)  # 08-28: Flow < Sprung → unverändert
    )
    assert result["twr_pct"] == pytest.approx((expected_growth - 1.0) * 100.0, abs=1e-6)
    assert result["twr_pct"] > 3.0, "Maschinenleistung ist positiv, nicht −5.79 %"
    assert result["funding_costs"] == pytest.approx(1.59 + 40.26, abs=1e-6)
    # Strategie-P&L ohne Funding-Kosten:
    assert result["pnl_net_of_deposits"] == pytest.approx(
        11896.74 - 224.78 - (115.66 + 5833.00 + 5743.06) + (1.59 + 40.26), abs=1e-6
    )
    assert result["net_deposits"] == pytest.approx(115.66 + 5833.00 + 5743.06, abs=1e-6)


def test_r2_overshoot_beyond_fee_plausibility_stays_a_loss():
    """Overshoot weit über 1.5 %/40 $-Plausibilität = echter Verlust am
    Einzahlungstag — wird NICHT stillschweigend als Gebühr weggebucht."""
    fixture = [
        ("2026-08-01", 1000.0, 0.0),
        (
            "2026-08-02",
            1800.0,
            1000.0,
        ),  # Sprung 800, Flow 1000 → Overshoot 200 >> 15+Cap
    ]
    result, _, _ = _call(fixture)

    assert result["funding_costs"] == 0.0
    # Alt-Verhalten: (1800-1000)/1000 - 1 = -20 %
    assert result["twr_pct"] == pytest.approx(-20.0, abs=1e-9)


def test_r3_no_flows_byte_identical_plus_key():
    fixture = [
        ("2026-08-01", 100.0, 0.0),
        ("2026-08-02", 103.0, 0.0),
        ("2026-08-03", 101.0, 0.0),
    ]
    result, _, _ = _call(fixture)

    assert result["twr_pct"] == pytest.approx((103.0 / 100.0 * 101.0 / 103.0 - 1) * 100)
    assert result["funding_costs"] == 0.0
    assert result["net_deposits"] == 0.0


def test_r4_settlement_lag_carry_unchanged():
    """Regression: Flow >= Tages-Equity (Settlement-Lag) → weiter carry/defer,
    KEINE Fehlklassifikation als Gebühr."""
    fixture = [
        ("2026-08-01", 100.0, 0.0),
        ("2026-08-02", 100.5, 5000.0),  # Equity spiegelt den Flow noch nicht
        ("2026-08-03", 5105.0, 0.0),  # Sprung 5004.5 > Flow → kein Overshoot
    ]
    result, _, _ = _call(fixture)

    expected_growth = (100.5 / 100.0) * ((5105.0 - 5000.0) / 100.5)
    assert result["twr_pct"] == pytest.approx((expected_growth - 1.0) * 100.0, abs=1e-9)
    assert result["funding_costs"] == 0.0


def test_r5_withdrawal_fee_symmetric():
    """Abhebung 500, Equity fällt um 505 → 5 $ Transferkosten, kein Kursverlust."""
    fixture = [
        ("2026-08-01", 1000.0, 0.0),
        ("2026-08-02", 495.0, -500.0),
    ]
    result, _, _ = _call(fixture)

    assert result["funding_costs"] == pytest.approx(5.0, abs=1e-9)
    assert result["twr_pct"] == pytest.approx(0.0, abs=1e-9)
    assert result["pnl_net_of_deposits"] == pytest.approx(
        495.0 - 1000.0 - (-500.0) + 5.0, abs=1e-9
    )


def test_r6_single_point_failsoft_has_key():
    result = compute_cashflow_adjusted_returns(["2026-08-01"], [100.0], [0.0])
    assert result["twr_pct"] == 0.0
    assert result["funding_costs"] == 0.0
