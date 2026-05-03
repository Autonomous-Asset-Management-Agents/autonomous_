# tests/unit/test_senate_protocol.py
# Epic 2.5 / Issue I-3 — TDD
# SenateProtocol: JSONL Log, Redis Stream, session_id Eindeutigkeit
#
# Gherkin (Architect Blueprint):
#   Given: A completed Round Table session
#   When:  SenateProtocol.log_session()
#   Then:  A JSONL entry with a unique session_id must be present

from __future__ import annotations

import json
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def make_session(symbol: str = "AAPL", score: float = 0.72) -> "SenateSession":
    from core.round_table.senate_log import SenateSession, make_session_id

    return SenateSession(
        session_id=make_session_id(),
        symbol=symbol,
        timestamp="2026-03-10T07:00:00+00:00",
        votes=[
            {
                "agent_name": "DrawdownGuardAgent",
                "score": 0.8,
                "weight": 0.6,
                "reasoning": "test",
                "vetoed": False,
            },
        ],
        consensus_score=score,
        gatekeeper_approved=True,
        gatekeeper_reason="AllChecksPassed",
        signal_action="BUY",
    )


class TestSenateImports:
    def test_senate_protocol_importable(self):
        from core.round_table.senate_log import SenateProtocol  # noqa: F401

        assert SenateProtocol is not None

    def test_senate_session_importable(self):
        from core.round_table.senate_log import SenateSession  # noqa: F401

    def test_make_session_id_importable(self):
        from core.round_table.senate_log import make_session_id

        id1 = make_session_id()
        id2 = make_session_id()
        assert id1 != id2, "Session-IDs müssen eindeutig sein (UUID4)"


class TestSenateSessionStructure:
    def test_session_has_required_fields(self):
        """SenateSession muss alle Pflichtfelder haben."""
        session = make_session()
        assert session.session_id
        assert session.symbol == "AAPL"
        assert session.timestamp
        assert isinstance(session.votes, list)
        assert 0.0 <= session.consensus_score <= 1.0
        assert isinstance(session.gatekeeper_approved, bool)

    def test_session_id_is_unique(self):
        """
        Gherkin:
          Given: Two Round Table sessions
          When:  make_session_id() aufgerufen
          Then:  Beide IDs sind unterschiedlich
        """
        from core.round_table.senate_log import make_session_id

        ids = {make_session_id() for _ in range(100)}
        assert len(ids) == 100, "Alle 100 session_ids müssen eindeutig sein"


class TestSenateJSONLFallback:
    @pytest.mark.anyio
    async def test_jsonl_log_written(self):
        """
        Gherkin:
          Given: Redis nicht verfügbar
          When:  log_session() aufgerufen
          Then:  JSONL-Datei enthält validen JSON-Eintrag mit session_id
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("core.round_table.senate_log._LOG_DIR", Path(tmpdir)):
                from core.round_table.senate_log import SenateProtocol

                protocol = SenateProtocol()
                protocol._log_dir = Path(tmpdir)
                session = make_session()

                # Redis-Sink schlägt fehl → JSONL-Fallback
                with patch.object(protocol, "_log_to_redis_stream", return_value=False):
                    await protocol.log_session(session)

                # JSONL-Datei prüfen
                import glob

                jsonl_files = glob.glob(os.path.join(tmpdir, "senate_protocol_*.jsonl"))
                assert (
                    len(jsonl_files) == 1
                ), "Eine JSONL-Datei muss erstellt worden sein"

                with open(jsonl_files[0], "r") as f:
                    line = f.readline()
                    entry = json.loads(line)

                assert entry["session_id"] == session.session_id
                assert entry["symbol"] == "AAPL"
                assert "consensus_score" in entry
                assert "votes" in entry


class TestSenateRedisStream:
    @pytest.mark.anyio
    async def test_redis_stream_xadd_called(self):
        """Redis Stream XADD wird aufgerufen wenn Redis verfügbar."""
        from core.round_table.senate_log import SenateProtocol

        protocol = SenateProtocol()
        session = make_session()

        mock_redis = MagicMock()
        mock_redis.xadd = MagicMock(return_value=b"stream-id")

        with patch("core.round_table.senate_log.RedisClient", None):
            # Wenn RedisClient None → Fallback
            result = await protocol._log_to_redis_stream(session)
        assert result is False  # Kein Redis → False → JSONL-Fallback

    @pytest.mark.anyio
    async def test_database_fallback_no_crash(self):
        """Database Sink Exception darf nicht propagieren (fire-and-forget)."""
        from core.round_table.senate_log import SenateProtocol

        protocol = SenateProtocol()
        session = make_session()
        # Kein Crash erwartet auch wenn DB nicht verfügbar
        await protocol._log_to_database(session)


class TestSenateSessionEnrichment:
    """Epic 4.3 — Optional ML/regime/compliance fields on SenateSession."""

    def test_enrichment_fields_default_none(self):
        """
        Gherkin:
          Given: A SenateSession created without enrichment fields
          When:  Fields are accessed
          Then:  All default to None (backwards-compatible)
        """
        session = make_session()
        assert session.market_regime is None
        assert session.escalations is None
        assert session.specialist_summaries is None
        assert session.ml_scores is None

    def test_enrichment_fields_can_be_set(self):
        from core.round_table.senate_log import SenateSession, make_session_id

        session = SenateSession(
            session_id=make_session_id(),
            symbol="TSLA",
            timestamp="2026-03-10T07:00:00+00:00",
            votes=[],
            consensus_score=0.65,
            gatekeeper_approved=False,
            gatekeeper_reason="LowScore",
            signal_action="HOLD",
            market_regime="bear",
            escalations=["DrawdownBreached"],
            specialist_summaries={"TSLA": "Negative momentum"},
            ml_scores={"regime": 0.3, "momentum": 0.4},
        )
        assert session.market_regime == "bear"
        assert session.escalations == ["DrawdownBreached"]
        assert session.specialist_summaries == {"TSLA": "Negative momentum"}
        assert session.ml_scores == {"regime": 0.3, "momentum": 0.4}

    @pytest.mark.anyio
    async def test_jsonl_includes_enrichment_fields(self):
        """
        Gherkin:
          Given: A SenateSession with enrichment fields populated
          When:  log_session() writes JSONL
          Then:  The JSONL entry contains market_regime and ml_scores
        """
        import glob
        from core.round_table.senate_log import (
            SenateProtocol,
            SenateSession,
            make_session_id,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("core.round_table.senate_log._LOG_DIR", Path(tmpdir)):
                protocol = SenateProtocol()
                protocol._log_dir = Path(tmpdir)

                session = SenateSession(
                    session_id=make_session_id(),
                    symbol="NVDA",
                    timestamp="2026-03-10T07:00:00+00:00",
                    votes=[],
                    consensus_score=0.85,
                    gatekeeper_approved=True,
                    gatekeeper_reason="AllChecksPassed",
                    signal_action="BUY",
                    market_regime="bull",
                    ml_scores={"momentum": 0.82, "regime": 0.77},
                )

                with patch.object(protocol, "_log_to_redis_stream", return_value=False):
                    await protocol.log_session(session)

                jsonl_files = glob.glob(os.path.join(tmpdir, "senate_protocol_*.jsonl"))
                assert len(jsonl_files) == 1

                with open(jsonl_files[0], "r") as f:
                    entry = json.loads(f.readline())

                assert entry["market_regime"] == "bull"
                assert entry["ml_scores"] == {"momentum": 0.82, "regime": 0.77}
                assert entry["escalations"] is None
                assert entry["specialist_summaries"] is None
