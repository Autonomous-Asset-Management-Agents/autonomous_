"""#2935 — Verdrängungs-Karussell: der Haltefrist-Riegel muss auch an Tag 0 greifen.

Live-Vorfall 17.08. (Echtgeld-Konto, 22 Ausführungen): 18 davon in einer Kette
BA → PCAR → PCG → SNDK → TRV → WDC → CMI → HAL/IFF/MU à ~1,00–1,40 $, jeder Verkauf
finanzierte den nächsten Kauf, jedes Mal mit exakt der Menge, die Minuten vorher gekauft
worden war. Ursache: `debate_position_swap` prüfte

    if min_hold_days > 0 and 0 < weakest_position.days_held < min_hold_days:

`days_held` ist ein Integer aus `.days` — für heute Gekauftes 0, und bei 0 griff der Riegel
nicht. Die dokumentierte Absicht war Fail-open bei UNBEKANNTEM Alter; „unbekannt" und „vor
Minuten gekauft" waren aber derselbe Wert 0. Damit rutschten ausgerechnet die frischesten
Positionen durch — der Splitter aus dem Vorzyklus ist nach Marktwert immer der Schwächste.

Diese Suite pinnt: Tag 0 mit BEKANNTEM Alter ist geschützt, unbekanntes Alter bleibt
fail-open, alles jenseits der Frist verhält sich wie bisher, und der Rückfall über
DISPLACEMENT_RESPECTS_MIN_HOLD=false ist byte-identisch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.portfolio_manager import OpportunityScore, PortfolioManager, PositionScore


def _pm(max_positions: int = 10) -> PortfolioManager:
    client = MagicMock()
    client.get_account.return_value = MagicMock(equity="346.86")
    client.get_all_positions.return_value = []
    return PortfolioManager(
        client, total_capital=346.86, max_positions=max_positions, user_id="t"
    )


def _weakest(
    days_held: int,
    age_known: bool,
    *,
    score: float = 40.0,
    market_value: float = 1.20,
    pnl_pct: float = 0.0,
) -> PositionScore:
    """Die schwächste Position — standardmäßig der 1,20-$-Splitter aus dem Vorfall."""
    kwargs = {
        "symbol": "PCAR",
        "qty": 0.0079,
        "avg_entry": 130.85,
        "current_price": 130.76,
        "market_value": market_value,
        "unrealized_pnl": 0.0,
        "unrealized_pnl_pct": pnl_pct,
        "total_score": score,
        "days_held": days_held,
        "last_updated": datetime.now(timezone.utc),
    }
    # age_known existiert erst mit dem Fix; vor dem Fix schlaegt der Test genau deshalb fehl.
    try:
        return PositionScore(age_known=age_known, **kwargs)
    except TypeError:
        return PositionScore(**kwargs)


def _opportunity(score: float = 80.0) -> OpportunityScore:
    """Eine klar bessere Gelegenheit: score_diff > 15 ⇒ 'Strong upgrade'-Zweig."""
    return OpportunityScore(symbol="PCG", current_price=17.59, total_score=score)


@pytest.fixture
def min_hold_20(monkeypatch):
    """Betriebszustand der Installation: Riegel scharf, Frist 20 Tage."""
    import config

    cfg = config.get_config()
    monkeypatch.setattr(cfg, "DISPLACEMENT_RESPECTS_MIN_HOLD", True, raising=False)
    monkeypatch.setattr(cfg, "SMART_EXIT_MIN_HOLD_DAYS", 20.0, raising=False)


def test_position_bought_today_is_not_displaced(min_hold_20):
    """Der Kern des Vorfalls: heute gekauft (Alter bekannt) ⇒ keine Verdrängung."""
    pm = _pm()
    should_swap, reason = pm.debate_position_swap(
        _opportunity(), _weakest(days_held=0, age_known=True)
    )
    assert should_swap is False
    assert (
        "minimum holding period" in reason
    ), f"erwartet wurde das Haltefrist-Veto, bekam: {reason!r}"


def test_veto_beats_a_strong_score_upgrade(min_hold_20):
    """Der Riegel ist ein VETO, kein Argument (Lehre aus #2696): die Entscheidung beginnt
    mit `if score_diff > 15: should_swap = True` und überspringt alle args_against."""
    pm = _pm()
    should_swap, reason = pm.debate_position_swap(
        _opportunity(score=99.0), _weakest(days_held=0, age_known=True, score=1.0)
    )
    assert should_swap is False, "score_diff 98 darf das Veto nicht aushebeln"
    assert "minimum holding period" in reason


def test_unknown_age_stays_fail_open(min_hold_20):
    """Dokumentierte Absicht, unverändert: ohne Handelshistorie ist das Alter unbekannt,
    und 'wir wissen es nicht' ist kein Schutzgrund."""
    pm = _pm()
    should_swap, _ = pm.debate_position_swap(
        _opportunity(), _weakest(days_held=0, age_known=False)
    )
    assert should_swap is True


def test_beyond_min_hold_unchanged(min_hold_20):
    pm = _pm()
    should_swap, _ = pm.debate_position_swap(
        _opportunity(), _weakest(days_held=20, age_known=True)
    )
    assert should_swap is True


def test_within_min_hold_day_five_still_vetoed(min_hold_20):
    """Regression gegen die halbe Behebung aus #2696: ab Tag 1 wirkte der Riegel schon."""
    pm = _pm()
    should_swap, reason = pm.debate_position_swap(
        _opportunity(), _weakest(days_held=5, age_known=True)
    )
    assert should_swap is False
    assert "minimum holding period" in reason


def test_rollback_flag_off_is_byte_identical(monkeypatch):
    """DISPLACEMENT_RESPECTS_MIN_HOLD=false ⇒ Verhalten wie vor diesem PR."""
    import config

    cfg = config.get_config()
    monkeypatch.setattr(cfg, "DISPLACEMENT_RESPECTS_MIN_HOLD", False, raising=False)
    monkeypatch.setattr(cfg, "SMART_EXIT_MIN_HOLD_DAYS", 20.0, raising=False)
    pm = _pm()
    should_swap, _ = pm.debate_position_swap(
        _opportunity(), _weakest(days_held=0, age_known=True)
    )
    assert should_swap is True


def test_no_position_yet_proceeds():
    """Leeres Buch: kein Kandidat ⇒ unverändert erlaubt."""
    pm = _pm()
    should_swap, reason = pm.debate_position_swap(_opportunity(), None)
    assert should_swap is True
    assert "No existing positions" in reason
