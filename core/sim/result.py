"""Sim-Day result model + evaluation (#2548 UX / #82, #83).

The "easy to evaluate" half of the runner: a structured ``SimResult`` that serialises to
``sim_result.json`` (asserts / CI), renders a human-readable console summary, and diffs against a
previous run (``--compare``) so "I changed the order logic — what changed?" is a one-liner.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# #2632 (P4) — Windows console safety. The report is decorated with U+2192 ("→") and
# U+2550 ("═"); neither exists in cp1252, the default console codepage on the operator's
# machine. `print(result.summary())` therefore raised UnicodeEncodeError and took the
# report — and `--help` — down, which made the harness unusable exactly where it is run.
# We do not drop the nice glyphs: we ask the TARGET encoding whether it can carry them and
# fall back per character when it cannot. UTF-8 consoles are unchanged.
_GLYPH_FALLBACKS = {
    "→": "->",
    "═": "=",
    "─": "-",
    "•": "*",
    "⚠": "!",
    "·": ".",
}


def _target_encoding(encoding: Optional[str] = None) -> str:
    """Explicit encoding wins; else what stdout reports; else the safest assumption.

    A piped/captured stdout can report ``encoding=None`` — treat that as ASCII rather
    than guessing UTF-8, so a redirected run can never crash either.
    """
    if encoding:
        return encoding
    return getattr(sys.stdout, "encoding", None) or "ascii"


def _encodable(text: str, encoding: str) -> bool:
    try:
        text.encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _arrow(encoding: Optional[str] = None) -> str:
    """ "→" where the console can render it, "->" where it cannot."""
    enc = _target_encoding(encoding)
    return "→" if _encodable("→", enc) else "->"


def _console_safe(text: str, encoding: Optional[str] = None) -> str:
    """Per-character degradation to the target encoding — never raises, never silently
    swallows content (an unmapped, unencodable character becomes ``?``)."""
    enc = _target_encoding(encoding)
    if _encodable(text, enc):
        return text
    return "".join(
        ch if _encodable(ch, enc) else _GLYPH_FALLBACKS.get(ch, "?") for ch in text
    )


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
    # #2693 — one closing point per replayed trading day: {date, equity, cash, n_positions}.
    # EMPTY for a single-day run: a curve of one point is not a curve, and growing the field
    # would suggest a multi-day result that does not exist.
    daily_equity: List[Dict[str, Any]] = field(default_factory=list)
    # #2693 — True when the wall-clock timeout cut the run short. A truncated run still writes a
    # result, so without this flag a partial replay is INDISTINGUISHABLE from a complete one and
    # an A/B would silently compare different numbers of days.
    timed_out: bool = False

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

    # ---- multi-day (#2693) ----
    @property
    def daily_returns_pct(self) -> List[float]:
        """Day-over-day returns in percent (empty for a single-day run)."""
        curve = [
            p.get("equity") for p in self.daily_equity if p.get("equity") is not None
        ]
        return [
            round((curve[i] / curve[i - 1] - 1.0) * 100, 4)
            for i in range(1, len(curve))
            if curve[i - 1]
        ]

    @property
    def max_drawdown_pct(self) -> Optional[float]:
        """Largest peak-to-trough fall of the daily equity curve, in percent (<= 0).

        Measured against the RUNNING PEAK, not against the start: a run that gains 10% and then
        gives back 10% has a real drawdown, and reading it off the starting value would hide
        exactly the churn behaviour these A/Bs are meant to expose.
        """
        curve = [
            p.get("equity") for p in self.daily_equity if p.get("equity") is not None
        ]
        if len(curve) < 2:
            return None
        peak = curve[0]
        worst = 0.0
        for value in curve[1:]:
            peak = max(peak, value)
            if peak:
                worst = min(worst, (value / peak - 1.0) * 100)
        return round(worst, 4)

    # ---- io ----
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["derived"] = {
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "n_orders": len(self.orders),
            "n_filled": self.n_filled,
            "n_blocked": self.n_blocked,
            "timed_out": bool(self.timed_out),
        }
        if len(self.daily_equity) >= 2:
            returns = self.daily_returns_pct
            d["derived"].update(
                {
                    "n_days": len(self.daily_equity),
                    "max_drawdown_pct": self.max_drawdown_pct,
                    "best_day_pct": round(max(returns), 4) if returns else None,
                    "worst_day_pct": round(min(returns), 4) if returns else None,
                }
            )
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
    def summary(self, encoding: Optional[str] = None) -> str:
        lines = [
            "════════════════════════════════════════════════════════",
            f" Sim-Day {self.sim_date}  window {self.window}  cadence {self.cadence}",
            f" universe {len(self.symbols)} symbols · {self.n_cycles} cycles · {self.wall_time_s:.1f}s",
            "────────────────────────────────────────────────────────",
            f" orders: {len(self.orders)}  (filled {self.n_filled}, blocked {self.n_blocked})",
        ]
        for o in self.orders[:50]:
            # A blocked/skipped order record can carry an explicit None side/qty (the signal
            # never became a broker order) — coerce to a placeholder so `{None:<4}` never
            # raises. `or "?"` is right for the string fields; qty keeps a legitimate 0.
            tag = o.get("status") or "?"
            px = o.get("fill_price")
            extra = f"@ {px}" if px is not None else (o.get("blocked_reason") or "")
            side = o.get("side") or "?"
            sym = o.get("symbol") or "?"
            qty = o.get("qty")
            qty = "?" if qty is None else qty
            lines.append(f"   {side:<4} {sym:<6} {str(qty):>8}  {tag:<9} {extra}")
        if len(self.orders) > 50:
            lines.append(f"   … (+{len(self.orders) - 50} more)")
        if len(self.daily_equity) >= 2:
            # #2693 — the curve IS the result of a multi-day run: a single end number cannot show
            # whether a policy got there smoothly or gave back a peak on the way.
            lines.append("────────────────────────────────────────────────────────")
            lines.append(f" {len(self.daily_equity)} trading days:")
            returns = self.daily_returns_pct
            for i, point in enumerate(self.daily_equity):
                delta = f"{returns[i - 1]:+7.2f}%" if i else "       —"
                lines.append(
                    f"   {point['date']}  {point['equity']:>13,.2f}  {delta}  "
                    f"({point['n_positions']} pos, cash {point['cash']:,.0f})"
                )
            dd = self.max_drawdown_pct
            if dd is not None:
                lines.append(
                    f" max drawdown {dd:+.2f}%   best day {max(returns):+.2f}%   "
                    f"worst day {min(returns):+.2f}%"
                )
        if self.timed_out:
            lines.append(
                " ⚠ TRUNCATED — the wall-clock timeout stopped this run early; it covers FEWER "
                "days than requested and must not be compared against a complete run."
            )
        lines += [
            "────────────────────────────────────────────────────────",
            f" equity {self.starting_equity:,.2f} → {self.ending_equity:,.2f}   "
            f"P&L {self.pnl:+,.2f} ({self.pnl_pct:+.2f}%)",
            f" end positions: {self.end_positions or '(flat)'}",
            "════════════════════════════════════════════════════════",
        ]
        return _console_safe("\n".join(lines), encoding)


def _order_key(o: Dict[str, Any]) -> tuple:
    return (o.get("symbol"), o.get("side"), o.get("status"))


def _curve_diff(prev: SimResult, curr: SimResult) -> str:
    """Per-day equity difference, so a divergence can be dated instead of guessed."""
    prev_by_date = {p["date"]: p.get("equity") for p in prev.daily_equity}
    rows = [" per-day equity (prev → curr, Δ):"]
    for point in curr.daily_equity:
        day, curr_eq = point["date"], point.get("equity")
        prev_eq = prev_by_date.get(day)
        if prev_eq is None or curr_eq is None:
            rows.append(
                f"     {day}  {curr_eq if curr_eq is None else f'{curr_eq:,.2f}'}"
            )
            continue
        rows.append(
            f"     {day}  {prev_eq:>12,.2f} → {curr_eq:>12,.2f}   {curr_eq - prev_eq:+,.2f}"
        )
    return "\n".join(rows)


def compare(prev: SimResult, curr: SimResult, encoding: Optional[str] = None) -> str:
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
    # A truncated arm covers fewer days than it appears to — comparing it against a complete one
    # is meaningless, so this has to be impossible to miss.
    if prev.timed_out or curr.timed_out:
        which = " and ".join(
            n for n, r in (("prev", prev), ("curr", curr)) if r.timed_out
        )
        lines.append(
            f" ⚠ TRUNCATED run ({which}) — hit the wall-clock timeout, so this comparison "
            f"covers different amounts of replay. Re-run with a higher timeout."
        )
    # #2693 — multi-day: the day count and the drawdown are what an exit-policy A/B is actually
    # read on. A one-day comparison is structurally blind to exit policy (selling at the mark and
    # holding at the mark are worth the same), so the curve is the point of the whole exercise.
    if len(prev.daily_equity) >= 2 or len(curr.daily_equity) >= 2:
        lines.append(
            f" days     {len(prev.daily_equity):>4} → {len(curr.daily_equity):<4}"
        )
        p_dd, c_dd = prev.max_drawdown_pct, curr.max_drawdown_pct
        if p_dd is not None and c_dd is not None:
            lines.append(
                f" max DD   {p_dd:+.2f}% → {c_dd:+.2f}%   (Δ {c_dd - p_dd:+.2f}pp)"
            )
        lines.append(_curve_diff(prev, curr))
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
    return _console_safe("\n".join(lines), encoding)
