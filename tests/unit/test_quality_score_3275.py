"""#3275 (Part 2) — core/quality_score.py pure composite math (netzfrei).

Voller 9-Punkte-Piotroski aus current+prior + Composite v2 (z-Blend aus Novy-Marx
Gross-Profitability (kontinuierlich) und Piotroski-F/9) + Perzentil-Helfer. Alle
gegen konstruierte current+prior-Dicts mit bekannten Erwartungswerten.
"""

from __future__ import annotations

import pytest

from core import quality_score as qs


def _perfect():
    """Firma, die alle 9 Piotroski-Punkte besteht."""
    return {
        "net_income": 20.0,
        "total_assets": 100.0,
        "operating_cash_flow": 30.0,
        "total_liabilities": 30.0,
        "current_assets": 50.0,
        "current_liabilities": 25.0,
        "diluted_shares": 100.0,
        "gross_profit": 60.0,
        "revenue": 120.0,
        "prior_net_income": 10.0,
        "prior_total_assets": 100.0,
        "prior_total_liabilities": 40.0,
        "prior_current_assets": 40.0,
        "prior_current_liabilities": 25.0,
        "prior_diluted_shares": 100.0,
        "prior_gross_profit": 40.0,
        "prior_revenue": 100.0,
        "prior_operating_cash_flow": 15.0,
    }


def _zero():
    """Firma, die keinen einzigen Piotroski-Punkt besteht."""
    return {
        "net_income": -5.0,
        "total_assets": 100.0,
        "operating_cash_flow": -10.0,
        "total_liabilities": 50.0,
        "current_assets": 20.0,
        "current_liabilities": 25.0,
        "diluted_shares": 120.0,
        "gross_profit": 30.0,
        "revenue": 120.0,
        "prior_net_income": 10.0,
        "prior_total_assets": 100.0,
        "prior_total_liabilities": 40.0,
        "prior_current_assets": 50.0,
        "prior_current_liabilities": 25.0,
        "prior_diluted_shares": 100.0,
        "prior_gross_profit": 60.0,
        "prior_revenue": 150.0,
        "prior_operating_cash_flow": 5.0,
    }


def test_piotroski_perfect_nine():
    assert qs.piotroski_score(_perfect()) == 9


def test_piotroski_zero():
    assert qs.piotroski_score(_zero()) == 0


def test_piotroski_individual_points_toggle():
    # Toggle nur den Accrual-Punkt (CFO>NI): CFO unter NI drücken ⇒ genau −1.
    base = _perfect()
    assert qs.piotroski_score(base) == 9
    base2 = dict(base, operating_cash_flow=5.0)  # CFO(5) <= NI(20) ⇒ Accrual & CFO>0…
    # CFO(5)>0 bleibt wahr, CFO>NI wird falsch ⇒ genau ein Punkt weniger.
    assert qs.piotroski_score(base2) == 8


def test_piotroski_none_without_prior():
    # Ohne Vorperiode sind die 6 Δ-Punkte nicht bildbar ⇒ kein voller F-Score.
    cur_only = {
        "net_income": 20.0,
        "total_assets": 100.0,
        "operating_cash_flow": 30.0,
        "gross_profit": 60.0,
        "revenue": 120.0,
        "total_liabilities": 30.0,
        "current_assets": 50.0,
        "current_liabilities": 25.0,
        "diluted_shares": 100.0,
    }
    assert qs.piotroski_score(cur_only) is None


def test_gross_profitability():
    assert qs.gross_profitability(_perfect()) == pytest.approx(0.60)
    assert qs.gross_profitability({"gross_profit": 30.0}) is None


def test_composite_blend_and_monotonicity():
    good = qs.composite_quality(_perfect())
    bad = qs.composite_quality(_zero())
    assert good is not None and bad is not None
    assert 0.0 <= bad < good <= 1.0
    # 2-Komponenten-Blend: Mittel aus gesquashter GP und Piotroski/9.
    gp_score = qs._squash(0.60, qs._GP_NEUTRAL, qs._GP_SCALE)
    assert good == pytest.approx((gp_score + 9.0 / 9.0) / 2.0)


def test_composite_none_when_thin():
    # Fehlt die GP-Grundlage ⇒ kein Composite (Abstain-Grundlage).
    thin = dict(_perfect())
    thin.pop("gross_profit")
    assert qs.composite_quality(thin) is None
    # Fehlt der volle Piotroski (keine Vorperiode) ⇒ kein Composite.
    cur_only = {"gross_profit": 60.0, "total_assets": 100.0}
    assert qs.composite_quality(cur_only) is None


def test_quality_percentile():
    ref = [0.1, 0.2, 0.3, 0.4]
    assert qs.quality_percentile(0.35, ref) == pytest.approx(0.75)
    assert qs.quality_percentile(0.05, ref) == pytest.approx(0.0)
    assert qs.quality_percentile(0.5, [0.2]) is None  # < 2 distinct
    assert qs.quality_percentile(0.5, [0.2, 0.2, 0.2]) is None


# ===========================================================================
# Producer (quality_state_keys) — Vortagsreferenz (AC-7), Dark, Isolation
# ===========================================================================
import datetime as _dt  # noqa: E402

from core.implied_vol import ImpliedVolStore  # noqa: E402

_D1 = _dt.date(2026, 1, 1)
_D2 = _dt.date(2026, 1, 2)


def test_producer_dark_when_flag_off(monkeypatch, tmp_path):
    monkeypatch.setattr(qs, "_enabled", lambda: False)
    store = ImpliedVolStore(path=tmp_path / "q.json")
    keys = qs.quality_state_keys("AAPL", 100.0, _D2, store=store)
    assert keys == {
        "quality_score": None,
        "quality_reference": None,
        "quality_reference_date": None,
    }
    # dark path does NO work: nothing written to the store, no global leak.
    assert not (tmp_path / "q.json").exists()


def test_producer_serves_previous_session_reference(monkeypatch, tmp_path):
    monkeypatch.setattr(qs, "_enabled", lambda: True)
    # Feed → composite: pin the composite the producer computes for the symbol today.
    monkeypatch.setattr(qs, "_read_fundamentals", lambda *a, **k: {"_pinned": True})
    monkeypatch.setattr(
        qs, "composite_quality", lambda fund: 0.42 if fund.get("_pinned") else None
    )
    store = ImpliedVolStore(path=tmp_path / "q.json")
    # Seed the PREVIOUS session's cross-section (AC-7 reference).
    store.put_seeded_day(_D1, {"MSFT": 0.2, "NVDA": 0.8, "AMZN": 0.5})

    keys = qs.quality_state_keys("AAPL", 100.0, _D2, store=store)
    assert keys["quality_score"] == pytest.approx(0.42)  # today's composite
    assert sorted(keys["quality_reference"]) == [0.2, 0.5, 0.8]  # previous session
    assert keys["quality_reference_date"].startswith("2026-01-01")
    # today's composite is persisted for the NEXT session's reference (no leak beyond store)
    assert store.get("AAPL", _D2) == pytest.approx(0.42)


def test_producer_best_effort_on_missing_feed(monkeypatch, tmp_path):
    monkeypatch.setattr(qs, "_enabled", lambda: True)
    monkeypatch.setattr(qs, "_read_fundamentals", lambda *a, **k: None)  # cold cache
    store = ImpliedVolStore(path=tmp_path / "q.json")
    store.put_seeded_day(_D1, {"MSFT": 0.2, "NVDA": 0.8})
    keys = qs.quality_state_keys("AAPL", 100.0, _D2, store=store)
    assert keys["quality_score"] is None  # no composite today (honest gap)
    assert sorted(keys["quality_reference"]) == [0.2, 0.8]  # reference still served
