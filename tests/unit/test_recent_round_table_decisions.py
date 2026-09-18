"""G1a (#1050): in-memory latest-per-symbol store for Round Table decisions.

The desktop console routes (`/round-table-decisions`, `/round-table/<symbol>`,
G1b) need a display source. On main no accessor exists (verified in the epic);
the audit chain (LocalJSONAuditLogger JSONL) is the compliance record, not a
display source. This store ports the bundle's battle-tested pattern
(latest-per-symbol dict — including its fix for the old rolling-deque bug
where one symbol's refreshes evicted other symbols) under main's Round-Table
nomenclature. Fed by `run_round_table` next to the existing fire-and-forget
`_senate.log_session(...)` call; read-only for the API layer.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _session(symbol: str, score: float = 0.5, ts: str = "2026-06-12T10:00:00+00:00"):
    from core.round_table.senate_log import SenateSession

    return SenateSession(
        session_id=f"sess-{symbol}-{ts}",
        symbol=symbol,
        timestamp=ts,
        votes=[
            {
                "name": "TrendAgent",
                "agent_name": "TrendAgent",
                "score": score,
                "weight": 1.0,
                "reasoning": "test",
                "vetoed": False,
                "signal": "HOLD",
            }
        ],
        consensus_score=score,
        gatekeeper_approved=True,
        gatekeeper_reason="ok",
        signal_action="HOLD",
    )


class StoreContract(unittest.TestCase):
    def setUp(self):
        from core.round_table import recent_decisions as rd

        rd.clear_recent_round_table_decisions()
        self.rd = rd

    def test_empty_state(self):
        self.assertEqual(self.rd.get_recent_round_table_decisions(), [])
        self.assertIsNone(self.rd.get_round_table_decision("AAPL"))

    def test_record_and_get_by_symbol(self):
        self.rd.record_round_table_decision(_session("AAPL", 0.7))
        d = self.rd.get_round_table_decision("AAPL")
        self.assertIsNotNone(d)
        self.assertEqual(d["symbol"], "AAPL")
        self.assertEqual(d["consensus_score"], 0.7)
        self.assertEqual(len(d["votes"]), 1)

    def test_latest_per_symbol_replaces_not_appends(self):
        # The bundle's hard-won fix: a symbol's refresh must REPLACE its entry,
        # never let one busy symbol evict the others (old rolling-deque bug).
        self.rd.record_round_table_decision(
            _session("AAPL", 0.4, "2026-06-12T10:00:00+00:00")
        )
        self.rd.record_round_table_decision(
            _session("AAPL", 0.8, "2026-06-12T11:00:00+00:00")
        )
        self.rd.record_round_table_decision(_session("MSFT", 0.6))
        all_ = self.rd.get_recent_round_table_decisions()
        self.assertEqual(len(all_), 2)
        self.assertEqual(
            self.rd.get_round_table_decision("AAPL")["consensus_score"], 0.8
        )

    def test_newest_first_ordering(self):
        self.rd.record_round_table_decision(_session("AAPL"))
        self.rd.record_round_table_decision(_session("MSFT"))
        self.rd.record_round_table_decision(_session("AAPL"))  # re-decided → newest
        syms = [d["symbol"] for d in self.rd.get_recent_round_table_decisions()]
        self.assertEqual(syms[0], "AAPL")

    def test_limit(self):
        for s in ("A", "B", "C", "D"):
            self.rd.record_round_table_decision(_session(s))
        self.assertEqual(len(self.rd.get_recent_round_table_decisions(limit=2)), 2)

    def test_accepts_plain_dict_payload(self):
        # Defensive: callers may hand a dict (e.g. tests, future producers).
        self.rd.record_round_table_decision(
            {"symbol": "NVDA", "consensus_score": 0.9, "votes": []}
        )
        self.assertEqual(
            self.rd.get_round_table_decision("NVDA")["consensus_score"], 0.9
        )

    def test_record_never_raises_on_garbage(self):
        # Display-store failures must never reach the trading path (fail-safe).
        self.rd.record_round_table_decision(None)  # type: ignore[arg-type]
        self.rd.record_round_table_decision({"no_symbol": True})
        self.assertEqual(self.rd.get_recent_round_table_decisions(), [])


class ConcurrencyHammer(unittest.TestCase):
    """PR-review P0-1: concurrent reads (FastAPI thread pool) + writes (engine
    loop) must never raise 'dictionary changed size during iteration'."""

    def test_concurrent_reads_and_writes(self):
        from concurrent.futures import ThreadPoolExecutor

        from core.round_table import recent_decisions as rd

        rd.clear_recent_round_table_decisions()
        errors: list[BaseException] = []

        def writer(start: int):
            try:
                for i in range(500):
                    rd.record_round_table_decision(
                        {"symbol": f"SYM{(start + i) % 50}", "consensus_score": 0.5}
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def reader():
            try:
                for _ in range(500):
                    rd.get_recent_round_table_decisions()
                    rd.get_round_table_decision("SYM1")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(writer, n * 100) for n in range(4)]
            futures += [pool.submit(reader) for _ in range(4)]
            for f in futures:
                f.result()

        self.assertEqual(errors, [], f"concurrent access raised: {errors[:3]}")
        self.assertLessEqual(len(rd.get_recent_round_table_decisions()), 50)


class RunnerWiringReal(unittest.IsolatedAsyncioTestCase):
    """PR-review P0-2: REAL wiring test (replaces the withdrawn source-string
    check) — drive run_round_table with stubbed collaborators and verify the
    store-record seam is actually CALLED with the session (a commented-out
    call or rename now fails this test)."""

    async def test_run_round_table_calls_record(self):
        from unittest.mock import AsyncMock, MagicMock

        import core.round_table.runner as runner
        from core.round_table.base_agent import VoteResult
        from core.round_table.gatekeeper import GatekeeperDecision

        class StubAgent:
            async def vote(self, state):
                return VoteResult(
                    agent_name="StubAgent",
                    symbol=state["symbol"],
                    score=0.7,
                    weight=1.0,
                    reasoning="stub",
                )

        consensus = MagicMock()
        consensus.check_distribution.return_value = (True, "")
        consensus.aggregate.return_value = 0.7

        record_mock = MagicMock()
        with patch.object(runner, "_active_agents", [StubAgent()]), patch.object(
            runner, "_consensus_engine", consensus
        ), patch.object(
            runner, "_senate", MagicMock(log_session=AsyncMock())
        ), patch.object(
            runner,
            "_resolve_gatekeeper_decision",
            AsyncMock(
                return_value=GatekeeperDecision(
                    symbol="AAPL", approved=True, reason="ok"
                )
            ),
        ), patch.object(
            runner, "_maybe_record_shadow_tft_vote", MagicMock()
        ), patch.object(
            runner, "_ml_watchdog", None
        ), patch.object(
            runner, "record_round_table_decision", record_mock
        ):
            result = await runner.run_round_table({"symbol": "AAPL"})

        self.assertNotIn("error", result or {})
        record_mock.assert_called_once()
        session = record_mock.call_args.args[0]
        self.assertEqual(session.symbol, "AAPL")
        self.assertEqual(session.consensus_score, 0.7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
