# core/specialist/__init__.py
# RPAR Epic #1262, Task V0 (#1263) — extracted specialist-report package.
"""``core.specialist`` — the clean home for the specialist-report stack.

Phase V0 of the Specialist-Report-Parity epic (#1262) lands the
``SpecialistReport`` schema here. The legacy import path
``core.stock_specialist.SpecialistReport`` re-exports from this package, so all
existing producers/consumers are unaffected (byte-identical behaviour). Later
tasks (T1..T6) grow this package with the prompt/parser, card builders, news
merge, provider routing, and the insight-quality pipeline.
"""

from core.specialist.report import SpecialistReport

__all__ = ["SpecialistReport"]
