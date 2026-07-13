# core/specialist/report.py
# RPAR Epic #1262, Task V0 (#1263) — SpecialistReport schema home.
"""SpecialistReport — the per-symbol deep-research report DTO.

Extracted from ``core.stock_specialist`` as the clean foundation for the staged
bundle -> Dev-Env report-parity port (RPAR Epic #1262). This module owns the
*schema only*; the producer (``StockSpecialistAgent``) still lives in
``stock_specialist.py``, which re-exports this class so every existing importer
keeps working unchanged.

The fields below the "RPAR parity stubs" marker are additive and schema-only:
no producer populates them in Task V0, so the built report and its serialized
DTO (``core/engine/api_routes.py::_serialize_specialist_report``) are
byte-identical to pre-V0. They are split by how today's serializer treats them:

  * Group A (11) — the serializer ALREADY reads these via
    ``getattr(r, name, <fallback>)``. Each default below is set to exactly that
    fallback, so the emitted DTO value is unchanged.
  * Group B (7)  — the serializer does NOT read these today; they are byte-
    neutral because unread. Surfacing them in the DTO is a separate follow-up
    (Task T-SER), not V0.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional


@dataclass
class SpecialistReport:
    """Deep-dive research report for a single stock symbol."""

    symbol: str
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # AI-synthesized summaries
    news_summary: str = ""
    company_summary: str = ""
    alternative_signals: str = ""  # Non-headline intelligence

    # Raw gathered data
    insider_trades: List[Dict[str, Any]] = field(default_factory=list)
    political_trades: List[Dict[str, Any]] = field(default_factory=list)
    material_events: List[Dict[str, Any]] = field(default_factory=list)  # 8-K filings
    activist_stakes: List[Dict[str, Any]] = field(default_factory=list)  # 13D filings
    reddit_mentions: int = 0
    wiki_spike: bool = False
    short_interest_pct: Optional[float] = None
    google_trend_score: Optional[float] = None  # 0-100, relative interest

    # Fusion (dormant): per-symbol TFT prediction. "unavailable" = no model / not fetched.
    # No decision uses these unless ML_SENTIMENT_BLEND_ENABLED is set; the dormant
    # Shadow-TFT-Vote reads them to measure the signal (validate-before-activate).
    ml_direction: str = "unavailable"  # "up" | "down" | "neutral" | "unavailable"
    ml_confidence: Optional[float] = None  # [0,1] inverse-normalised quantile spread
    ml_base_return_pct: Optional[float] = None  # 50th-pct forecast (%)
    ml_bear_return_pct: Optional[float] = None  # 10th-pct forecast (%)
    ml_bull_return_pct: Optional[float] = None  # 90th-pct forecast (%)
    forecast_vol: Optional[float] = (
        None  # HAR-RV forward-vol (risk-aware sizing, later)
    )

    # Scoring
    sentiment_score: float = 50.0
    recommendation: Literal["buy", "hold", "sell"] = "hold"
    confidence: float = 0.5
    reasons: List[str] = field(default_factory=list)

    # Escalation flag: raised when something exceptional is found
    escalate: bool = False
    escalate_reason: str = ""

    # ─────────────────────────────────────────────────────────────
    # RPAR parity stubs (Task V0, #1263) — additive, schema-only.
    # No producer sets these in V0 -> report + serialized DTO unchanged.
    # ─────────────────────────────────────────────────────────────

    # Group A — the serializer already reads these; default == its getattr
    # fallback (see api_routes.py::_serialize_specialist_report), so the DTO is
    # byte-identical. Element shapes are finalised by their producers (T1/T3/T5).
    about: str = ""  # getattr(r,"about","") -> about or company_summary or "<sym>: …"
    edge_signals: List[str] = field(default_factory=list)  # getattr(...,None) or []
    investment_thesis: str = ""  # (getattr(...,"") or "")[:1500]
    bull_case: str = ""  # (getattr(...,"") or "")[:1000]
    bear_case: str = ""  # (getattr(...,"") or "")[:1000]
    headlines: List[Dict[str, Any]] = field(default_factory=list)  # (... or [])[:8]
    insider_trades_total: Optional[int] = None  # None -> len(insider_trades) in DTO
    signal_quality: str = "llm_only"  # getattr(...,"llm_only")
    walkforward_ic: Optional[float] = None  # _round_or_none(...,3)
    walkforward_sharpe: Optional[float] = None  # _round_or_none(...,2)
    ml_attention_features: List[Dict[str, Any]] = field(
        default_factory=list
    )  # (... or [])

    # Group B — NOT read by today's serializer (byte-neutral because unread).
    # Visibility in the DTO is Task T-SER, not V0.
    pros: List[str] = field(default_factory=list)
    cons: List[str] = field(default_factory=list)
    summary: str = ""
    data_quality: float = 1.0  # [0,1] data-integrity confidence (degraded path, T6a)
    degraded: bool = False  # set when a data-integrity guard trips (T6a)
    rsi_14: Optional[float] = None
    macd_signal: Optional[str] = None  # "bullish" | "bearish" | None
