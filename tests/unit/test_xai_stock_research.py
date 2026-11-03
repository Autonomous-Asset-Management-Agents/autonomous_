# tests/unit/test_xai_stock_research.py
# XAI-1 / XAI-T6 (#1335) — Stock-Research domain provider.
# Pins: ZERO-HALLUCINATION rendering (only on-record fields shown; absent field omitted; no
# invented financial value), safe degrade (no ticker / no report / registry off / raising
# registry -> clear honest message, never crash), dict+dataclass serialization, provider
# wiring, import-light.
import dataclasses
import os
import subprocess
import sys
from unittest.mock import AsyncMock

import allure
import pytest

from core.xai.agent_core import XaiRequest
from core.xai.interfaces import IDomainProvider, ISpecialistReportSource
from core.xai.stock_research import (
    _NO_SYMBOL,
    RegistrySpecialistReportSource,
    StockResearchProvider,
    _to_dict,
    render_report,
)


def _report(**over):
    base = {
        "symbol": "AAPL",
        "recommendation": "buy",
        "sentiment_score": 72.0,
        "confidence": 0.8,
        "company_summary": "Apple Inc designs electronics.",
        "news_summary": "Positive earnings.",
        "insider_trades": [{"a": 1}, {"a": 2}],
        "political_trades": [],
        "short_interest_pct": 1.2,
        "reasons": ["strong margins", "buyback"],
    }
    base.update(over)
    return base


@dataclasses.dataclass
class _FakeReport:
    symbol: str = "AAPL"
    recommendation: str = "buy"
    sentiment_score: float = 60.0


class _Registry:
    def __init__(self, report):
        self._r = report

    def get_report(self, symbol):
        return self._r


@allure.feature("XAI-1 Transparency Window")
@allure.story("Stock-Research (XAI-T6)")
class TestRender:
    def test_full_report_renders_populated_fields(self):
        out = render_report(_report(), "AAPL")
        assert "Stock research for AAPL:" in out
        assert "recommendation: buy" in out and "sentiment 72.0/100" in out
        assert "Company: Apple Inc designs electronics." in out
        assert "News: Positive earnings." in out
        assert "Insider trades on record: 2" in out
        assert "Short interest: 1.2%" in out
        assert "Reasons: strong margins; buyback" in out
        assert "no figure is invented" in out
        assert "Political trades" not in out  # empty list -> omitted

    def test_absent_field_is_omitted(self):
        out = render_report(_report(news_summary="", company_summary="   "), "AAPL")
        assert "News:" not in out and "Company:" not in out

    def test_reasons_truncated_to_five(self):
        out = render_report(_report(reasons=[f"r{i}" for i in range(8)]), "AAPL")
        assert "r4" in out and "r5" not in out

    def test_empty_report_degrades(self):
        assert "No SpecialistReport on record for AAPL" in render_report({}, "AAPL")


@allure.feature("XAI-1 Transparency Window")
@allure.story("Stock-Research (XAI-T6)")
class TestHostileFields:
    # the injected source is external (Enterprise/mock); odd field types must never crash
    # or fabricate (the recurring bug class on T4/T5/T8).
    def test_reasons_string_not_char_spammed(self):
        out = render_report(_report(reasons="buyback"), "AAPL")
        assert (
            "Reasons:" not in out
        )  # a string is not a list -> dropped, not char-spammed

    def test_reasons_scalar_does_not_crash(self):
        out = render_report(_report(reasons=5), "AAPL")  # must not raise
        assert "Reasons:" not in out

    def test_nan_inf_figures_omitted(self):
        out = render_report(
            _report(
                sentiment_score=float("nan"),
                short_interest_pct=float("inf"),
                confidence=float("nan"),
            ),
            "AAPL",
        )
        assert "sentiment" not in out
        assert "Short interest" not in out
        assert "confidence" not in out

    def test_non_list_collections_omitted(self):
        out = render_report(
            _report(insider_trades=5, political_trades={"a": 1}), "AAPL"
        )
        assert "Insider trades" not in out and "Political trades" not in out

    def test_confidence_has_scale(self):
        assert "confidence 0.8/1" in render_report(_report(), "AAPL")


@allure.feature("XAI-1 Transparency Window")
@allure.story("Stock-Research (XAI-T6)")
class TestSource:
    def test_to_dict_variants(self):
        assert _to_dict({"a": 1}) == {"a": 1}
        assert _to_dict(_FakeReport())["recommendation"] == "buy"
        assert _to_dict("junk") == {} and _to_dict(None) == {}

    @pytest.mark.anyio
    async def test_source_serializes_dataclass(self):
        src = RegistrySpecialistReportSource(registry=_Registry(_FakeReport()))
        out = await src.get_report("AAPL")
        assert out["recommendation"] == "buy"

    @pytest.mark.anyio
    async def test_source_passthrough_dict(self):
        src = RegistrySpecialistReportSource(registry=_Registry(_report()))
        assert (await src.get_report("AAPL"))["symbol"] == "AAPL"

    @pytest.mark.anyio
    async def test_source_none_and_no_registry(self):
        assert (
            await RegistrySpecialistReportSource(registry=_Registry(None)).get_report(
                "X"
            )
            is None
        )
        assert await RegistrySpecialistReportSource().get_report("X") is None

    @pytest.mark.anyio
    async def test_source_raising_registry_degrades(self):
        class _Raise:
            def get_report(self, s):
                raise RuntimeError("boom")

        assert (
            await RegistrySpecialistReportSource(registry=_Raise()).get_report("X")
            is None
        )


@allure.feature("XAI-1 Transparency Window")
@allure.story("Stock-Research (XAI-T6)")
class TestProvider:
    @pytest.mark.anyio
    async def test_gherkin_answer_from_report(self):
        # Given a SpecialistReport; When asked for fundamentals; Then answered FROM the
        # report (no invented figure).
        src = AsyncMock(spec=ISpecialistReportSource)
        src.get_report.return_value = _report()
        res = await StockResearchProvider(source=src).answer(
            XaiRequest(text="Fundamentaldaten von AAPL?")
        )
        assert res["symbol"] == "AAPL" and res["report"] is not None
        assert (
            "recommendation: buy" in res["text"]
            and "no figure is invented" in res["text"]
        )

    @pytest.mark.anyio
    async def test_no_ticker_asks_for_one(self):
        res = await StockResearchProvider().answer(
            XaiRequest(text="what stocks look good")
        )
        assert res["text"] == _NO_SYMBOL and res["symbol"] is None

    @pytest.mark.anyio
    async def test_no_report_degrades_safely(self):
        src = AsyncMock(spec=ISpecialistReportSource)
        src.get_report.return_value = None  # not researched / registry off
        res = await StockResearchProvider(source=src).answer(
            XaiRequest(text="fundamentals for AAPL")
        )
        assert "No SpecialistReport on record for AAPL" in res["text"]
        assert res["report"] is None and res["symbol"] == "AAPL"

    @pytest.mark.anyio
    async def test_no_source_degrades(self):
        res = await StockResearchProvider().answer(
            XaiRequest(text="fundamentals for AAPL")
        )
        assert "No SpecialistReport on record for AAPL" in res["text"]

    @pytest.mark.anyio
    async def test_payload_shape(self):
        src = AsyncMock(spec=ISpecialistReportSource)
        src.get_report.return_value = _report()
        res = await StockResearchProvider(source=src).answer(XaiRequest(text="AAPL?"))
        assert set(res) == {"text", "report", "symbol"}

    def test_is_domain_provider(self):
        assert isinstance(StockResearchProvider(), IDomainProvider)


@allure.feature("XAI-1 Transparency Window")
@allure.story("Stock-Research (XAI-T6)")
class TestImportLight:
    def test_no_torch_pulled(self):
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        code = (
            "import sys\n"
            "import core.xai.stock_research\n"
            "bad = sorted(m for m in sys.modules if m == 'torch' or m.startswith('torch.'))\n"
            "assert not bad, bad\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code], cwd=root, capture_output=True, text=True
        )
        assert r.returncode == 0, (r.stdout, r.stderr)
