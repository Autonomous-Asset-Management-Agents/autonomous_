"""#3349 completion — the Earnings-Guard cache gets a PRODUCER, and the guard reaches
the desktop/[Global] money path.

Before this, nothing ever called a refresh: the cache stayed cold, a cold cache is
fail-open, so the guard was inert however it was configured.
"""

import inspect
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import core.engine.order_executor as oe
from core.engine.earnings_guard import (
    ensure_fresh_earnings_cache,
    load_cached_report_dates,
)

NOW = datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)
CIKS = {"AAPL": "0000320193", "HAL": "0000045012", "MSFT": "0000789019"}


def _sub(*dates):
    n = len(dates)
    return {
        "filings": {
            "recent": {
                "form": ["8-K"] * n,
                "items": ["2.02"] * n,
                "filingDate": list(dates),
            }
        }
    }


def _good(cik):
    return _sub("2026-09-16")


def _down(cik):
    return None


def _run(tmp_path, symbols, fetch, now=NOW, ciks=None):
    path = str(tmp_path / "earnings_dates.json")
    n = ensure_fresh_earnings_cache(
        symbols,
        now,
        cache_file=path,
        resolve_cik=(ciks or CIKS).get,
        fetch=fetch,
        pause_sec=0,
    )
    return n, path


def _blob(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_fills_the_cache_and_the_guard_can_read_it(tmp_path):
    n, path = _run(tmp_path, ["AAPL", "hal"], _good)
    assert n == 2
    dates, failed = load_cached_report_dates("HAL", path)
    assert not failed and dates[0].isoformat() == "2026-09-16"


def test_second_call_same_day_fetches_nothing(tmp_path):
    calls = []

    def fetch(cik):
        calls.append(cik)
        return _sub("2026-09-16")

    _run(tmp_path, ["AAPL", "HAL"], fetch)
    n, _ = _run(tmp_path, ["AAPL", "HAL"], fetch)
    assert n == 0 and len(calls) == 2


def test_a_symbol_entering_mid_day_is_added_without_refetching_the_rest(tmp_path):
    calls = []

    def fetch(cik):
        calls.append(cik)
        return _sub("2026-09-16")

    _run(tmp_path, ["AAPL"], fetch)
    n, path = _run(tmp_path, ["AAPL", "MSFT"], fetch)
    assert n == 1 and calls == [CIKS["AAPL"], CIKS["MSFT"]]
    assert load_cached_report_dates("AAPL", path)[0] is not None


def test_a_new_day_starts_from_an_empty_cache(tmp_path):
    _run(tmp_path, ["AAPL", "HAL"], _good)
    n, path = _run(tmp_path, ["AAPL"], _good, now=NOW + timedelta(days=1))
    assert n == 1
    assert "HAL" not in _blob(path)  # yesterday's entry must not linger


def test_no_cik_means_no_reports_and_no_network_call(tmp_path):
    def fetch(cik):
        raise AssertionError("must not be called for a symbol without a CIK")

    n, path = _run(tmp_path, ["SPY"], fetch)
    assert n == 0
    assert load_cached_report_dates("SPY", path) == (None, False)  # no veto


def test_fetch_failure_is_stamped_and_retried_only_after_the_backoff(tmp_path):
    _, path = _run(tmp_path, ["HAL"], _down)
    assert load_cached_report_dates("HAL", path) == (None, True)  # fail-closed (plan)

    n, _ = _run(tmp_path, ["HAL"], _good, now=NOW + timedelta(minutes=5))
    assert n == 0  # still inside the backoff
    n, _ = _run(tmp_path, ["HAL"], _good, now=NOW + timedelta(minutes=31))
    assert n == 1
    assert load_cached_report_dates("HAL", path)[1] is False


def test_an_outage_aborts_and_leaves_the_rest_cold(tmp_path):
    ciks = {f"S{i:02d}": f"{i:010d}" for i in range(1, 21)}
    n, path = _run(tmp_path, list(ciks), _down, ciks=ciks)
    assert n == 5  # ADR-EG-03
    stamped = [k for k, v in _blob(path).items() if k != "_meta" and v.get("error")]
    assert len(stamped) == 5
    # not fetched => cold => the guard does not block them
    assert load_cached_report_dates("S20", path) == (None, False)


def test_meta_entry_is_never_mistaken_for_a_symbol(tmp_path):
    _, path = _run(tmp_path, ["AAPL"], _good)
    assert load_cached_report_dates("_meta", path) == (None, False)


# --- seams ------------------------------------------------------------------


def _cfg(enabled):
    return SimpleNamespace(
        EARNINGS_GUARD_ENABLED=enabled,
        EARNINGS_GUARD_POST_DAYS=2,
        EARNINGS_GUARD_PRE_DAYS=0,
    )


def test_both_money_paths_use_the_shared_helper():
    src = inspect.getsource(oe)
    # definition + tenant path + desktop/[Global] path
    assert src.count("_earnings_guard_veto(") >= 3
    desktop = src[src.index("the Earnings-Proximity-Guard on the desktop/[Global]") :]
    desktop = desktop[: desktop.index("Part C (#2176")]
    assert "_earnings_guard_veto(symbol, action)" in desktop
    assert "_audit_skipped_signal(" in desktop and "qty = 0.0" in desktop


def test_helper_is_dark_by_default_and_never_reads_the_cache():
    with patch("config.get_config", return_value=_cfg(False)), patch(
        "core.engine.earnings_guard.earnings_guard_block"
    ) as block:
        assert oe._earnings_guard_veto("HAL", "BUY") is None
        block.assert_not_called()


def test_helper_blocks_a_buy_and_ignores_a_sell():
    veto = ("blocked:earnings", "earnings report 1 day ago")
    with patch("config.get_config", return_value=_cfg(True)), patch(
        "core.engine.earnings_guard.earnings_guard_block", return_value=veto
    ) as block:
        assert oe._earnings_guard_veto("HAL", "BUY") == veto
        assert oe._earnings_guard_veto("HAL", "SELL") is None
        assert block.call_count == 1


def test_helper_fails_open_on_any_error():
    with patch("config.get_config", return_value=_cfg(True)), patch(
        "core.engine.earnings_guard.earnings_guard_block",
        side_effect=RuntimeError("boom"),
    ):
        assert oe._earnings_guard_veto("HAL", "BUY") is None


def test_loop_producer_is_armed_only_single_flight_and_off_the_cycle():
    import core.engine.trading_loop as tl

    src = inspect.getsource(tl)
    hook = src[src.index("#3349: Earnings-Guard cache producer") :]
    hook = hook[: hook.index("# --- #3361")]
    assert '"EARNINGS_GUARD_ENABLED", False' in hook
    assert '"SIM_MODE", False' in hook
    assert "_eg_task.done()" in hook  # single-flight
    assert "asyncio.create_task(" in hook and "asyncio.to_thread(" in hook
    assert "await asyncio.to_thread" not in hook  # never blocks the cycle


# --- Review zu #3480 ---------------------------------------------------------


def test_finding_01_replace_is_retried_when_windows_holds_the_file(
    tmp_path, monkeypatch
):
    """os.replace raises PermissionError (WinError 32) while ANOTHER process has the
    target open (virus scanner, backup). That must not kill the producer run."""
    import core.engine.earnings_guard as eg

    real = eg.os.replace
    attempts = []

    def flaky(src, dst):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError(32, "used by another process")
        return real(src, dst)

    monkeypatch.setattr(eg.os, "replace", flaky)
    monkeypatch.setattr(eg.time, "sleep", lambda s: None)
    n, path = _run(tmp_path, ["AAPL"], _good)
    assert n == 1 and len(attempts) == 3
    assert load_cached_report_dates("AAPL", path)[0] is not None
    assert [p.name for p in tmp_path.iterdir()] == ["earnings_dates.json"]


def test_finding_01_a_write_that_never_succeeds_keeps_the_old_cache(
    tmp_path, monkeypatch
):
    import core.engine.earnings_guard as eg

    _, path = _run(tmp_path, ["AAPL"], _good)
    before = _blob(path)

    def locked(src, dst):
        raise PermissionError(32, "used by another process")

    monkeypatch.setattr(eg.os, "replace", locked)
    monkeypatch.setattr(eg.time, "sleep", lambda s: None)
    _run(tmp_path, ["AAPL", "MSFT"], _good)  # must not raise
    assert _blob(path) == before
    assert [p.name for p in tmp_path.iterdir()] == ["earnings_dates.json"]


def test_finding_01_reader_and_writer_share_one_lock():
    """In-process the order path (reader) and the producer thread (writer) are the two
    parties; a reader holding the file open is exactly what makes os.replace fail on
    Windows. Both go through the same lock."""
    import core.engine.earnings_guard as eg

    assert "_CACHE_LOCK" in inspect.getsource(eg.load_cached_report_dates)
    assert "_CACHE_LOCK" in inspect.getsource(eg._read_blob)
    assert "_CACHE_LOCK" in inspect.getsource(eg._write_blob)


def test_finding_01_order_path_reads_never_break_under_a_running_producer(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    ciks = {f"S{i:02d}": f"{i:010d}" for i in range(1, 9)}
    path = str(tmp_path / "earnings_dates.json")
    errors = []

    def produce(day):
        try:
            ensure_fresh_earnings_cache(
                list(ciks),
                NOW + timedelta(days=day),
                cache_file=path,
                resolve_cik=ciks.get,
                fetch=_good,
                pause_sec=0,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def read(_):
        for _i in range(200):
            dates, failed = load_cached_report_dates("S01", path)
            if failed:
                errors.append("reader saw a fetch-error stamp that was never written")

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(produce, d) for d in range(3)]
        futures += [pool.submit(read, i) for i in range(3)]
        for f in futures:
            f.result()
    assert not errors, errors[:2]
    assert [p.name for p in tmp_path.iterdir()] == ["earnings_dates.json"]


def test_finding_02_new_seams_log_through_the_module_logger():
    import core.engine.trading_loop as tl

    veto = inspect.getsource(oe._earnings_guard_veto)
    assert "logger.warning(" in veto and "logging.warning(" not in veto
    src = inspect.getsource(tl)
    hook = src[src.index("#3349: Earnings-Guard cache producer") :]
    hook = hook[: hook.index("# --- #3361")]
    assert "logger.warning(" in hook and "logging.warning(" not in hook
