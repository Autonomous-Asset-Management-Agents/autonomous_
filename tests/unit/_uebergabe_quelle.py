"""#3945 — Quelltext der Signal-Übergabe: Dirigent + Schritte, in Aufrufreihenfolge.

Plan: ``docs/3945-vorarbeit-testkonstruktion-und-waechter/implementation_plan.md`` §2.

Elf Wächter sichern **Mitlage** über ``_process_signal_event``: dass bestimmte Aufrufe in
*einer* Quelle in einer bestimmten Ordnung stehen. Zieht G-1a (#3819) Blöcke in benannte
Schritte, verlässt ihr Text die Funktion — ``inspect.getsource`` sähe ihn nicht mehr, die
Wächter verlören ihren Gegenstand. Dieser Helfer liefert deshalb den **Dirigenten plus
seine Schritte**, in der Reihenfolge, in der der Dirigent sie aufruft. Die Reihenfolge
wird **aus dem Dirigenten gelesen**, nicht hier gepflegt.

Ein Schritt ist ein Aufruf ``self._schritt_<name>(...)`` im Dirigenten. Aufgelöst wird an
der **komponierten** Klasse (``BotEngine``, ``base.py:100-106``), weil die Schritte dort
liegen werden, wo die Produktion sie findet — nicht unbedingt auf ``OrderExecutorMixin``.

Solange es keine Schritte gibt, ist das Ergebnis **zeichengleich** mit
``inspect.getsource(_process_signal_event)``. Findet der Helfer Dirigent oder Schritt
nicht, **erhebt** er — leerer Text machte alle Wächter stillschweigend wahr.

Helfermodul (wie ``_geld_gate.py``) — pytest sammelt es nicht. Eigentests:
``tests/unit/test_uebergabe_quelle.py``.
"""

from __future__ import annotations

import ast
import inspect
import sys
import textwrap
from pathlib import Path

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

DIRIGENT = "_process_signal_event"
#: #3821 (G-1b): Auch die Absendung je Mandant ist ein Dirigent mit ``_schritt_mandant_*``.
DIRIGENTEN = (DIRIGENT, "_execute_tenant_order")
SCHRITT_PRAEFIX = "_schritt_"


def _komponiert() -> type:
    """Die Klasse, die die Produktion fährt."""
    from core.engine.base import BotEngine

    return BotEngine


def _quelle_von(klasse: type, name: str, rolle: str) -> str:
    fn = inspect.getattr_static(klasse, name, None)
    if fn is None:
        raise LookupError(
            f"{rolle} {klasse.__name__}.{name} nicht gefunden — "
            "ohne Quelle würde jeder Wächter stillschweigend wahr"
        )
    src = inspect.getsource(fn)
    if not src.strip():
        raise LookupError(f"{rolle} {klasse.__name__}.{name}: leerer Quelltext")
    return src


def _schritte(quelle: str) -> list[str]:
    """Die ``self._schritt_*``-Aufrufe des Dirigenten, in Quelltextreihenfolge, je einmal."""
    baum = ast.parse(textwrap.dedent(quelle))
    aufrufe = sorted(
        (n.lineno, n.col_offset, n.func.attr)
        for n in ast.walk(baum)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
        and n.func.attr.startswith(SCHRITT_PRAEFIX)
    )
    return list(dict.fromkeys(name for _, _, name in aufrufe))


def uebergabe_quelle(klasse: type | None = None, dirigent: str = DIRIGENT) -> str:
    """Quelltext des Dirigenten, gefolgt von seinen Schritten in Aufrufreihenfolge."""
    klasse = klasse or _komponiert()
    quelle = _quelle_von(klasse, dirigent, "Dirigent")
    return quelle + "".join(
        _quelle_von(klasse, name, "Schritt") for name in _schritte(quelle)
    )


def quelle(func) -> str:
    """Ersatz für ``inspect.getsource`` in den Wächtern.

    Für einen Dirigenten (``DIRIGENTEN``) dessen Quelle samt Schritten, für jede andere
    Funktion ``inspect.getsource`` — so bleiben parametrisierte Wächter unverändert.
    """
    for dirigent in DIRIGENTEN:
        if func is inspect.getattr_static(_komponiert(), dirigent, None):
            return uebergabe_quelle(dirigent=dirigent)
    return inspect.getsource(func)
