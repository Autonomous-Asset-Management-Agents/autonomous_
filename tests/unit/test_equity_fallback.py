"""BUG-AI-S01 (#1232): the engine must never size positions off a hardcoded
fictional equity. When the broker is unavailable / flapping, fall back to the
configured DEFAULT_EQUITY and emit a WARNING (CLAUDE.md §5.6) — never a silent
made-up value.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from core.engine.equity_fallback import resolve_equity


def test_resolve_equity_returns_live_equity_when_available():
    api = MagicMock()
    api.get_account.return_value.equity = 42000.0
    assert resolve_equity(api, 100000.0) == 42000.0


def test_resolve_equity_falls_back_to_default_when_no_broker(caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_equity(None, 5000.0) == 5000.0
    # never silent — the fallback use must be visible at WARNING
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_resolve_equity_falls_back_on_fetch_error(caplog):
    api = MagicMock()
    api.get_account.side_effect = RuntimeError("api flap")
    with caplog.at_level(logging.WARNING):
        assert resolve_equity(api, 7000.0) == 7000.0
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_resolve_equity_falls_back_on_nonpositive_equity(caplog):
    api = MagicMock()
    api.get_account.return_value.equity = 0
    with caplog.at_level(logging.WARNING):
        assert resolve_equity(api, 9000.0) == 9000.0


def test_config_exposes_default_equity():
    import config

    # configurable default (was a hardcoded 100000.0 sprinkled in the loops)
    assert float(config.get_config().DEFAULT_EQUITY) == 100000.0
