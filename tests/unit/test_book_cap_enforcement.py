"""#2886 — Book-Cap-Invariante: broker-verifiziertes Slot-Gate (fail-closed),
Konto-Identitäts-Tracking und book_overflow-Rückbau.

Vorfall 14.08. (gemessen, DB der Desktop-Installation): Nach einem Konto-Wechsel
zählte der Slot-Check 0/10 belegte Slots (leerer/stale interner PM = fail-open)
und kaufte bei vollem Buch weiter — 12 Positionen statt 10. Diese Suite pinnt:
(a) die Regression exakt (leerer Zähler kann die Obergrenze nicht mehr aushebeln),
(b) fail-closed bei unbestätigtem Broker-Stand — nur für Neukäufe,
(c) den geordneten Rückbau (min-hold bindend, schwächster Rang zuerst, Cap),
(d) Konto-Wechsel ⇒ Reset + Rehydration,
(e) Flag OFF ⇒ byte-identisches Legacy-Verhalten (inkl. des Bugs — Rollback-Pfad).
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import config
from core.portfolio_manager import OpportunityScore, PortfolioManager


def _mk_broker_pos(symbol: str, qty: float = 10.0, price: float = 100.0):
    return {
        "symbol": symbol,
        "qty": qty,
        "avg_entry_price": price,
        "current_price": price,
        "market_value": qty * price,
        "unrealized_pl": 0.0,
    }


def _mk_client(n_positions: int, account_id: str = "ACC-A", equity: float = 150_000.0):
    client = MagicMock()
    client.get_account.return_value = SimpleNamespace(
        equity=str(equity), account_number=account_id
    )
    client.get_all_positions.return_value = [
        _mk_broker_pos(f"SY{i:02d}") for i in range(n_positions)
    ]
    return client


def _mk_pm(client, max_positions: int = 10) -> PortfolioManager:
    return PortfolioManager(
        client, total_capital=150_000.0, max_positions=max_positions, user_id="t"
    )


def _opp(symbol: str = "NEWN") -> OpportunityScore:
    return OpportunityScore(symbol=symbol, current_price=100.0, total_score=80.0)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(
        config.get_config(), "BOOK_CAP_ENFORCEMENT_ENABLED", True, raising=False
    )


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.setattr(
        config.get_config(), "BOOK_CAP_ENFORCEMENT_ENABLED", False, raising=False
    )


# ---------------------------------------------------------------------------
# (a) Regression 14.08. — leerer Zähler bei erreichbarem Broker
# ---------------------------------------------------------------------------


def test_full_broker_book_never_grants_a_free_slot(flag_on):
    """Broker meldet 10/10 — ein frisch erzeugter (leerer) PM darf keinen
    'Room for new position'-Freischein mehr ausstellen."""
    pm = _mk_pm(_mk_client(10))
    ok, reason, _ = pm.should_open_new_position(_opp())
    assert "Room for new position" not in reason
    assert not (ok and "Room" in reason)


def test_broker_error_fails_closed_for_new_names(flag_on):
    """get_all_positions wirft ⇒ Neukauf blockt mit slot_unverified (fail-closed)."""
    client = _mk_client(0)
    client.get_all_positions.side_effect = RuntimeError("API down")
    pm = _mk_pm(client)
    ok, reason, _ = pm.should_open_new_position(_opp())
    assert ok is False
    assert "slot_unverified" in reason


def test_flag_off_preserves_legacy_fail_open(flag_off):
    """Rollback-Pfad: Flag OFF ⇒ exakt das Legacy-Verhalten — der leere Zähler
    öffnet Case 1 (der 14.08.-Bug, bewusst gepinnt als Byte-Identität)."""
    client = _mk_client(0)
    client.get_all_positions.side_effect = RuntimeError("API down")
    pm = _mk_pm(client)
    ok, reason, _ = pm.should_open_new_position(_opp())
    assert ok is True
    assert "Room for new position" in reason


# ---------------------------------------------------------------------------
# (b) Overflow: Bestand > Obergrenze ⇒ alle BUYs geblockt (auch Top-ups)
# ---------------------------------------------------------------------------


def test_overflow_blocks_new_names_and_topups(flag_on):
    pm = _mk_pm(_mk_client(12))
    ok, reason, _ = pm.should_open_new_position(_opp("NEWN"))
    assert ok is False and "book_overflow" in reason
    held = pm.refresh_positions()
    some_held = next(iter(held))
    ok2, reason2, _ = pm.should_open_new_position(_opp(some_held))
    assert ok2 is False and "book_overflow" in reason2


def test_at_cap_routes_to_swap_debate_not_overflow(flag_on):
    """Genau AM Limit (10/10) ist kein Overflow — der normale Verdrängungs-Pfad
    (Case 2) bleibt zuständig."""
    pm = _mk_pm(_mk_client(10))
    ok, reason, _ = pm.should_open_new_position(_opp())
    assert "book_overflow" not in reason


# ---------------------------------------------------------------------------
# (c) Rückbau-Selektion (pure Funktion) — schwächster Rang zuerst, min-hold bindend
# ---------------------------------------------------------------------------


def _score(days_held: float):
    return SimpleNamespace(
        days_held=days_held, qty=10.0, current_price=100.0, avg_entry=100.0
    )


@pytest.mark.mutates_global_state
def test_unwind_selects_weakest_ranks_beyond_min_hold():
    from core.engine.book_overflow import select_overflow_unwind

    positions = {
        "AAA": _score(30),  # rank 5  — stark, bleibt
        "BBB": _score(30),  # rank 40 — schwächster ⇒ zuerst
        "CCC": _score(30),  # rank 25 — zweitschwächster
        "DDD": _score(3),  # rank 90 — schwächster, aber unter min-hold ⇒ geschützt
    }
    ranks = {"AAA": 5, "BBB": 40, "CCC": 25, "DDD": 90}
    sel = select_overflow_unwind(
        positions,
        rank_of=lambda s: ranks.get(s),
        max_positions=2,  # 4 gehalten, Ziel 2 ⇒ Überhang 2
        min_hold_days=20.0,
        budget=2,
    )
    assert sel == ["BBB", "CCC"]


def test_unwind_respects_budget_and_targets_only_the_excess():
    from core.engine.book_overflow import select_overflow_unwind

    positions = {f"S{i}": _score(30) for i in range(12)}
    ranks = {f"S{i}": i + 1 for i in range(12)}
    sel = select_overflow_unwind(
        positions,
        rank_of=lambda s: ranks.get(s),
        max_positions=10,
        min_hold_days=20.0,
        budget=5,
    )
    # Überhang = 2 < Budget 5 ⇒ genau 2, die schwächsten Ränge (11, 12)
    assert sel == ["S11", "S10"]


def test_unwind_without_rank_is_fail_safe_hold():
    from core.engine.book_overflow import select_overflow_unwind

    positions = {"AAA": _score(30), "BBB": _score(30), "CCC": _score(30)}
    sel = select_overflow_unwind(
        positions,
        rank_of=lambda s: None,
        max_positions=2,
        min_hold_days=20.0,
        budget=2,
    )
    assert sel == []


def test_unwind_empty_when_book_within_cap():
    from core.engine.book_overflow import select_overflow_unwind

    positions = {"AAA": _score(30)}
    sel = select_overflow_unwind(
        positions,
        rank_of=lambda s: 1,
        max_positions=10,
        min_hold_days=20.0,
        budget=2,
    )
    assert sel == []


# ---------------------------------------------------------------------------
# (d) Konto-Wechsel ⇒ Reset + Rehydration
# ---------------------------------------------------------------------------


def test_account_switch_resets_internal_state(flag_on):
    client = _mk_client(10, account_id="ACC-A")
    pm = _mk_pm(client)
    pm.refresh_positions()
    pm.record_trade("SY00", "buy")
    assert pm._trade_history.get("SY00")

    # Konto wechselt: andere Account-ID, anderes (leeres) Buch
    client.get_account.return_value = SimpleNamespace(
        equity="500.0", account_number="ACC-B"
    )
    client.get_all_positions.return_value = []
    pm.refresh_positions()
    assert not pm._trade_history.get(
        "SY00"
    ), "Trade-Historie des alten Kontos darf das neue Konto nicht steuern"
    assert len(pm._position_scores) == 0


def test_flag_off_account_switch_keeps_legacy_state(flag_off):
    client = _mk_client(10, account_id="ACC-A")
    pm = _mk_pm(client)
    pm.refresh_positions()
    pm.record_trade("SY00", "buy")
    client.get_account.return_value = SimpleNamespace(
        equity="500.0", account_number="ACC-B"
    )
    pm.refresh_positions()
    assert pm._trade_history.get("SY00"), "Flag OFF: kein neues Reset-Verhalten"


# ---------------------------------------------------------------------------
# (e) Flag-Default: dark in BEIDEN Editionen
# ---------------------------------------------------------------------------


def test_flag_default_armed_enterprise():
    # Owner-Entscheid 16.08.: Invariante ist per Default scharf; env False bleibt
    # der byte-identische Rollback-Hebel (oben explizit getestet).
    assert config.get_config().BOOK_CAP_ENFORCEMENT_ENABLED is True


@pytest.mark.mutates_global_state
def test_flag_default_armed_oss_parity():
    import importlib.util
    from pathlib import Path

    oss = Path(config.__file__).parent / "config.oss.py"
    spec = importlib.util.spec_from_file_location("config_oss_bookcap", oss)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.BOOK_CAP_ENFORCEMENT_ENABLED is True
