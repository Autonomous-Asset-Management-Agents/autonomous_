# tests/unit/test_xai_trading_history.py
# XAI-1 / XAI-T3 (#1332) — Trading-History (Glass Box) domain provider.
# Pins (incl. pre-PR fresh-eyes fixes): conservative symbol extraction, ZERO-HALLUCINATION
# templating (no fabricated "BLOCKED" from missing data; vetoes never hidden by truncation;
# lossless numbers), instant-correct ordering, per-entry hash integrity, robust reader
# (non-UTF-8 / malformed / missing dir), provider wiring, import-light.
import json
import os
import subprocess
import sys
import types
from unittest.mock import AsyncMock

import allure
import pytest

from core.xai.agent_core import XaiRequest
from core.xai.interfaces import IDomainProvider, ISenateLogReader
from core.xai.trading_history import (
    JsonlSenateLogReader,
    TradingHistoryProvider,
    compute_entry_hash,
    entry_integrity,
    extract_symbol,
    render_answer,
    render_decision,
)


def _entry(symbol="AAPL", ts="2026-06-18T11:00:00+00:00", action="SELL", score=0.32):
    return {
        "session_id": "s-" + ts,
        "symbol": symbol,
        "timestamp": ts,
        "consensus_score": score,
        "gatekeeper_approved": True,
        "gatekeeper_reason": "",
        "signal_action": action,
        "votes": [
            {
                "name": "RiskAgent",
                "agent_name": "RiskAgent",
                "score": 0.10,
                "weight": 2.0,
                "reasoning": "elevated drawdown risk",
                "vetoed": False,
                "signal": "SELL",
            },
            {
                "name": "MomentumAgent",
                "agent_name": "MomentumAgent",
                "score": 0.55,
                "weight": 1.0,
                "reasoning": "neutral momentum",
                "vetoed": False,
                "signal": "HOLD",
            },
        ],
        "prev_hash": "0" * 64,
        "hash": "deadbeef",
    }


def _hashed(entry):
    """Return `entry` with a *valid* self-hash (as LocalJSONAuditLogger would write it)."""
    e = {k: v for k, v in entry.items() if k != "hash"}
    e["hash"] = compute_entry_hash(e)
    return e


@allure.feature("XAI-1 Transparency Window")
@allure.story("Trading-History Glass Box (XAI-T3)")
class TestExtractSymbol:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Why did the senate sell AAPL?", "AAPL"),
            ("TSLA and MSFT both moved", "TSLA"),  # first wins
            ("what happened yesterday?", None),
            ("BUY AAPL now", "AAPL"),  # BUY stoplisted
            ("the CEO said EPS rose", None),  # finance acronyms stoplisted
            ("", None),
        ],
    )
    def test_extract(self, text, expected):
        assert extract_symbol(text) == expected


@allure.feature("XAI-1 Transparency Window")
@allure.story("Trading-History Glass Box (XAI-T3)")
class TestRenderDecisionZeroHallucination:
    def test_renders_only_real_fields(self):
        out = render_decision(_entry())
        assert "AAPL" in out
        assert "SELL" in out
        assert "0.32" in out
        assert "APPROVED" in out
        assert "RiskAgent" in out
        assert "elevated drawdown risk" in out
        assert "$" not in out  # nothing invented

    def test_blocked_gatekeeper_shows_reason(self):
        e = _entry()
        e["gatekeeper_approved"] = False
        e["gatekeeper_reason"] = "daily loss limit reached"
        out = render_decision(e)
        assert "BLOCKED" in out
        assert "daily loss limit reached" in out

    def test_absent_gatekeeper_is_unknown_not_fabricated_block(self):
        # The headline P0: missing data must NOT render an affirmative "BLOCKED".
        out = render_decision({"symbol": "AAPL", "signal_action": "BUY"})
        assert "UNKNOWN (not recorded)" in out
        assert "BLOCKED" not in out

    def test_lossless_score_does_not_cross_threshold(self):
        # 0.349 (a SELL) must not render as "0.35" (the HOLD boundary).
        out = render_decision(_entry(score=0.349))
        assert "0.349" in out
        assert "0.35" not in out
        assert "0.005" in render_decision(_entry(score=0.005))

    def test_missing_optional_fields_do_not_crash(self):
        out = render_decision({"symbol": "MSFT"})
        assert "MSFT" in out

    def test_vetoed_vote_is_marked(self):
        e = _entry()
        e["votes"][0]["vetoed"] = True
        assert "VETOED" in render_decision(e)

    def test_low_weight_veto_is_never_hidden_and_omission_disclosed(self):
        # The other P0: a decisive veto must survive top-3-by-weight truncation.
        e = _entry()
        e["votes"] = [
            {
                "agent_name": "A",
                "weight": 3.0,
                "signal": "BUY",
                "vetoed": False,
                "reasoning": "x",
            },
            {
                "agent_name": "B",
                "weight": 2.0,
                "signal": "BUY",
                "vetoed": False,
                "reasoning": "y",
            },
            {
                "agent_name": "C",
                "weight": 2.0,
                "signal": "BUY",
                "vetoed": False,
                "reasoning": "z",
            },
            {
                "agent_name": "VetoAgent",
                "weight": 0.1,
                "signal": "SELL",
                "vetoed": True,
                "reasoning": "hard compliance veto",
            },
        ]
        out = render_decision(e)
        assert "VetoAgent" in out  # the low-weight veto is shown
        assert "VETOED" in out
        assert "not shown" in out  # omission is disclosed truthfully


@allure.feature("XAI-1 Transparency Window")
@allure.story("Trading-History Glass Box (XAI-T3)")
class TestRenderAnswer:
    def test_empty_is_explicit_no_data(self):
        msg = render_answer([], symbol="AAPL")
        assert "No Senate decisions found" in msg
        assert "AAPL" in msg

    def test_lists_each_decision(self):
        msg = render_answer([_entry(), _entry(ts="2026-06-18T10:00:00+00:00")])
        assert msg.count("Round Table decided") == 2


@allure.feature("XAI-1 Transparency Window")
@allure.story("Trading-History Glass Box (XAI-T3)")
class TestHashIntegrity:
    def test_valid_self_hash_verifies(self):
        assert entry_integrity(_hashed(_entry())) is True

    def test_tampered_entry_fails(self):
        good = _hashed(_entry())
        tampered = dict(good)
        tampered["symbol"] = "TSLA"  # mutate after hashing
        assert entry_integrity(tampered) is False

    def test_missing_hash_is_unverified(self):
        assert entry_integrity({"symbol": "AAPL"}) is False


@allure.feature("XAI-1 Transparency Window")
@allure.story("Trading-History Glass Box (XAI-T3)")
class TestJsonlSenateLogReader:
    @pytest.mark.anyio
    async def test_reads_newest_first(self, tmp_path):
        d = tmp_path / "logs"
        d.mkdir()
        (d / "audit_log_2026-06-18.jsonl").write_text(
            json.dumps(_entry(ts="2026-06-18T10:00:00+00:00"))
            + "\n"
            + json.dumps(_entry(ts="2026-06-18T11:00:00+00:00"))
            + "\n",
            encoding="utf-8",
        )
        out = await JsonlSenateLogReader(log_dir=str(d)).read_decisions(limit=10)
        assert [e["timestamp"] for e in out] == [
            "2026-06-18T11:00:00+00:00",
            "2026-06-18T10:00:00+00:00",
        ]

    @pytest.mark.anyio
    async def test_orders_by_instant_across_offsets(self, tmp_path):
        # 08:00 -05:00 == 13:00 UTC, which is NEWER than 12:00 +00:00 — a string sort
        # would get this backwards.
        d = tmp_path / "logs"
        d.mkdir()
        (d / "audit_log_2026-06-18.jsonl").write_text(
            json.dumps(_entry(symbol="A", ts="2026-06-18T12:00:00+00:00"))
            + "\n"
            + json.dumps(_entry(symbol="B", ts="2026-06-18T08:00:00-05:00"))
            + "\n",
            encoding="utf-8",
        )
        out = await JsonlSenateLogReader(log_dir=str(d)).read_decisions(limit=10)
        assert [e["symbol"] for e in out] == ["B", "A"]

    @pytest.mark.anyio
    async def test_absent_timestamp_sorts_last(self, tmp_path):
        d = tmp_path / "logs"
        d.mkdir()
        no_ts = _entry(symbol="NOTS")
        no_ts.pop("timestamp")
        (d / "audit_log_2026-06-18.jsonl").write_text(
            json.dumps(no_ts)
            + "\n"
            + json.dumps(_entry(symbol="HAS", ts="2026-06-18T11:00:00+00:00"))
            + "\n",
            encoding="utf-8",
        )
        out = await JsonlSenateLogReader(log_dir=str(d)).read_decisions(limit=10)
        assert [e["symbol"] for e in out] == ["HAS", "NOTS"]

    @pytest.mark.anyio
    async def test_symbol_filter_and_limit(self, tmp_path):
        d = tmp_path / "logs"
        d.mkdir()
        (d / "audit_log_2026-06-18.jsonl").write_text(
            json.dumps(_entry(symbol="AAPL", ts="2026-06-18T10:00:00+00:00"))
            + "\n"
            + json.dumps(_entry(symbol="TSLA", ts="2026-06-18T11:00:00+00:00"))
            + "\n"
            + json.dumps(_entry(symbol="AAPL", ts="2026-06-18T12:00:00+00:00"))
            + "\n",
            encoding="utf-8",
        )
        out = await JsonlSenateLogReader(log_dir=str(d)).read_decisions(
            symbol="aapl", limit=1
        )
        assert len(out) == 1
        assert out[0]["symbol"] == "AAPL"
        assert out[0]["timestamp"] == "2026-06-18T12:00:00+00:00"

    @pytest.mark.anyio
    async def test_malformed_lines_are_skipped(self, tmp_path):
        d = tmp_path / "logs"
        d.mkdir()
        (d / "audit_log_2026-06-18.jsonl").write_text(
            "not json\n" + json.dumps(_entry()) + "\n" + "{partial\n", encoding="utf-8"
        )
        out = await JsonlSenateLogReader(log_dir=str(d)).read_decisions(limit=10)
        assert len(out) == 1

    @pytest.mark.anyio
    async def test_malformed_line_is_logged(self, tmp_path, caplog):
        # A malformed line in a hash-chained audit trail must be SURFACED, not silently
        # dropped (possible corruption / tampering).
        d = tmp_path / "logs"
        d.mkdir()
        (d / "audit_log_2026-06-18.jsonl").write_text(
            "not json\n" + json.dumps(_entry()) + "\n", encoding="utf-8"
        )
        with caplog.at_level("WARNING"):
            out = await JsonlSenateLogReader(log_dir=str(d)).read_decisions(limit=10)
        assert len(out) == 1
        assert "malformed audit line" in caplog.text

    @pytest.mark.anyio
    async def test_non_utf8_file_does_not_crash(self, tmp_path):
        d = tmp_path / "logs"
        d.mkdir()
        (d / "audit_log_2026-06-18.jsonl").write_bytes(
            b"\xff\xfe garbage bytes\n" + json.dumps(_entry()).encode("utf-8") + b"\n"
        )
        out = await JsonlSenateLogReader(log_dir=str(d)).read_decisions(limit=10)
        assert len(out) == 1  # bad bytes replaced + line skipped, valid entry kept

    @pytest.mark.anyio
    async def test_missing_dir_is_empty(self, tmp_path):
        reader = JsonlSenateLogReader(log_dir=str(tmp_path / "nope"))
        assert await reader.read_decisions(limit=10) == []


@allure.feature("XAI-1 Transparency Window")
@allure.story("Trading-History Glass Box (XAI-T3)")
class TestTradingHistoryProvider:
    @pytest.mark.anyio
    async def test_answer_uses_reader_and_extracts_symbol(self):
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions = AsyncMock(return_value=[_entry()])
        provider = TradingHistoryProvider(reader=reader)
        assert isinstance(provider, IDomainProvider)
        out = await provider.answer(XaiRequest(text="Why did the senate sell AAPL?"))
        reader.read_decisions.assert_awaited_once()
        assert reader.read_decisions.await_args.kwargs["symbol"] == "AAPL"
        assert out["count"] == 1
        assert "Round Table decided" in out["text"]
        assert out["decisions"][0]["symbol"] == "AAPL"

    @pytest.mark.anyio
    async def test_answer_no_data_is_explicit(self):
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions = AsyncMock(return_value=[])
        provider = TradingHistoryProvider(reader=reader)
        out = await provider.answer(XaiRequest(text="history for TSLA?"))
        assert out["count"] == 0
        assert "No Senate decisions found" in out["text"]

    @pytest.mark.anyio
    async def test_chain_verified_reflects_integrity(self):
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions = AsyncMock(return_value=[_hashed(_entry())])
        out = await TradingHistoryProvider(reader=reader).answer(
            XaiRequest(text="senate AAPL")
        )
        assert out["chain_verified"] is True

        tampered = _hashed(_entry())
        tampered["symbol"] = "TSLA"
        reader.read_decisions = AsyncMock(return_value=[tampered])
        out = await TradingHistoryProvider(reader=reader).answer(
            XaiRequest(text="senate")
        )
        assert out["chain_verified"] is False

    @pytest.mark.anyio
    async def test_request_without_text_attribute_is_safe(self):
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions = AsyncMock(return_value=[])
        out = await TradingHistoryProvider(reader=reader).answer(
            types.SimpleNamespace()
        )
        assert out["count"] == 0
        assert reader.read_decisions.await_args.kwargs["symbol"] is None


@allure.feature("XAI-1 Transparency Window")
@allure.story("Trading-History Glass Box (XAI-T3)")
class TestImportLight:
    def test_no_torch_pulled(self):
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        code = (
            "import sys\n"
            "import core.xai.trading_history\n"
            "bad = sorted(m for m in sys.modules if m == 'torch' or m.startswith('torch.'))\n"
            "assert not bad, bad\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code], cwd=root, capture_output=True, text=True
        )
        assert r.returncode == 0, r.stderr
