"""Sim-Day result model + evaluation (#2548 UX / #82, #83).

The "easy to evaluate" half of the runner: a structured ``SimResult`` that serialises to
``sim_result.json`` (asserts / CI), renders a human-readable console summary, and diffs against a
previous run (``--compare``) so "I changed the order logic — what changed?" is a one-liner.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class OrderRecord:
    """One order the sim produced. ``status`` ∈ filled | blocked | rejected | accepted."""

    symbol: str
    side: str
    qty: float
    status: str
    fill_price: Optional[float] = None
    blocked_reason: Optional[str] = None


@dataclass
class SimResult:
    sim_date: str
    window: str
    cadence: str
    symbols: List[str] = field(default_factory=list)
    orders: List[Dict[str, Any]] = field(default_factory=list)
    end_positions: Dict[str, float] = field(default_factory=dict)  # symbol -> qty
    starting_equity: float = 0.0
    ending_equity: float = 0.0
    n_cycles: int = 0
    wall_time_s: float = 0.0

    # ---- derived ----
    @property
    def pnl(self) -> float:
        return round(self.ending_equity - self.starting_equity, 2)

    @property
    def pnl_pct(self) -> float:
        return (
            round((self.ending_equity / self.starting_equity - 1.0) * 100, 4)
            if self.starting_equity
            else 0.0
        )

    def _by_status(self, status: str) -> List[Dict[str, Any]]:
        return [o for o in self.orders if o.get("status") == status]

    @property
    def n_filled(self) -> int:
        return len(self._by_status("filled"))

    @property
    def n_blocked(self) -> int:
        return len(self._by_status("blocked"))

    # ---- io ----
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["derived"] = {
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "n_orders": len(self.orders),
            "n_filled": self.n_filled,
            "n_blocked": self.n_blocked,
        }
        return d

    def to_json(self, path: str) -> str:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def from_json(cls, path: str) -> "SimResult":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        d.pop("derived", None)
        return cls(**d)

    # ---- rendering ----
    def summary(self) -> str:
        lines = [
            "════════════════════════════════════════════════════════",
            f" Sim-Day {self.sim_date}  window {self.window}  cadence {self.cadence}",
            f" universe {len(self.symbols)} symbols · {self.n_cycles} cycles · {self.wall_time_s:.1f}s",
            "────────────────────────────────────────────────────────",
            f" orders: {len(self.orders)}  (filled {self.n_filled}, blocked {self.n_blocked})",
        ]
        for o in self.orders[:50]:
            tag = o.get("status", "?")
            px = o.get("fill_price")
            extra = f"@ {px}" if px is not None else (o.get("blocked_reason") or "")
            side = o.get("side", "?")
            sym = o.get("symbol", "?")
            qty = o.get("qty", "?")
            lines.append(f"   {side:<4} {sym:<6} {qty:>8}  {tag:<9} {extra}")
        if len(self.orders) > 50:
            lines.append(f"   … (+{len(self.orders) - 50} more)")
        lines += [
            "────────────────────────────────────────────────────────",
            f" equity {self.starting_equity:,.2f} → {self.ending_equity:,.2f}   "
            f"P&L {self.pnl:+,.2f} ({self.pnl_pct:+.2f}%)",
            f" end positions: {self.end_positions or '(flat)'}",
            "════════════════════════════════════════════════════════",
        ]
        return "\n".join(lines)


def _order_key(o: Dict[str, Any]) -> tuple:
    return (o.get("symbol"), o.get("side"), o.get("status"))


def compare(prev: SimResult, curr: SimResult) -> str:
    """Human-readable diff between two runs — the ``--compare`` "what changed?" view."""
    prev_keys = {_order_key(o) for o in prev.orders}
    curr_keys = {_order_key(o) for o in curr.orders}
    added = [o for o in curr.orders if _order_key(o) not in prev_keys]
    removed = [o for o in prev.orders if _order_key(o) not in curr_keys]

    lines = [
        "═══════════════ Sim-Day compare ═══════════════",
        f" orders   {len(prev.orders):>4} → {len(curr.orders):<4}   "
        f"(filled {prev.n_filled}→{curr.n_filled}, blocked {prev.n_blocked}→{curr.n_blocked})",
        f" P&L      {prev.pnl:+,.2f} → {curr.pnl:+,.2f}   "
        f"(Δ {curr.pnl - prev.pnl:+,.2f})",
        f" equity   {prev.ending_equity:,.2f} → {curr.ending_equity:,.2f}",
    ]
    if added:
        lines.append(f" + {len(added)} new order(s):")
        for o in added[:20]:
            lines.append(
                f"     + {o.get('side')} {o.get('symbol')} {o.get('qty')} [{o.get('status')}]"
            )
    if removed:
        lines.append(f" - {len(removed)} gone order(s):")
        for o in removed[:20]:
            lines.append(
                f"     - {o.get('side')} {o.get('symbol')} {o.get('qty')} [{o.get('status')}]"
            )
    if not added and not removed:
        lines.append(" (no order-set change)")
    lines.append("════════════════════════════════════════════════")
    return "\n".join(lines)
