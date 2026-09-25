# core/xai/stock_research.py
# XAI-1 / XAI-T6 (#1335) — Stock-Research domain provider.
#
# Answers fundamentals / sector / insider / news-sentiment questions STRICTLY from the
# recorded SpecialistReport (core/specialist/report.py — the stable RPAR-1 schema) —
# ZERO-HALLUCINATION: only fields the report actually carries are rendered; NO financial
# value is invented. If no report exists (symbol not researched, or the research registry
# is not running in this OSS deployment), it DEGRADES safely with a clear message rather
# than fabricating. No creative LLM touches this path.
#
# Import-light: stdlib + the T3 ticker/number helpers. The heavy StockSpecialistRegistry is
# INJECTED (duck-typed), never imported here — so this module pulls no engine/torch deps.
from __future__ import annotations

import dataclasses
import math
from typing import Any, Optional

from core.xai.interfaces import IDomainProvider, ISpecialistReportSource
from core.xai.trading_history import _fmt_num, extract_symbol

_NO_SYMBOL = "Please name a stock ticker (e.g. AAPL) for the research lookup."


def _no_report(symbol: str) -> str:
    return (
        f"No SpecialistReport on record for {symbol} (not yet researched, or the research "
        "registry is not running on this deployment). No data to report."
    )


def _to_dict(report: Any) -> dict:
    """A SpecialistReport (dataclass) or an already-dict report -> plain dict. Anything else
    (or a failing conversion) -> {} (the caller degrades). Never raises."""
    if isinstance(report, dict):
        return report
    try:
        if dataclasses.is_dataclass(report) and not isinstance(report, type):
            return dataclasses.asdict(report)
    except Exception:  # noqa: BLE001
        return {}
    return {}


def _num(v: Any) -> Optional[float]:
    # Reject bool AND non-finite (NaN/inf): a corrupt/hostile source must never render a
    # garbage figure under the "no figure is invented" banner.
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return v


def render_report(report: dict, symbol: str) -> str:
    """Zero-hallucination rendering: only fields the report actually carries are shown; an
    absent/empty field is omitted (never defaulted into a claim). If nothing is populated,
    degrade to the honest no-data message. Every figure is copied verbatim from the record.
    """
    lines = [f"Stock research for {symbol}:"]

    head = []
    rec = report.get("recommendation")
    if isinstance(rec, str) and rec.strip():
        head.append(f"recommendation: {rec.strip()}")
    sent = _num(report.get("sentiment_score"))
    if sent is not None:
        head.append(f"sentiment {_fmt_num(sent)}/100")
    conf = _num(report.get("confidence"))
    if conf is not None:
        head.append(f"confidence {_fmt_num(conf)}/1")
    if head:
        lines.append("  " + " · ".join(head))

    for label, key in (
        ("Company", "company_summary"),
        ("News", "news_summary"),
        ("Thesis", "investment_thesis"),
    ):
        val = report.get(key)
        if isinstance(val, str) and val.strip():
            lines.append(f"  {label}: {val.strip()}")

    insiders = report.get("insider_trades")
    if isinstance(insiders, list) and insiders:
        lines.append(f"  Insider trades on record: {len(insiders)}")
    political = report.get("political_trades")
    if isinstance(political, list) and political:
        lines.append(f"  Political trades on record: {len(political)}")
    short = _num(report.get("short_interest_pct"))
    if short is not None:
        lines.append(f"  Short interest: {_fmt_num(short)}%")

    raw_reasons = report.get("reasons")
    reasons = (
        [r for r in raw_reasons if isinstance(r, str) and r.strip()]
        if isinstance(raw_reasons, list)
        else []  # a non-list `reasons` (scalar -> crash, string -> char-spam) is dropped
    )
    if reasons:
        lines.append("  Reasons: " + "; ".join(reasons[:5]))

    if len(lines) == 1:  # only the header -> nothing on record was populated
        return _no_report(symbol)
    lines.append("  (From the on-record SpecialistReport — no figure is invented.)")
    return "\n".join(lines)


class RegistrySpecialistReportSource(ISpecialistReportSource):
    """OSS source: the latest SpecialistReport for a symbol from the INJECTED registry
    (duck-typed: any object with ``get_report(symbol) -> report | None``), serialized to a
    plain dict. Robust/fail-safe: no registry, a raising registry, or no report -> None
    (the provider degrades). The registry is injected so this module stays import-light.
    """

    def __init__(self, *, registry: Any = None) -> None:
        self._registry = registry

    async def get_report(self, symbol: str) -> Optional[dict]:
        if self._registry is None:
            return None
        try:
            report = self._registry.get_report(symbol)
        except Exception:  # noqa: BLE001 — a research read must never crash the chat
            return None
        if report is None:
            return None
        data = _to_dict(report)
        return data or None


class StockResearchProvider(IDomainProvider):
    """Stock-Research handler: extract a ticker, read its SpecialistReport, render it from
    on-record fields ONLY. Degrades safely (a clear message) when no ticker is named or no
    report exists. Returns ``{text, report, symbol}``."""

    def __init__(self, *, source: Optional[ISpecialistReportSource] = None) -> None:
        self._source = source

    async def answer(self, request: Any) -> dict:
        text = getattr(request, "text", "") or ""
        symbol = extract_symbol(text)
        if not symbol:
            return {"text": _NO_SYMBOL, "report": None, "symbol": None}
        report = (
            await self._source.get_report(symbol) if self._source is not None else None
        )
        if not isinstance(report, dict) or not report:
            return {"text": _no_report(symbol), "report": None, "symbol": symbol}
        return {
            "text": render_report(report, symbol),
            "report": report,
            "symbol": symbol,
        }
