# core/report/__init__.py
# RPT-1 (#2000, Epic #1998) — auditable GS-quality report stack.
"""``core.report`` — the deterministic fact foundation for auditable reports.

RPT-1 lands the fact layer (:mod:`core.report.fact_set`).
RPT-2 adds the point-in-time fundamentals feed (:mod:`core.report.financials_feed`) that wires RPT-1's optional ``fundamentals`` field.
RPT-3 adds the deterministic renderer (:mod:`core.report.render`).
RPT-4 adds the mechanical auto-audit gate (:mod:`core.report.auditor`).
RPT-5 adds the LLM narrator that fills the renderer's prose slots through the sanctioned LLM provider seam (:mod:`core.report.narrator`).
RPT-6 wires the pipeline end to end (:mod:`core.report.pipeline`) over the real engine sources (:mod:`core.report.engine_reader`), behind the dormant ``REPORT_GENERATOR_V2_ENABLED`` flag.
RPT-7 adds the deterministic insight-pattern layer (:mod:`core.report.insight_patterns`) — the code detects the deep analytical patterns and derives their implications, rendered as the ``## Insights`` section.
Later tasks grow this package with the RPT-4b semantic judge.
Report-only and dormant: ``SPECIALIST_ALPHA_WEIGHT`` stays 0.
"""

from core.report.auditor import AuditResult, Claim, audit, semantic_attribution_hook

# RPT-6 wiring. ``engine_reader`` / ``pipeline`` only import sibling submodules
# (fact_set / financials_feed / render / narrator / auditor) — never the package
# namespace — so there is no import cycle regardless of ordering. ``pipeline``
# lazily imports ``engine_reader``, and ``engine_reader`` lazily imports the data
# provider, so importing ``core.report`` never pulls the data-provider chain until
# report generation actually runs.
from core.report.engine_reader import EngineFactSetReader
from core.report.fact_set import (
    Extreme,
    Fact,
    FactSet,
    FactSetReader,
    InMemoryDataContext,
    NewsItem,
    build_fact_set,
)
from core.report.financials_feed import (
    CachedFundamentalsReader,
    derive_fundamentals,
    fetch_financials_vX,
    fetch_raw_financials,
)
from core.report.insight_patterns import Insight, detect_insights
from core.report.narrator import narrate, serialize_fact_set
from core.report.pipeline import ReportBundle, build_report_bundle, generate_v2_report
from core.report.render import render

__all__ = [
    "AuditResult",
    "Claim",
    "Extreme",
    "Fact",
    "FactSet",
    "FactSetReader",
    "InMemoryDataContext",
    "Insight",
    "NewsItem",
    "audit",
    "build_fact_set",
    "CachedFundamentalsReader",
    "derive_fundamentals",
    "fetch_financials_vX",
    "fetch_raw_financials",
    "detect_insights",
    "narrate",
    "render",
    "semantic_attribution_hook",
    "serialize_fact_set",
    "EngineFactSetReader",
    "ReportBundle",
    "build_report_bundle",
    "generate_v2_report",
]
