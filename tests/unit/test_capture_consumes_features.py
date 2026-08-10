"""TDD — #2113 consumes #2389's features, never re-computes (plan §4/§7).

* R12 — the capture path performs NO feature computation: it copies the
  already-enriched (or default) DecisionContext scalars; a mocked
  ``compute_technical_features`` is never called.
* R13 — fail-open: with #2389 off (default/neutral features in the context)
  the capture row is still written AND a logging.warning (never DEBUG) is
  emitted (CLAUDE.md §5.6).

Run: cd ai_trading_bot && python -m pytest tests/unit/test_capture_consumes_features.py -o addopts="" -q
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

import config
from core.cloud_logger import DecisionContext


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(
        config.get_config(), "DECISION_CAPTURE_ENABLED", True, raising=False
    )


def _ctx(**kw) -> DecisionContext:
    defaults = {"symbol": "AAPL", "action": "BUY", "current_price": 150.0}
    defaults.update(kw)
    return DecisionContext(**defaults)


class TestNoDoubleCompute:
    def test_capture_never_calls_compute_technical_features(self, flag_on):
        from core.decision_capture.capture import capture_decision_outcome

        with patch(
            "core.round_table.features.compute_technical_features"
        ) as compute, patch(
            "core.cloud_logger.get_cloud_logger", return_value=MagicMock()
        ):
            capture_decision_outcome(_ctx())
        compute.assert_not_called()

    def test_capture_copies_the_context_scalars_verbatim(self, flag_on):
        """#2389 active → the enriched scalars are copied 1:1 (no re-derivation)."""
        from core.decision_capture.capture import capture_decision_outcome

        ctx = _ctx(vix_level=31.5, market_regime="stress")
        sink = MagicMock()
        with patch("core.cloud_logger.get_cloud_logger", return_value=sink):
            capture_decision_outcome(ctx)
        row = sink.log_decision_outcome.call_args[0][0]
        assert row["vix_level"] == 31.5
        assert row["market_regime"] == "stress"


class TestFailOpenWithDefaultFeatures:
    def test_row_written_and_warning_logged(self, flag_on, monkeypatch, caplog):
        """#2389 off (no DESKTOP_FEATURE_SNAPSHOT_ENABLED) → capture still works,
        default features are copied, and a WARNING — never DEBUG — is logged."""
        import core.decision_capture.capture as cap
        from core.decision_capture.capture import capture_decision_outcome

        # #2389 is "off" simply by its flag not existing yet in this tree —
        # getattr(cfg, "DESKTOP_FEATURE_SNAPSHOT_ENABLED", False) is False.
        assert (
            getattr(config.get_config(), "DESKTOP_FEATURE_SNAPSHOT_ENABLED", False)
            is False
        )
        # the WARNING is rate-limited to once per process — reset for this test
        monkeypatch.setattr(cap, "_warned_default_features", False, raising=False)
        sink = MagicMock()
        with caplog.at_level(logging.WARNING), patch(
            "core.cloud_logger.get_cloud_logger", return_value=sink
        ):
            capture_decision_outcome(_ctx())

        sink.log_decision_outcome.assert_called_once()
        row = sink.log_decision_outcome.call_args[0][0]
        # the (neutral) default features still land in the row — fail-open
        assert row["vix_level"] == 20.0
        assert any(
            r.levelno == logging.WARNING and "2389" in r.getMessage()
            for r in caplog.records
        ), "fail-open with default features must WARN (never DEBUG, CLAUDE.md §5.6)"
