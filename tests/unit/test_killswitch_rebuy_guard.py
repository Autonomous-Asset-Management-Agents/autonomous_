"""#2467 kill-switch — displacement re-BUY must honour halt.

Regression guard: the compensating re-BUY was the ONLY real broker
submit in ``order_executor.py`` without a ``kill_switch.check_halt`` in front.
"""

import re
from pathlib import Path

_OE = Path(__file__).resolve().parents[2] / ("core/engine/order_executor.py")


def test_compensating_rebuy_is_guarded_by_check_halt():
    lines = _OE.read_text(encoding="utf-8").splitlines()
    submit_idx = next(
        (i for i, ln in enumerate(lines) if "submit_order, re_buy_req" in ln),
        None,
    )
    assert submit_idx is not None, "re-BUY submit not found"
    start = max(0, submit_idx - 8)
    window = "\n".join(lines[start:submit_idx])

    assert re.search(
        r"kill_switch\.check_halt\(", window
    ), "SAFETY: re-BUY must have check_halt()."
