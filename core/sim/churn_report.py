"""core/sim/churn_report.py — churn A/B evidence for the #1957 guard hardening.

Quantifies, over a ``sim_result.json`` orders list, exactly the forensic patterns of the
04.-07.08 operating-install evidence:

* ``same_minute_pairs``  — opposite-side fills of the SAME symbol within 60s (the wash
  class the displacement/recovery roundtrips produced; #2712).
* ``roundtrips_lt_1d``   — opposite-side fills of the same symbol with identical qty
  (1e-6 eps) less than 24h apart (the rotation-churn class; #2711).
* ``dust_orders``        — fills below the notional floor (default $50; #2714).
* ``turnover`` / ``n_filled`` / ``max_symbol_fills`` — volume context.

Usage (prove-it-or-kill-it): run the SAME replay day twice —

    A (legacy):   ROTATION_MIN_HOLD_HARD_GATE=false DISPLACEMENT_BUY_FIRST=false \
                  STOPOUT_REENTRY_COOLDOWN_MIN=0 MIN_ORDER_VALUE_USD=1 \
                  python -m core.sim run --date <D> --out sim_a.json
    B (hardened): python -m core.sim run --date <D> --out sim_b.json   (defaults)

    python -m core.sim.churn_report sim_a.json sim_b.json

Pure functions over plain dicts — no engine imports, fail-safe on missing timestamps
(time-based metrics simply see no pairs; nothing ever crashes the report).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

_QTY_EPS = 1e-6
_PAIR_WINDOW_S = 60.0
_ROUNDTRIP_WINDOW_S = 24 * 3600.0


def _ts(order: Dict[str, Any]) -> Optional[datetime]:
    raw = order.get("filled_at") or order.get("submitted_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _side(o: Dict[str, Any]) -> str:
    return str(o.get("side") or "").lower()


def _fills(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # The runner emits broker-enum casing ("BUY"/"SELL"); tests/legacy data may be
    # lowercase - normalize instead of silently dropping real fills.
    return [
        o
        for o in orders or []
        if o.get("status") == "filled" and _side(o) in ("buy", "sell")
    ]


def churn_metrics(
    orders: List[Dict[str, Any]], min_value: float = 50.0
) -> Dict[str, Any]:
    """The churn scorecard for one sim run. Pure + deterministic."""
    fills = _fills(orders)
    turnover = 0.0
    dust = 0
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for o in fills:
        qty = float(o.get("qty") or 0.0)
        price = float(o.get("fill_price") or 0.0)
        value = qty * price
        turnover += value
        if 0 < value < min_value:
            dust += 1
        by_symbol.setdefault(str(o.get("symbol")), []).append(o)

    same_minute = 0
    roundtrips = 0
    for sym_fills in by_symbol.values():
        stamped = [(o, _ts(o)) for o in sym_fills]
        for i, (a, ta) in enumerate(stamped):
            for b, tb in stamped[i + 1 :]:
                if _side(a) == _side(b):
                    continue
                if ta is None or tb is None:
                    continue  # fail-safe: no timestamp, no time-based claim
                dt = abs((tb - ta).total_seconds())
                if dt <= _PAIR_WINDOW_S:
                    same_minute += 1
                if (
                    dt < _ROUNDTRIP_WINDOW_S
                    and abs(float(a.get("qty") or 0) - float(b.get("qty") or 0))
                    <= _QTY_EPS
                ):
                    roundtrips += 1

    return {
        "n_filled": len(fills),
        "turnover": round(turnover, 2),
        "dust_orders": dust,
        "same_minute_pairs": same_minute,
        "roundtrips_lt_1d": roundtrips,
        "max_symbol_fills": max((len(v) for v in by_symbol.values()), default=0),
        "min_value": min_value,
    }


def render_compare(
    a: Dict[str, Any],
    b: Dict[str, Any],
    label_a: str = "A",
    label_b: str = "B",
) -> str:
    """Human-readable A/B table with deltas (negative delta = less churn = better)."""
    keys = [
        "n_filled",
        "turnover",
        "dust_orders",
        "same_minute_pairs",
        "roundtrips_lt_1d",
        "max_symbol_fills",
    ]
    width = max(len(k) for k in keys)
    lines = [f"{'metric'.ljust(width)}  {label_a:>12}  {label_b:>12}  {'delta':>10}"]
    for k in keys:
        va, vb = a.get(k, 0), b.get(k, 0)
        delta = round((vb or 0) - (va or 0), 2)
        lines.append(f"{k.ljust(width)}  {va!s:>12}  {vb!s:>12}  {delta!s:>10}")
    return "\n".join(lines)


def _load_orders(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("orders") or []


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    min_value = 50.0
    if "--min-value" in args:
        i = args.index("--min-value")
        min_value = float(args[i + 1])
        del args[i : i + 2]
    if not args or len(args) > 2:
        print(__doc__)
        return 2
    a = churn_metrics(_load_orders(args[0]), min_value=min_value)
    if len(args) == 1:
        print(json.dumps(a, indent=2))
        return 0
    b = churn_metrics(_load_orders(args[1]), min_value=min_value)
    print(render_compare(a, b, label_a=args[0], label_b=args[1]))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
