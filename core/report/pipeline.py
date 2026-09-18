# core/report/pipeline.py
# RPT-6 (Epic #1998) — the end-to-end report pipeline, wired behind the dormant flag.
"""Compose the auditable report: fact_set -> narrate -> render (+insights) -> audit.

This is the single orchestration seam the product calls to turn a symbol + as-of
into an **auditable Markdown research report**. It stitches the RPT-1..RPT-7
modules together and returns both the report and its audit verdict:

    build_fact_set(reader)            # RPT-1: the deterministic, PIT fact layer
      -> narrate(fs, get_llm_provider)# RPT-5: the ONLY LLM touch, via the seam
      -> render(fs, narration)        # RPT-3 renderer; RPT-7 detect_insights runs
                                      #   INSIDE render -> the ``## Insights`` section
      -> audit(md, fs)                # RPT-4: mechanical falsifiability gate

Design contract:

* **Report-only and DORMANT.** Nothing here is read by the decision path;
  ``SPECIALIST_ALPHA_WEIGHT`` stays 0. This module is only ever reached on the
  flag-gated ``REPORT_GENERATOR_V2_ENABLED`` path.
* **BORA / edition-agnostic.** The model is reached ONLY through RPT-5's
  sanctioned ``get_llm_provider()`` seam (``llm=None`` -> the seam; no provider
  is named or configured here). No Redis / SQLite / ``DEPLOYMENT_MODE`` branch.
* **Insights need no wiring here.** RPT-7's ``detect_insights`` is called by
  ``render`` itself (``render._section_insights``), so the deterministic
  ``## Insights`` section renders (and audits) automatically — the pipeline just
  calls ``render`` and does not compute or forward insights of its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.report.auditor import AuditResult, audit
from core.report.fact_set import FactSet, build_fact_set
from core.report.narrator import narrate as _narrate
from core.report.render import render

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportBundle:
    """The composed report plus the facts it was built from and its audit verdict."""

    fact_set: FactSet
    markdown: str
    audit: AuditResult


def build_report_bundle(
    symbol: str,
    as_of: Any,
    *,
    reader: Any = None,
    llm: Any = None,
    narrate: bool = True,
) -> ReportBundle:
    """Run the full pipeline and return the audited :class:`ReportBundle`.

    Parameters
    ----------
    symbol / as_of:
        The report identity and its point-in-time cutoff.
    reader:
        A :class:`~core.report.fact_set.FactSetReader`. When ``None`` the real
        :class:`~core.report.engine_reader.EngineFactSetReader` over the live
        sources is used (lazy import keeps this module free of the data-provider
        chain until it is actually needed).
    llm:
        Passed through to RPT-5 ``narrate``. ``None`` -> the sanctioned
        ``get_llm_provider()`` seam; when the seam yields no provider every prose
        slot abstains (a deterministic placeholder) and the report still audits.
    narrate:
        **Deterministic-first switch (RPT-8b).** When ``False`` the RPT-5 narrate
        stage is skipped entirely — no LLM is resolved or called — and ``render``
        runs with ``narration=None``, so the prose slots fall back to their
        deterministic placeholders. This yields a complete, audit-clean report at
        **zero LLM cost**, which is what makes generating a report for the whole
        ~500-symbol universe cheap (the prose is filled lazily, on report-open).
        ``llm`` is ignored in this mode.
    """
    if reader is None:
        from core.report.engine_reader import EngineFactSetReader

        reader = EngineFactSetReader(as_of)

    fact_set = build_fact_set(symbol, as_of, reader)
    narration = _narrate(fact_set, llm=llm) if narrate else None
    markdown = render(fact_set, narration=narration)
    result = audit(markdown, fact_set)
    return ReportBundle(fact_set=fact_set, markdown=markdown, audit=result)


def generate_v2_report(
    symbol: str,
    as_of: Any,
    *,
    reader: Any = None,
    llm: Any = None,
    narrate: bool = True,
) -> str:
    """Return the auditable Markdown report for ``symbol`` as of ``as_of``.

    Thin string wrapper over :func:`build_report_bundle` (report-only). This is
    the function the specialist wiring and the ``scripts/generate_report.py``
    entrypoint call. ``narrate=False`` produces the deterministic report with no
    LLM touch (see :func:`build_report_bundle`).
    """
    return build_report_bundle(
        symbol, as_of, reader=reader, llm=llm, narrate=narrate
    ).markdown


__all__ = ["ReportBundle", "build_report_bundle", "generate_v2_report"]
