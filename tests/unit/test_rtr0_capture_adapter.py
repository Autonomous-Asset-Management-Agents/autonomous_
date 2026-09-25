"""
test_rtr0_capture_adapter.py — RTR-0 (#1947) dormant #2113 capture adapter.

R8 (plan §8): until #2113 (decision-outcome capture) is merged there is no
durable ``decision_outcomes`` source. The adapter must fail CLEANLY and
explicitly (typed error, actionable message) — it must never fake a frame,
and it must never touch the replay path.
"""

from __future__ import annotations

import pytest

from core.analysis.attribution.source_capture import (
    CaptureSourceUnavailable,
    load_capture_votes,
)


class TestDormantCaptureAdapter:
    def test_raises_typed_unavailable_error(self):
        with pytest.raises(CaptureSourceUnavailable) as exc_info:
            load_capture_votes()
        assert "#2113" in str(exc_info.value)

    def test_error_is_a_not_implemented_error(self):
        # contract from the plan: NotImplemented-guard until #2113 merges
        assert issubclass(CaptureSourceUnavailable, NotImplementedError)
