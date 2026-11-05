# core/dust_floor.py
"""#2789 — the RELATIVE dust floor for exit orders, as a pure decision function.

Dust orders originate at the EXITS by construction: displacement remainders, rotation
part-sells, trim slivers, partial fills. #2714 recorded the gap as "kein Notional-Floor
an den Submit-Stellen"; #2721 answered it by raising the ENTRY knob
``MIN_ORDER_VALUE_USD`` to a flat $50, which suppressed no exit dust (its only
exit-side consumer, the trim lever, is default-OFF) and blocked every account under
~$1,000 until #2784 reverted it.

The lesson that shapes this module: an ABSOLUTE floor confuses "economically too small"
with "small account". Every threshold here is therefore RELATIVE, which makes the floor
structurally incapable of blocking a small account — on a $55 account 0.1 % of equity is
$0.055, far under Alpaca's own $1 minimum, which binds first anyway.

Pure by intent: no config import, no broker, no logging. The caller supplies the
thresholds and owns the WARNING log, so this decision is testable in isolation and the
same function serves both submit paths.
"""

from __future__ import annotations

from typing import Optional, Tuple

# Exit classes that must NEVER be suppressed, whatever the notional.
#   "risk" — a protective stop/TP/trailing exit. Trapping a position is strictly more
#            dangerous than the dust it would avoid.
#   None   — an unlabeled/legacy path (``classify_exit_kind`` returns None). Keeping the
#            #2713 fail-safe exemption means a path nobody has classified yet can never
#            be blocked by accident.
EXEMPT_EXIT_KINDS = (None, "risk")


def dust_exit_skip(
    *,
    enabled: bool,
    notional: float,
    position_value: float,
    equity: float,
    exit_kind: Optional[str],
    is_full_close: bool,
    min_position_pct: float,
    min_equity_pct: float,
) -> Tuple[bool, str]:
    """Decide whether an EXIT order is dust and should be skipped.

    Returns ``(skip, reason)``. ``reason`` is empty when the order may proceed and names
    the binding rule otherwise, so the caller's WARNING is specific.

    The contract is deliberately skip-or-allow: no size is ever returned, so the floor
    cannot inflate an order beyond the approved quantity (ADR-016). A suppressed exit is
    a DEFERRED exit — the next cycle re-evaluates it once the drift is worth an order.

    Rule order:
      1. exempt exit classes — never skipped;
      2. full closes — never skipped (else the remaining position is trapped);
      3. rule A, the primary: notional below ``min_position_pct`` of the position being
         reduced. This targets the dust mechanism directly (a sliver of a position) and
         scales with account size;
      4. rule B, the backstop: notional below ``min_equity_pct`` of equity. Only reaches
         orders whose position value is unknown — it must stay strictly weaker than
         rule A, or it silently becomes the binding constraint and suppresses legitimate
         de-concentration on large accounts (the #2721 mistake, relatively dressed).

    Unknown inputs fail OPEN (submit), consistent with the exemption philosophy above:
    a missing price or position must never be the reason a protective exit is dropped.
    """
    if not enabled:
        return False, ""
    if exit_kind in EXEMPT_EXIT_KINDS:
        return False, ""
    if is_full_close:
        return False, ""

    try:
        notional = float(notional)
        position_value = float(position_value or 0.0)
        equity = float(equity or 0.0)
        min_position_pct = float(min_position_pct)
        min_equity_pct = float(min_equity_pct)
    except (TypeError, ValueError):
        return False, ""  # unreadable input → fail open, never block an exit on a cast

    if notional <= 0:
        return (
            False,
            "",
        )  # not this guard's business (a 0-qty order is aborted upstream)

    if position_value > 0 and min_position_pct > 0:
        threshold = position_value * min_position_pct
        if notional < threshold:
            return True, (
                f"below {min_position_pct:.1%} of the position "
                f"(${notional:.2f} < ${threshold:.2f})"
            )

    if equity > 0 and min_equity_pct > 0:
        threshold = equity * min_equity_pct
        if notional < threshold:
            return True, (
                f"below {min_equity_pct:.2%} of equity "
                f"(${notional:.2f} < ${threshold:.2f})"
            )

    return False, ""
