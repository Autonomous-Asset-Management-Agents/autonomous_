# tests/unit/test_obs_trading_cycle_context.py
# INF-13 OBS-4 (#2637): the trading fan-out must run each symbol inside a
# ``trading.symbol_eval`` span parented to the per-cycle ``trading.cycle`` root,
# so the round-table ``model.inference`` spans — which end on a background thread
# via ``asyncio.to_thread`` — join the SAME trade trace instead of being detached
# roots.
import asyncio

import pytest
from opentelemetry import trace as trace_api
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from core.engine.trading_loop import _run_symbol_eval


def _provider():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_symbol_eval_child_of_cycle_and_carries_across_thread_hop():
    provider, exporter = _provider()
    tracer = provider.get_tracer("test")
    cycle = tracer.start_span("trading.cycle")
    cycle_ctx = trace_api.set_span_in_context(cycle)

    async def factory():
        # model.inference runs on a worker thread (asyncio.to_thread) exactly like
        # AsyncAIAgent.vote -> _run_inference in the round table.
        def _infer():
            with tracer.start_as_current_span("model.inference"):
                pass

        await asyncio.to_thread(_infer)
        return "vote"

    result = asyncio.run(_run_symbol_eval(tracer, cycle_ctx, "AAPL", factory))
    cycle.end()

    assert result == "vote"  # behaviour-neutral: the factory result passes through
    spans = {s.name: s for s in exporter.get_finished_spans()}
    sev = spans["trading.symbol_eval"]
    inf = spans["model.inference"]
    # symbol_eval parents to the cycle root
    assert sev.parent is not None
    assert sev.parent.span_id == cycle.get_span_context().span_id
    assert sev.attributes.get("symbol") == "AAPL"
    # model.inference (ended on a worker thread) joins the SAME trace, under symbol_eval
    assert inf.parent is not None
    assert inf.parent.span_id == sev.get_span_context().span_id
    assert inf.get_span_context().trace_id == cycle.get_span_context().trace_id


def test_run_symbol_eval_propagates_exceptions():
    # the loop's gather uses return_exceptions=True, so the helper must NOT swallow
    provider, _ = _provider()
    tracer = provider.get_tracer("test")

    async def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        asyncio.run(_run_symbol_eval(tracer, None, "SYM", boom))
