# tests/unit/test_sim_order_dispatch_and_news_isolation.py
# #2632 — four blockers between the sim and a decision-grade A/B. Findings 1+2 came from the
# corpus acceptance run (2026-08-06); 3+4 were found by running the harness once 1 was fixed,
# which is the point: each fix made the next defect observable.
#
# FINDING 1 (order dispatch — the 0-order/0-fill result): the acceptance run APPROVED a
#   rotation SELL for AAPL (rank 464/504, "full SELL dispatched") that never reached the broker:
#       ERROR order_executor.py:2563 Multi-tenant execution failed for AAPL:
#       1 validation error for MarketOrderRequest
#       id  Object has no attribute 'id' [type=no_such_attribute, input_value=UUID(...)]
#   Root cause, reproduced locally: `VirtualLiveBroker.submit_order` did
#   `order = copy.copy(order_data)` then `order.id = uuid.uuid4()` — it mutated the alpaca
#   REQUEST and reused it as the response. `MarketOrderRequest` is a Pydantic **v2** model with
#   13 declared fields and no `id`, so v2's strict __setattr__ refuses it. EVERY order died
#   this way. Live is unaffected: real Alpaca returns an Order object rather than echoing the
#   request, which is why live SELLs execute while the sim reported nothing.
#
# FINDING 2 (network leak): `core/nlp/headlines.py` fetches Google-News RSS with no SIM gate,
#   and NEWS_SENTIMENT_NLP_ENABLED defaults to **True** in both editions — so an offline run
#   made one live HTTP request per evaluated symbol (~500 on the full corpus). That breaks the
#   offline contract and is a determinism leak: replayed past date, today's headlines. The gate
#   is SIM-ONLY — live/paper keeps the feature the owner deliberately armed. Third leak after
#   the specialist (#2663) and the LatencyWatchdog (this branch); the check is now shared
#   (`core.sim.is_sim_mode`) instead of copied a third time.
#
# FINDING 3 (boot deadlock): with orders flowing, the run died at boot in
#   `_frozen_importlib._DeadlockError: _ModuleLock('core.report.pipeline')` — main thread and
#   SpecialistRegistry thread first-importing `core.report` concurrently. A RACE (an earlier
#   attempt got through), which alone disqualifies the harness.
#
# FINDING 4 (mislabelled sides): the first SUCCESSFUL run reported the AAPL exit as
#   `side='BUY', fill_price=303.41` — the seeded opening BUY — because fills were matched to
#   recorder events by symbol off a FIFO. An exit reported as an entry, on the exact axis this
#   harness exists to compare. Worse than a crash: a confident wrong answer.
#
# NOTE on the acceptance protocol that reported "determinism passed": it was vacuous. Both arms
# produced ZERO orders, so comparing them proved only that nothing happened twice. Determinism
# is meaningful only once a run dispatches — now 6 orders, two arms bit-identical.

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

# --- fields the sim consumers actually read off an order -----------------------
# fill_engine: status, symbol, qty, side, filled_qty, filled_avg_price, filled_at
# runner:      symbol, qty, side, filled_avg_price
_REQUIRED = (
    "id",
    "symbol",
    "qty",
    "side",
    "status",
    "submitted_at",
    "filled_qty",
    "filled_avg_price",
    "filled_at",
)


def _request(qty=20.0, side=None):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    return MarketOrderRequest(
        symbol="AAPL",
        qty=qty,
        side=side or OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        client_order_id=str(uuid.uuid4()),
    )


def _broker():
    """A VirtualLiveBroker with a stubbed clock and fill engine — we only exercise the
    request→order conversion here, not the fill maths (that has its own suite)."""
    from core.sim.broker import VirtualLiveBroker

    b = VirtualLiveBroker.__new__(VirtualLiveBroker)
    b.orders = []
    b.positions = {}
    b.cash = 100_000.0
    b.clock = SimpleNamespace(
        current_time=__import__("datetime").datetime(2026, 8, 4, 14, 0)
    )
    b.fill_engine = SimpleNamespace(process_orders=lambda orders: None)
    return b


# ---------------------------------------------------------------------------
# Finding 1 — the request object must never be mutated
# ---------------------------------------------------------------------------


def test_pydantic_request_has_no_id_field():
    """Pins WHY this breaks: the model genuinely has no `id`, so any assignment is a
    validation error rather than a harmless extra attribute."""
    from alpaca.trading.requests import MarketOrderRequest

    assert "id" not in MarketOrderRequest.model_fields


def test_submit_order_does_not_raise_on_a_pydantic_request():
    """The exact acceptance-run failure: an approved SELL must reach the broker."""
    b = _broker()
    order = b.submit_order(_request())  # must NOT raise a validation error
    assert order is not None


def test_submit_order_returns_a_record_carrying_every_consumed_field():
    b = _broker()
    order = b.submit_order(_request())
    missing = [f for f in _REQUIRED if not hasattr(order, f)]
    assert not missing, f"sim order record misses fields the sim reads: {missing}"


def test_submit_order_leaves_the_request_untouched():
    """No mutation of the caller's request — the executor may still read it afterwards."""
    req = _request()
    before = req.model_dump()
    b = _broker()
    b.submit_order(req)
    assert req.model_dump() == before
    assert not hasattr(req, "id")


def test_submit_order_preserves_symbol_qty_side():
    from alpaca.trading.enums import OrderSide

    b = _broker()
    order = b.submit_order(_request(qty=20.0, side=OrderSide.SELL))
    assert order.symbol == "AAPL"
    assert float(order.qty) == pytest.approx(20.0)
    assert order.side == OrderSide.SELL


def test_submit_order_ids_are_unique_and_registered():
    b = _broker()
    a = b.submit_order(_request())
    c = b.submit_order(_request())
    assert a.id != c.id
    assert len(b.orders) == 2
    assert b.get_order_by_id(str(a.id)) is a


def test_submit_order_stamps_a_tz_aware_submitted_at():
    """Pagination + the durable entry-time reconcile compare against tz-aware stamps."""
    b = _broker()
    order = b.submit_order(_request())
    assert order.submitted_at.tzinfo is not None


def test_submit_order_accepts_a_notional_request():
    """A notional (fractional-dollar) request carries no `qty`. The executor submits only
    qty orders today (no `notional=` in core/engine/order_executor.py), so this guards the
    conversion + logging path against a crash if that ever changes — it does NOT claim the
    fill engine prices notional orders (it reads float(order.qty) and would need work).
    """
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    req = MarketOrderRequest(
        symbol="AAPL",
        notional=250.0,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
    b = _broker()
    order = b.submit_order(req)
    assert order.symbol == "AAPL"
    assert getattr(order, "notional", None) is not None


def test_submit_order_still_accepts_a_plain_namespace():
    """The seeded opening fills (`runner._seed_positions`) push SimpleNamespace orders
    through the same list — the conversion must not regress that shape."""
    b = _broker()
    ns = SimpleNamespace(symbol="MSFT", qty="10", side="SELL", id=uuid.uuid4())
    order = b.submit_order(ns)
    assert order.symbol == "MSFT"
    assert hasattr(order, "status")


# ---------------------------------------------------------------------------
# Finding 2 — the news poller must be offline under SIM_MODE
# ---------------------------------------------------------------------------


_NOW = __import__("datetime").datetime(
    2026, 8, 4, 14, 0, tzinfo=__import__("datetime").timezone.utc
)


@pytest.fixture(autouse=True)
def _clean_rss_cache():
    """The RSS TTL cache is module-level — a cached entry would mask a fetch."""
    import core.nlp.headlines as mod

    mod._reset_cache_for_tests()
    yield
    mod._reset_cache_for_tests()


def _arm(monkeypatch, *, sim: bool, nlp_on: bool = True):
    """Arm the two flags this path reads, at the seam the module actually uses
    (``config.get_config()`` — the module holds no frozen constants)."""
    import config
    import core.nlp.headlines as mod

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            NEWS_SENTIMENT_NLP_ENABLED=nlp_on,
            NEWS_SENTIMENT_MAX_HEADLINES=8,
            SIM_MODE=sim,
        ),
        raising=False,
    )
    monkeypatch.setattr(mod, "_sim_mode", lambda: sim, raising=False)


def _forbid_network(monkeypatch):
    """Fail loudly on ANY fetch attempt — the offline contract, not a mocked stand-in."""
    import core.nlp.headlines as mod

    async def _boom(*a, **kw):
        raise AssertionError("SIM_MODE must not perform an RSS fetch")

    monkeypatch.setattr(mod, "_fetch_rss", _boom, raising=False)


async def test_headlines_empty_and_no_fetch_under_sim_mode(monkeypatch):
    """SIM_MODE → no fetch at all, caller gets []: the channel exists but is empty, so
    NewsSentimentAgent abstains (w=0) instead of voting on look-ahead news."""
    import core.nlp.headlines as mod

    _arm(monkeypatch, sim=True)
    _forbid_network(monkeypatch)

    assert await mod.produce_news_headlines("AAPL", _NOW) == []


async def test_headlines_still_fetch_when_sim_mode_is_off(monkeypatch):
    """Live/paper path unchanged — the gate is SIM-only, NOT a kill switch for the
    feature the owner deliberately armed (NEWS_SENTIMENT_NLP_ENABLED defaults True)."""
    import core.nlp.headlines as mod

    _arm(monkeypatch, sim=False)
    called = {"n": 0}

    async def _fake_rss(url):
        called["n"] += 1
        return "<rss></rss>"

    monkeypatch.setattr(mod, "_fetch_rss", _fake_rss, raising=False)

    await mod.produce_news_headlines("AAPL", _NOW)
    assert called["n"] == 1, "live path must still fetch exactly once per symbol"


async def test_sim_gate_does_not_override_the_dormant_flag(monkeypatch):
    """Flag-FIRST stays first: feature off → None (key absent), even under SIM."""
    import core.nlp.headlines as mod

    _arm(monkeypatch, sim=True, nlp_on=False)
    _forbid_network(monkeypatch)

    assert await mod.produce_news_headlines("AAPL", _NOW) is None


async def test_headlines_env_var_alone_is_enough(monkeypatch):
    """The sim CLI exports AAA_SIM_MODE before config import — the gate must honour env
    without any config help. Uses the REAL _sim_mode (no seam patch)."""
    import config
    import core.nlp.headlines as mod

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            NEWS_SENTIMENT_NLP_ENABLED=True, NEWS_SENTIMENT_MAX_HEADLINES=8
        ),
        raising=False,
    )
    monkeypatch.setenv("AAA_SIM_MODE", "true")
    _forbid_network(monkeypatch)

    assert await mod.produce_news_headlines("AAPL", _NOW) == []


def test_shared_sim_mode_helper_is_fail_safe_live(monkeypatch):
    """One definition for every SIM-gated component, and it must fail towards LIVE:
    an unreadable config may never switch a live component off."""
    import config
    from core.sim import is_sim_mode

    monkeypatch.delenv("AAA_SIM_MODE", raising=False)

    def _explode():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(config, "get_config", _explode, raising=False)
    assert is_sim_mode() is False


def test_latency_watchdog_uses_the_shared_sim_definition():
    """No fourth copy of the check: the watchdog must delegate, not re-implement."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "core" / "latency_watchdog.py"
    ).read_text(encoding="utf-8")
    assert "from core.sim import is_sim_mode" in src


# ---------------------------------------------------------------------------
# Finding 3 — import-lock deadlock at sim boot (found by running the fixed sim)
#
# _frozen_importlib._DeadlockError: deadlock detected by _ModuleLock('core.report.pipeline')
#   main thread : run_sim → _seed_lstm_panel → from core.report.lstm_panel_store import ...
#   registry thr: SpecialistRegistry refresh → RPT-6 → import core.report
# Both first-import `core.report` concurrently; CPython's per-module import lock detects the
# cycle and raises, killing the run before cycle 1 (exit 1, no result file). It is a RACE —
# an earlier run of the same command got through — which is disqualifying on its own: an A/B
# harness that crashes on thread timing can never be decision-grade.
# Fix: complete the import while the process is still single-threaded, i.e. BEFORE the engine
# boot starts any threads. Then every later import is an uncontended sys.modules hit.
# ---------------------------------------------------------------------------


def test_prewarm_loads_the_report_package_into_sys_modules():
    import sys

    from core.sim.runner import _prewarm_report_imports

    for mod in ("core.report.lstm_panel_store", "core.report.pipeline", "core.report"):
        sys.modules.pop(mod, None)

    _prewarm_report_imports()

    assert "core.report" in sys.modules
    assert "core.report.lstm_panel_store" in sys.modules
    assert "core.report.pipeline" in sys.modules, (
        "core.report.pipeline is the module the deadlock was detected on — the warm-up "
        "must fully initialise it, not just the parent package"
    )


def test_prewarm_is_idempotent_and_never_raises(monkeypatch):
    """Called at boot: a broken optional import must not be what kills a sim run."""
    import builtins

    from core.sim.runner import _prewarm_report_imports

    _prewarm_report_imports()
    _prewarm_report_imports()  # second call must be a no-op, not an error

    real_import = builtins.__import__

    def _explode(name, *a, **kw):
        if name.startswith("core.report"):
            raise ImportError("simulated broken report package")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _explode)
    _prewarm_report_imports()  # degrades, does not raise


def test_prewarm_runs_before_the_engine_boot():
    """Ordering IS the fix: warming after ``_init_trading_clients()`` (which starts the
    specialist refresh thread) would leave exactly the race we just hit."""
    import inspect

    from core.sim import runner

    src = inspect.getsource(runner.run_sim)
    warm = src.find("_prewarm_report_imports()")
    boot = src.find("_init_trading_clients()")
    assert warm != -1, "run_sim must warm the report imports"
    assert boot != -1
    assert warm < boot, "the warm-up must happen BEFORE any engine thread is started"


# ---------------------------------------------------------------------------
# Finding 4 — the result MISLABELS sides (found by reading the first successful run)
#
# The fixed run dispatched a rotation SELL for AAPL (broker log: "VirtualLiveBroker order:
# AAPL qty=20.0 SELL" → "Simulated SELL fill @ 309.335"), but the result JSON reported
#   {'symbol': 'AAPL', 'side': 'BUY', 'qty': 20.0, 'fill_price': 303.41}
# i.e. the SEEDED opening BUY. Cause: `_build_orders` walks the recorder EVENTS and matches
# each "executed" event to a broker order by symbol via `queue.pop(0)` — a symbol-only FIFO.
# The seeded opening BUY is appended to broker.orders at boot, so it sits at the head of the
# queue and is handed to the first event of that symbol; the real SELL never appears.
# Impact: an exit is reported as an entry, and any broker order without a matching event is
# invisible. For the rotation/exit A/B this harness exists to decide, that is worse than a
# crash — it produces a confident, wrong answer.
# Fix: fills come from the BROKER's order book (ground truth, seeds excluded); events supply
# only the outcomes that never reached the broker (blocked/other).
# ---------------------------------------------------------------------------


def _ns(**kw):
    return SimpleNamespace(**kw)


def _seed_order(symbol="AAPL", qty=20.0, price=303.41):
    from alpaca.trading.enums import OrderSide

    return _ns(
        id=uuid.uuid4(),
        symbol=symbol,
        side=OrderSide.BUY,
        qty=str(qty),
        filled_qty=str(qty),
        filled_avg_price=str(price),
        status="filled",
        is_seed=True,
    )


def _broker_order(symbol, side, qty, price, status="filled"):
    from alpaca.trading.enums import OrderSide

    return _ns(
        id=uuid.uuid4(),
        symbol=symbol,
        side=OrderSide.SELL if str(side).upper() == "SELL" else OrderSide.BUY,
        qty=str(qty),
        filled_qty=str(qty),
        filled_avg_price=str(price),
        status=status,
    )


def test_executed_sell_is_reported_as_a_sell_not_the_seed_buy():
    """The exact defect: seeded BUY at the head of the FIFO stole the AAPL row."""
    from core.sim.runner import _build_orders

    events = [{"symbol": "AAPL", "outcome": "executed", "reason": ""}]
    broker_orders = [
        _seed_order("AAPL", 20.0, 303.41),
        _broker_order("AAPL", "SELL", 20.0, 309.335),
    ]

    rows = _build_orders(events, broker_orders)
    aapl = [r for r in rows if r["symbol"] == "AAPL" and r["status"] == "filled"]

    assert len(aapl) == 1, f"expected exactly one AAPL fill row, got {aapl}"
    assert aapl[0]["side"] == "SELL", f"exit reported as {aapl[0]['side']}"
    assert aapl[0]["fill_price"] == pytest.approx(309.335)


def test_seeded_opening_fills_are_not_counted_as_sim_day_orders():
    """Seeds are the STARTING BOOK, not decisions of the replayed day — counting them
    inflates n_orders/n_filled and makes two arms with different seeds incomparable."""
    from core.sim.runner import _build_orders

    rows = _build_orders([], [_seed_order("AAPL")])
    assert rows == []


def test_broker_order_without_an_event_is_still_reported():
    """Ground truth is the broker: an order that reached it must never be invisible,
    whatever the recorder saw."""
    from core.sim.runner import _build_orders

    rows = _build_orders([], [_broker_order("BA", "BUY", 42.275495, 237.115)])
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BA" and rows[0]["side"] == "BUY"


def test_blocked_events_survive_with_their_reason():
    """The blocked ledger is why this harness can explain a 0-order day — keep it."""
    from core.sim.runner import _build_orders

    events = [
        {
            "symbol": "MSFT",
            "outcome": "blocked:cash",
            "reason": "insufficient buying power",
        }
    ]
    rows = _build_orders(events, [])
    assert len(rows) == 1
    assert rows[0]["status"] == "blocked"
    assert "insufficient buying power" in rows[0]["blocked_reason"]


def test_unfilled_broker_order_keeps_its_real_status():
    """No price data → the fill engine leaves the order 'accepted'. Reporting it as
    'filled' would overstate what the run proved."""
    from core.sim.runner import _build_orders

    o = _broker_order("XYZ", "BUY", 1.0, 0.0, status="accepted")
    o.filled_avg_price = None
    rows = _build_orders([], [o])
    assert rows[0]["status"] == "accepted"
    assert rows[0]["fill_price"] is None


def test_both_sides_of_the_same_symbol_are_reported():
    """A round trip inside one window (BUY then SELL) must show as two rows — the
    symbol-keyed FIFO could only ever report one."""
    from core.sim.runner import _build_orders

    rows = _build_orders(
        [],
        [
            _broker_order("NVDA", "BUY", 5.0, 100.0),
            _broker_order("NVDA", "SELL", 5.0, 110.0),
        ],
    )
    sides = [r["side"] for r in rows if r["symbol"] == "NVDA"]
    assert sides == ["BUY", "SELL"]


def test_headlines_module_documents_the_sim_gate():
    """The offline contract must be readable where the fetch lives."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "core" / "nlp" / "headlines.py"
    ).read_text(encoding="utf-8")
    assert "SIM_MODE" in src
