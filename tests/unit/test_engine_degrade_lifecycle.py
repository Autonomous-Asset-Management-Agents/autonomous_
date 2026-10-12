"""LSR R2 (#2253): the boot-degrade lifecycle in core/engine/__main__.py.

A live boot that degrades to PAPER must be:
  * TERMINAL (Alpaca 401/403) → force paper + append a compensating WORM ``disable`` (self-heal so
    the next boot re-arms PAPER, not silently live) + drop a ``live_boot_outcome.json`` breadcrumb
    with ``terminal:true, disable_written:true`` for the shell's hard-crash path.
  * TRANSIENT (Ollama warming, 5xx, timeout) → force paper + KEEP the WORM enable (retry next boot)
    + mark ``config.DEGRADED_LIVE=true`` + surface ``LIVE_ENABLE_FAILED_REASON``; breadcrumb
    ``terminal:false`` and NO disable.
  * a boot that was ALREADY paper → no WORM disable, no DEGRADED_LIVE (nothing live to heal).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config  # noqa: E402
from core.engine import __main__ as engine_main  # noqa: E402
from scripts.shadow_boot import CheckFailure, ShadowBootResult  # noqa: E402


@pytest.fixture(autouse=True)
def reset_flags():
    prev_reason = getattr(config, "LIVE_ENABLE_FAILED_REASON", None)
    prev_deg = getattr(config, "DEGRADED_LIVE", False)
    yield
    config.LIVE_ENABLE_FAILED_REASON = prev_reason
    config.DEGRADED_LIVE = prev_deg


@pytest.fixture
def live_boot(monkeypatch, tmp_path):
    """A requested LIVE boot with a writable breadcrumb dir."""
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    config.LIVE_ENABLE_FAILED_REASON = None
    config.DEGRADED_LIVE = False
    return tmp_path


def _breadcrumb(tmp: Path):
    p = tmp / "live_boot_outcome.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _terminal_result():
    return ShadowBootResult(
        ok=False,
        failures=[
            CheckFailure("Alpaca", "Alpaca auth rejected (HTTP 401)", terminal=True)
        ],
    )


def _transient_result():
    return ShadowBootResult(
        ok=False,
        failures=[CheckFailure("LLM", "Ollama unreachable (warming)", terminal=False)],
    )


# ── terminal ──────────────────────────────────────────────────────────────────


def test_terminal_degrade_writes_compensating_disable_and_breadcrumb(live_boot):
    with patch("core.live_worm.revoke_live", return_value=True) as revoke, patch.object(
        config, "force_paper_trading"
    ) as fp:
        engine_main._handle_live_degrade(_terminal_result())

    fp.assert_called_once()
    revoke.assert_called_once()
    # trigger must mark this as the in-process boot-degrade path
    assert revoke.call_args.kwargs.get("trigger") == "boot-degrade"
    bc = _breadcrumb(live_boot)
    assert bc is not None
    assert bc["terminal"] is True
    assert bc["disable_written"] is True
    assert "401" in bc["reason"]
    assert isinstance(bc["pid"], int) and bc["ts"]
    # honest surfacing on THIS boot too
    assert (
        config.LIVE_ENABLE_FAILED_REASON and "401" in config.LIVE_ENABLE_FAILED_REASON
    )
    # terminal is a revocation, NOT a "keep the arm & retry" degrade
    assert config.DEGRADED_LIVE is False


def test_terminal_breadcrumb_records_disable_not_written_on_revoke_failure(live_boot):
    with patch("core.live_worm.revoke_live", return_value=False), patch.object(
        config, "force_paper_trading"
    ):
        engine_main._handle_live_degrade(_terminal_result())
    bc = _breadcrumb(live_boot)
    assert bc["terminal"] is True
    assert bc["disable_written"] is False  # honest: the shell must still compensate


# ── transient ─────────────────────────────────────────────────────────────────


def test_transient_degrade_keeps_arm_sets_degraded_live_no_disable(live_boot):
    with patch("core.live_worm.revoke_live") as revoke, patch.object(
        config, "force_paper_trading"
    ) as fp:
        engine_main._handle_live_degrade(_transient_result())

    fp.assert_called_once()
    revoke.assert_not_called()  # WORM enable is KEPT → next boot re-arms + retries
    assert config.DEGRADED_LIVE is True
    assert config.LIVE_ENABLE_FAILED_REASON.startswith("degraded-live:")
    bc = _breadcrumb(live_boot)
    assert bc["terminal"] is False
    assert bc["disable_written"] is False


def test_transient_emits_degraded_transient_otel(live_boot):
    spans = []

    class _Span:
        def __init__(self, name):
            self._attrs = {}
            spans.append((name, self._attrs))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def set_attribute(self, k, v):
            self._attrs[k] = v

    tracer = MagicMock()
    tracer.start_as_current_span = lambda name, **kw: _Span(name)

    with patch("core.live_worm.revoke_live"), patch.object(
        config, "force_paper_trading"
    ), patch("core.telemetry.get_tracer", return_value=tracer):
        engine_main._handle_live_degrade(_transient_result())

    names = [n for n, _ in spans]
    assert "live.boot.degraded_transient" in names
    attrs = spans[names.index("live.boot.degraded_transient")][1]
    assert attrs.get("arm_kept") is True
    assert attrs.get("reason")


# ── #2277 INC-1: switch_id correlation ────────────────────────────────────────


def test_terminal_degrade_threads_switch_id_to_revoke_and_breadcrumb(
    live_boot, monkeypatch
):
    monkeypatch.setenv("AAA_SWITCH_ID", "sw-degrade-1")
    with patch("core.live_worm.revoke_live", return_value=True) as revoke, patch.object(
        config, "force_paper_trading"
    ):
        engine_main._handle_live_degrade(_terminal_result())
    assert revoke.call_args.kwargs.get("switch_id") == "sw-degrade-1"
    bc = _breadcrumb(live_boot)
    assert bc["switch_id"] == "sw-degrade-1"


def test_transient_degrade_stamps_switch_id_on_span_and_breadcrumb(
    live_boot, monkeypatch
):
    monkeypatch.setenv("AAA_SWITCH_ID", "sw-degrade-2")
    spans = []

    class _Span:
        def __init__(self, name):
            self._attrs = {}
            spans.append((name, self._attrs))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def set_attribute(self, k, v):
            self._attrs[k] = v

    tracer = MagicMock()
    tracer.start_as_current_span = lambda name, **kw: _Span(name)
    with patch("core.live_worm.revoke_live"), patch.object(
        config, "force_paper_trading"
    ), patch("core.telemetry.get_tracer", return_value=tracer):
        engine_main._handle_live_degrade(_transient_result())
    names = [n for n, _ in spans]
    attrs = spans[names.index("live.boot.degraded_transient")][1]
    assert attrs.get("switch_id") == "sw-degrade-2"
    assert _breadcrumb(live_boot)["switch_id"] == "sw-degrade-2"


# ── already-paper boot: nothing to heal ───────────────────────────────────────


def test_paper_boot_degrade_does_not_revoke_or_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    config.LIVE_ENABLE_FAILED_REASON = None
    config.DEGRADED_LIVE = False
    with patch("core.live_worm.revoke_live") as revoke, patch.object(
        config, "force_paper_trading"
    ):
        engine_main._handle_live_degrade(_terminal_result())
    revoke.assert_not_called()
    assert config.DEGRADED_LIVE is False
    assert _breadcrumb(tmp_path) is None
