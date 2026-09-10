# tests/unit/test_lstm_panel_persistence.py
# #2683 — persist the LSTM panel so the trend smoothing survives a restart.
#
# WHY: `LSTM_RANK_SMOOTHING_ENABLED` ships ARMED (#2680) — every ranking consumer reads the
# per-symbol MEDIAN over the last N snapshot DATES. But `LstmPanelStore` is memory-only
# (its own class doc: "In-memory only. A restart/redeploy resets the store"), and the sole
# writer records ONE snapshot per calendar day (`lstm_strategy.py`). The operations engine
# restarts at least daily (git_sha + first-decision evidence 03.-05.08.), so the window
# holds ONE date after every start → the median over one sample IS the point-in-time value.
# The smoothing is therefore inert in practice, exactly when it matters: the IVZ case
# (bought at point-in-time top-10 14:00 UTC, rotated out at rank 242/501 17:09 UTC) can
# recur. Without this, #2680 is a facade.
#
# DESIGN (issue Option A): the store stays DB-agnostic and takes an injectable adapter with
# `save(date, rows)` / `load(max_dates)`. Unit tests use a fake — no database needed. The
# concrete SQLAlchemy adapter and the boot hydration live behind `LSTM_PANEL_PERSISTENCE_ENABLED`.
#
# Contract pinned here:
#   R1  record_cycle persists best-effort through the adapter
#   R2  an adapter that raises NEVER reaches the caller (telemetry must not break the loop) + WARNING
#   R3  hydrate_from fills the in-memory window so cross_section_smoothed spans the loaded dates
#   R4  hydration is DATE-ordered and respects the snapshot cap (out-of-order rows are fine)
#   R5  hydration is idempotent
#   R6  hydration also rebuilds the per-symbol history (the report's score-trend reads it)
#   R7  flag OFF → no adapter interaction at all (byte-identical to today)
#   R8  SIM_MODE → never hydrate (a sim must not inherit the desktop's live panel)
#   R9  observability: the loaded date count is retrievable (so "armed but 1 day" is visible)
#   R10 config parity (BORA)

from __future__ import annotations

import importlib.util
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]


class _FakeAdapter:
    """Records what the store asked it to do; can be made to explode."""

    def __init__(self, rows=None, explode=False):
        self.saved = []
        self.loaded = 0
        self._rows = rows or []
        self._explode = explode

    def save(self, snapshot_date, rows):
        if self._explode:
            raise RuntimeError("db down")
        self.saved.append((snapshot_date, list(rows)))

    def load(self, max_dates):
        self.loaded += 1
        if self._explode:
            raise RuntimeError("db down")
        return list(self._rows)


def _store():
    from core.report.lstm_panel_store import LstmPanelStore

    return LstmPanelStore()


def _cfg(monkeypatch, *, persistence=True, sim=False, smoothing=True):
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            LSTM_PANEL_PERSISTENCE_ENABLED=persistence,
            SIM_MODE=sim,
            LSTM_RANK_SMOOTHING_ENABLED=smoothing,
            LSTM_RANK_SMOOTHING_WINDOW_DAYS=10,
            LSTM_RANK_SMOOTHING_MIN_COVERAGE=1,
        ),
    )


# ---------------------------------------------------------------------------
# R1/R2 — writing
# ---------------------------------------------------------------------------


def test_r1_record_cycle_persists_through_the_adapter(monkeypatch):
    _cfg(monkeypatch)
    s, ad = _store(), _FakeAdapter()
    s.attach_persistence(ad)

    s.record_cycle(date(2026, 8, 5), [("AAPL", 0.9), ("MSFT", 0.4)])

    assert len(ad.saved) == 1
    saved_date, rows = ad.saved[0]
    assert saved_date == date(2026, 8, 5)
    assert dict(rows) == {"AAPL": 0.9, "MSFT": 0.4}


def test_r2_adapter_failure_never_escapes_and_is_logged(monkeypatch, caplog):
    """A broken DB must cost telemetry, never the trading cycle."""
    _cfg(monkeypatch)
    s, ad = _store(), _FakeAdapter(explode=True)
    s.attach_persistence(ad)

    with caplog.at_level(logging.WARNING):
        s.record_cycle(date(2026, 8, 5), [("AAPL", 0.9)])  # must not raise

    assert s.cross_section_at(date(2026, 8, 5)) == {
        "AAPL": 0.9
    }, "in-memory write must stand"
    assert any(rec.levelno >= logging.WARNING for rec in caplog.records)


def test_r7_flag_off_means_no_adapter_interaction(monkeypatch):
    _cfg(monkeypatch, persistence=False)
    s, ad = _store(), _FakeAdapter(rows=[(date(2026, 8, 1), "AAPL", 0.5)])
    s.attach_persistence(ad)

    s.record_cycle(date(2026, 8, 5), [("AAPL", 0.9)])
    s.hydrate_from_persistence()

    assert ad.saved == [] and ad.loaded == 0


# ---------------------------------------------------------------------------
# R3/R4/R5/R6/R9 — hydration
# ---------------------------------------------------------------------------


def _ten_days():
    """10 dates; AAPL steady 0.5, SPIKE 0.1 except a 9.0 outlier on the last day."""
    rows = []
    for i in range(10):
        d = date(2026, 7, 20 + i)
        rows.append((d, "AAPL", 0.5))
        rows.append((d, "SPIKE", 9.0 if i == 9 else 0.1))
    return rows


def test_r3_hydration_makes_the_median_span_the_loaded_dates(monkeypatch):
    """The whole point: after a restart the median must be a MEDIAN, not one sample."""
    _cfg(monkeypatch)
    s, ad = _store(), _FakeAdapter(rows=_ten_days())
    s.attach_persistence(ad)

    s.hydrate_from_persistence()

    assert len(s.snapshot_dates()) == 10
    xsec = s.cross_section_smoothed(date(2026, 7, 29), window_days=10, min_coverage=1)
    # the spike day must not carry SPIKE above the steady name
    assert xsec["AAPL"] == pytest.approx(0.5)
    assert xsec["SPIKE"] == pytest.approx(0.1)
    assert xsec["AAPL"] > xsec["SPIKE"]


def test_r4_hydration_is_date_ordered_and_respects_the_cap():
    """Rows may arrive unordered; only the newest `snapshot_len` dates are kept."""
    from core.report.lstm_panel_store import LstmPanelStore

    s = LstmPanelStore(history_len=20, snapshot_len=3)
    rows = [
        (date(2026, 7, 1), "A", 1.0),
        (date(2026, 7, 5), "A", 5.0),
        (date(2026, 7, 3), "A", 3.0),
        (date(2026, 7, 4), "A", 4.0),
        (date(2026, 7, 2), "A", 2.0),
    ]
    s.hydrate_from(rows)

    assert s.snapshot_dates() == [date(2026, 7, 3), date(2026, 7, 4), date(2026, 7, 5)]
    assert s.cross_section_at(date(2026, 7, 5)) == {"A": 5.0}


def test_r5_hydration_is_idempotent():
    from core.report.lstm_panel_store import LstmPanelStore

    s = LstmPanelStore()
    rows = [(date(2026, 7, 1), "A", 1.0), (date(2026, 7, 2), "A", 2.0)]
    s.hydrate_from(rows)
    first = s.snapshot_dates()
    s.hydrate_from(rows)

    assert s.snapshot_dates() == first
    assert s.cross_section_at(date(2026, 7, 2)) == {"A": 2.0}


def test_r6_hydration_rebuilds_the_per_symbol_history():
    """`history()` feeds the report's score-trend — it must come back too, in date order."""
    from core.report.lstm_panel_store import LstmPanelStore

    s = LstmPanelStore()
    s.hydrate_from([(date(2026, 7, 2), "A", 2.0), (date(2026, 7, 1), "A", 1.0)])

    hist = s.history("A")
    assert [d for d, _ in hist] == [date(2026, 7, 1), date(2026, 7, 2)]
    assert [v for _, v in hist] == [1.0, 2.0]


def test_r9_snapshot_dates_exposes_the_effective_window():
    """Observability: "smoothing armed but only 1 day of history" must be visible."""
    from core.report.lstm_panel_store import LstmPanelStore

    s = LstmPanelStore()
    assert s.snapshot_dates() == []
    s.record_cycle(date(2026, 8, 5), [("A", 1.0)])
    assert s.snapshot_dates() == [date(2026, 8, 5)]


def test_hydration_survives_malformed_rows():
    """One bad row must not void the whole hydration (telemetry robustness)."""
    from core.report.lstm_panel_store import LstmPanelStore

    s = LstmPanelStore()
    s.hydrate_from(
        [
            (date(2026, 7, 1), "A", 1.0),
            (date(2026, 7, 2), "B", None),  # score None
            ("not-a-date", "C", 3.0),  # bad date
            (date(2026, 7, 3), "D", "x"),  # non-numeric
        ]
    )
    assert s.cross_section_at(date(2026, 7, 1)) == {"A": 1.0}


def test_r2b_load_failure_leaves_an_empty_but_working_store(monkeypatch, caplog):
    _cfg(monkeypatch)
    s, ad = _store(), _FakeAdapter(explode=True)
    s.attach_persistence(ad)

    with caplog.at_level(logging.WARNING):
        s.hydrate_from_persistence()  # must not raise

    assert s.snapshot_dates() == []
    assert any(rec.levelno >= logging.WARNING for rec in caplog.records)


# ---------------------------------------------------------------------------
# R8 — a sim must not inherit the live panel
# ---------------------------------------------------------------------------


def test_r8_sim_mode_never_hydrates(monkeypatch):
    """`core/sim/runner._seed_lstm_panel` builds the sim's own deterministic panel from the
    corpus; loading the desktop's live snapshots on top would make a sim run depend on
    whatever the operator's engine happened to have seen."""
    _cfg(monkeypatch, sim=True)
    s, ad = _store(), _FakeAdapter(rows=_ten_days())
    s.attach_persistence(ad)

    s.hydrate_from_persistence()

    assert ad.loaded == 0
    assert s.snapshot_dates() == []


# ---------------------------------------------------------------------------
# R10 — config parity (BORA)
# ---------------------------------------------------------------------------


def test_r10_enterprise_config_defines_the_flag():
    import config

    cfg = config.get_config()
    assert hasattr(cfg, "LSTM_PANEL_PERSISTENCE_ENABLED")
    assert (
        cfg.LSTM_PANEL_PERSISTENCE_ENABLED is True
    ), "default ON — without persistence the armed smoothing (#2680) is inert"


def test_r10b_oss_config_defines_the_flag():
    spec = importlib.util.spec_from_file_location(
        "config_oss_panel_persistence_parity", _AI_BOT / "config.oss.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "LSTM_PANEL_PERSISTENCE_ENABLED")
    assert module.LSTM_PANEL_PERSISTENCE_ENABLED is True


# ---------------------------------------------------------------------------
# ORM model + adapter wiring
# ---------------------------------------------------------------------------


def test_orm_model_exists_with_a_composite_key():
    """(date, symbol) is the natural key — one score per symbol per day, idempotent upsert."""
    from core.database.models import LstmPanelSnapshot

    assert LstmPanelSnapshot.__tablename__ == "lstm_panel_snapshots"
    pk = {c.name for c in LstmPanelSnapshot.__table__.primary_key.columns}
    assert pk == {"snapshot_date", "symbol"}


def test_get_store_hydrates_once(monkeypatch):
    """The singleton hydrates on first access — no boot wiring to forget, works for every
    entry point (engine, CLI, tests). Second access must NOT reload."""
    import core.report.lstm_panel_store as mod

    _cfg(monkeypatch)
    monkeypatch.setattr(mod, "_store_singleton", None, raising=False)
    calls = {"n": 0}

    def _fake_hydrate(store):
        calls["n"] += 1

    monkeypatch.setattr(mod, "_hydrate_singleton", _fake_hydrate, raising=False)

    mod.get_store()
    mod.get_store()
    assert calls["n"] == 1
