"""#4125 Teil 2 (Plan §12d, 12f Punkt 2): ``plan_position_stops`` reicht den Grund durch.

Bisher trug das Plan-Dict nur ``reason`` (Text) und ``tier``; die Ausloese-Werte gingen hier
verloren. ``reason`` und ``tier`` bleiben unveraendert, ``verkaufsgrund`` kommt dazu und traegt
den Zeitpunkt der Pruefung (``now``).
"""

from datetime import datetime, timedelta, timezone

import pytest

from core.position_stop import evaluate_position_stop, plan_position_stops

pytestmark = pytest.mark.vc3

_JETZT = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)


def _position(symbol="X", qty=10.0, avg=100.0, current=93.5):
    return {
        "symbol": symbol,
        "qty": qty,
        "avg_entry_price": avg,
        "current_price": current,
    }


def test_plan_dict_traegt_den_verkaufsgrund():
    plaene = plan_position_stops(
        [_position()],
        {},
        entry_times={"X": _JETZT - timedelta(hours=30)},
        now=_JETZT,
    )
    assert len(plaene) == 1
    grund = plaene[0]["verkaufsgrund"]
    assert grund.klasse == "loss_cut"
    assert grund.werte["haltedauer_h"] == pytest.approx(30.0)
    assert grund.zeitpunkt == _JETZT


def test_reason_und_tier_bleiben_unveraendert():
    plan = plan_position_stops(
        [_position()],
        {},
        entry_times={"X": _JETZT - timedelta(hours=30)},
        now=_JETZT,
    )[0]
    assert plan["reason"].startswith("LOSS CUT:")
    assert plan["tier"] == "risk"
    assert set(plan) == {
        "symbol",
        "qty",
        "avg_entry_price",
        "current_price",
        "reason",
        "tier",
        "verkaufsgrund",
    }


def test_evaluate_setzt_den_zeitpunkt_der_pruefung():
    analyse = evaluate_position_stop(
        symbol="X",
        current_price=93.5,
        avg_entry_price=100.0,
        entry_time=_JETZT - timedelta(hours=30),
        now=_JETZT,
    )
    assert analyse.verkaufsgrund.zeitpunkt == _JETZT
