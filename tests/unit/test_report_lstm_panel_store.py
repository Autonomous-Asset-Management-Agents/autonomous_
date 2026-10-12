# tests/unit/test_report_lstm_panel_store.py
# RPT-8 (Epic #1998) — the live LSTM panel store + reader that wire the report's
# quant view to the ENGINE's real per-cycle LSTM scores (raw score, score-trend,
# own-percentile) and cross-sectional rank/percentile — PIT-correct, dormant,
# thread-safe, bounded.
#
# Test surfaces:
#   1. record_cycle appends a per-symbol (date, score) history AND a cross-section
#      snapshot; the per-symbol history is a bounded rolling window; the snapshot
#      map is bounded to the last few days.
#   2. Same calendar day re-recorded -> one point per day (latest wins) so the
#      score-trend is a stable daily progression regardless of engine cadence.
#   3. cross_section_at(as_of) returns the latest snapshot with date <= as_of;
#      empty / no-eligible-date -> {} (honest gap, never wrong numbers).
#   4. history(symbol) returns [] for an unknown symbol (graceful).
#   5. EngineLstmPanelReader.series(symbol) PIT-filters to <= its as_of; a future
#      day in the store is never leaked into an earlier report.
#   6. EngineLstmPanelReader.cross_section delegates to the store.
#   7. get_store() is a process singleton; reset() clears it.
#   8. Thread-safety smoke: concurrent writers + readers never raise.

import threading
from datetime import date

from core.report.lstm_panel_store import (
    EngineLstmPanelReader,
    LstmPanelStore,
    get_store,
)


def _mk(symbols_scores):
    """[(sym, score), ...] as the engine's _lstm_rank_cache shape (sorted desc)."""
    return sorted(
        [(s, float(v)) for s, v in symbols_scores], key=lambda x: x[1], reverse=True
    )


def test_record_cycle_builds_history_and_snapshot():
    st = LstmPanelStore()
    st.record_cycle(date(2026, 5, 1), _mk([("NVDA", 4.10), ("AAPL", 1.2)]))
    assert st.history("NVDA") == [(date(2026, 5, 1), 4.10)]
    xs = st.cross_section_at(date(2026, 5, 1))
    assert xs == {"NVDA": 4.10, "AAPL": 1.2}


def test_history_is_bounded_rolling_window():
    st = LstmPanelStore(history_len=3)
    for i, d in enumerate([date(2026, 5, d) for d in (1, 2, 3, 4, 5)]):
        st.record_cycle(d, _mk([("NVDA", float(i))]))
    hist = st.history("NVDA")
    assert len(hist) == 3  # only the last 3 days retained
    assert [s for _, s in hist] == [2.0, 3.0, 4.0]


def test_same_day_recorded_twice_keeps_latest_one_point():
    st = LstmPanelStore()
    st.record_cycle(date(2026, 5, 1), _mk([("NVDA", 3.5)]))
    st.record_cycle(date(2026, 5, 1), _mk([("NVDA", 4.10)]))  # later cycle same day
    hist = st.history("NVDA")
    assert hist == [(date(2026, 5, 1), 4.10)]  # one point per day, latest wins
    assert st.cross_section_at(date(2026, 5, 1)) == {"NVDA": 4.10}


def test_snapshot_map_is_bounded():
    st = LstmPanelStore(snapshot_len=2)
    for d in (1, 2, 3):
        st.record_cycle(date(2026, 5, d), _mk([("NVDA", float(d))]))
    # only the last 2 daily snapshots survive; day 1 evicted
    assert st.cross_section_at(date(2026, 5, 1)) == {}
    assert st.cross_section_at(date(2026, 5, 2)) == {"NVDA": 2.0}
    assert st.cross_section_at(date(2026, 5, 3)) == {"NVDA": 3.0}


def test_cross_section_at_returns_latest_on_or_before():
    st = LstmPanelStore()
    st.record_cycle(date(2026, 5, 1), _mk([("NVDA", 1.0)]))
    st.record_cycle(date(2026, 5, 3), _mk([("NVDA", 3.0)]))
    # as_of between recorded days -> the latest snapshot <= as_of (day 1)
    assert st.cross_section_at(date(2026, 5, 2)) == {"NVDA": 1.0}
    # as_of after all -> latest (day 3)
    assert st.cross_section_at(date(2026, 5, 9)) == {"NVDA": 3.0}
    # as_of before all -> empty honest gap
    assert st.cross_section_at(date(2026, 4, 30)) == {}


def test_unknown_symbol_history_is_empty():
    st = LstmPanelStore()
    st.record_cycle(date(2026, 5, 1), _mk([("NVDA", 1.0)]))
    assert st.history("TSLA") == []


def test_reader_series_is_pit_filtered():
    st = LstmPanelStore()
    st.record_cycle(date(2026, 5, 1), _mk([("NVDA", 1.0)]))
    st.record_cycle(date(2026, 5, 2), _mk([("NVDA", 2.0)]))
    st.record_cycle(date(2026, 5, 3), _mk([("NVDA", 3.0)]))  # AFTER the report as_of
    reader = EngineLstmPanelReader(st, date(2026, 5, 2))
    assert reader.series("NVDA") == [(date(2026, 5, 1), 1.0), (date(2026, 5, 2), 2.0)]
    # day 3 (future vs as_of) is NOT leaked
    assert (date(2026, 5, 3), 3.0) not in reader.series("NVDA")


def test_reader_cross_section_delegates():
    st = LstmPanelStore()
    st.record_cycle(date(2026, 5, 1), _mk([("NVDA", 4.10), ("AAPL", 1.2)]))
    reader = EngineLstmPanelReader(st, date(2026, 5, 1))
    assert reader.cross_section(date(2026, 5, 1)) == {"NVDA": 4.10, "AAPL": 1.2}


def test_reader_empty_store_is_graceful():
    st = LstmPanelStore()
    reader = EngineLstmPanelReader(st, date(2026, 5, 1))
    assert reader.series("NVDA") == []
    assert reader.cross_section(date(2026, 5, 1)) == {}


def test_get_store_is_singleton_and_reset_clears():
    a = get_store()
    b = get_store()
    assert a is b
    a.record_cycle(date(2026, 5, 1), _mk([("NVDA", 1.0)]))
    assert a.history("NVDA")
    a.reset()
    assert a.history("NVDA") == []


def test_end_to_end_panel_flows_into_factset():
    # The load-bearing contract: the live panel must feed BOTH the "solo" values
    # (raw score, own-history percentile, score-trend) AND the cross-sectional
    # rank/percentile into the FactSet — Georg's requirement to keep the solo
    # values *in addition to* the universe rank. Pure (no pandas/engine).
    from core.report.fact_set import InMemoryDataContext, build_fact_set

    st = LstmPanelStore()
    # a rising score trend during a price pullback (the NVDA-benchmark shape)
    for d, sc in [
        (date(2026, 4, 26), 1.55),
        (date(2026, 4, 27), 2.52),
        (date(2026, 4, 28), 3.29),
        (date(2026, 4, 29), 3.58),
        (date(2026, 4, 30), 4.10),
    ]:
        st.record_cycle(d, [("NVDA", sc), ("AAPL", 0.4), ("MSFT", 0.1)])
    reader = EngineLstmPanelReader(st, date(2026, 5, 1))

    bars = [(date(2026, 4, 26 + i), 200.0 - i) for i in range(5)]
    ctx = InMemoryDataContext(
        bars=bars,
        lstm_series=reader.series("NVDA"),
        lstm_cross_section=reader.cross_section(date(2026, 5, 1)),
    )
    fs = build_fact_set("NVDA", date(2026, 5, 1), ctx)

    # solo values (from the per-symbol series)
    assert fs.lstm_score.value == 4.10
    assert fs.lstm_own_percentile.value == 80.0  # 4.10 = highest of its 5 scores
    assert [round(s, 2) for s in fs.lstm_score_trend.value] == [
        1.55,
        2.52,
        3.29,
        3.58,
        4.10,
    ]
    # cross-sectional values (from the snapshot)
    assert fs.lstm_xsec_rank.value == 1  # NVDA top of the 3-symbol universe
    assert fs.lstm_xsec_n.value == 3


def test_thread_safety_smoke():
    st = LstmPanelStore()
    errors = []

    def writer():
        try:
            for d in range(1, 28):
                st.record_cycle(
                    date(2026, 5, d), _mk([("NVDA", float(d)), ("AAPL", 0.5)])
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def reader():
        try:
            for _ in range(200):
                st.history("NVDA")
                st.cross_section_at(date(2026, 5, 15))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(3)] + [
        threading.Thread(target=reader) for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], f"concurrent access raised: {errors[:3]}"
