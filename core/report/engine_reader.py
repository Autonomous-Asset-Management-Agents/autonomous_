# core/report/engine_reader.py
# RPT-6 (Epic #1998) — the production FactSetReader that wires RPT-1's
# build_fact_set to the REAL engine data sources.
"""Production :class:`~core.report.fact_set.FactSetReader` over the live sources.

RPT-1 (:mod:`core.report.fact_set`) builds a frozen, PIT FactSet from an injected
reader; RPT-5 said "production wiring supplies its own reader over the real
backends". This module is that reader. It goes to the SAME canonical sources the
engine already uses, and — where a source is not PIT-reconstructable in the live
engine — it degrades to an **honest gap** (never a fabricated number), exactly as
the pipeline's design contract demands.

What is wired to REAL data:

* **Price bars** — the canonical :class:`core.data_provider.HistoricalDataProvider`
  (Alpaca / Databento / GCS cache), the very source
  ``core.stock_specialist._get_data_provider().get_data(...)`` feeds the ML path.
  Bars are fetched with an end bound of ``as_of`` (PIT at fetch), and
  ``build_fact_set`` applies the calendar-date PIT filter again as the single
  source of truth.
* **Fundamentals** — :class:`core.report.financials_feed.CachedFundamentalsReader`
  over the nightly PIT cache. A cold cache yields ``None`` — an honest gap.
* **Company identity** — :class:`core.report.company_feed.CachedCompanyReader` over the
  ``scripts/prefetch_company_universe.py`` cache (ADR-RPT-COMPANY-01). A cold cache
  yields ``None``. ⚠ This is the one source that is NOT point-in-time — the endpoint is
  current-state only — so the facts carry their own ``fetched_at`` and the renderer
  dates them ("Stand <date>") instead of implying they hold as of ``as_of``.
* **Model card** — the committed static walk-forward constants
  (``core/report/model_card.json``).

What is an HONEST GAP by default (injectable seams for later wiring):

* **LSTM panel** (``get_lstm_series`` / ``get_lstm_cross_section``) — the live
  ``model_registry`` produces a *now-based* single prediction, which would be a
  lookahead for a historical ``as_of`` and carries no cross-section; so it is NOT
  wired into the dormant default. A PIT panel reader can be injected via
  ``lstm_reader`` (``.series(symbol)`` / ``.cross_section(as_of)``).
* **News** (``get_news``) — a structured, PIT ``published_utc`` feed is a
  follow-up; the specialist's headline strings carry no timestamp/URL, so they
  cannot be turned into provenanced :class:`~core.report.fact_set.NewsItem`s
  honestly. Injectable via ``news``.

Report-only and DORMANT: nothing here is read by the decision path;
``SPECIALIST_ALPHA_WEIGHT`` stays 0. This reader is only ever constructed on the
flag-gated ``REPORT_GENERATOR_V2_ENABLED`` path.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time, timezone
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from core.report.company_feed import CachedCompanyReader
from core.report.fact_set import NewsItem
from core.report.financials_feed import CachedFundamentalsReader, to_report_fundamentals

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_CARD_PATH = os.path.join(os.path.dirname(__file__), "model_card.json")
# ~2 calendar years -> >= 252 trading days, enough for the MA200 / 52-week facts.
_DEFAULT_LOOKBACK_DAYS = 800

_BarRow = Tuple[date, float]
_ScoreRow = Tuple[date, float]


# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------
def _as_date(value: Any) -> date:
    """Normalise a ``date`` / ``datetime`` / pandas Timestamp / ISO string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value[:10]).date()
    if hasattr(value, "date") and callable(value.date):  # pandas.Timestamp etc.
        return value.date()
    raise TypeError(f"Unsupported date type: {type(value)!r}")


def _end_dt(as_of_d: date) -> datetime:
    """End-of-day UTC on ``as_of`` — the PIT upper bound handed to the provider."""
    return datetime.combine(as_of_d, time(23, 59, 59), tzinfo=timezone.utc)


def _rows_from_frame(obj: Any) -> List[_BarRow]:
    """Convert a provider result to ``(date, close)`` rows.

    Accepts a ``HistoricalDataProvider`` frame (a ``.index`` + a ``"close"``
    column) or a plain sequence of ``(date, close)`` rows. Unparseable / NaN /
    missing closes are skipped (honest gap), never fabricated.
    """
    if obj is None:
        return []
    idx = getattr(obj, "index", None)
    if idx is not None and not isinstance(obj, (list, tuple)):
        if len(idx) == 0:
            return []  # empty frame (cold cache / no data) — a quiet honest gap
        try:
            closes = obj["close"]
        except Exception:  # noqa: BLE001 — a frame without a close column is a gap
            logger.warning("engine_reader: provider frame has no 'close' column")
            return []
        out: List[_BarRow] = []
        for ts, value in zip(list(idx), list(closes)):
            try:
                if value is None or value != value:  # None or NaN
                    continue
                out.append((_as_date(ts), float(value)))
            except (TypeError, ValueError):
                continue
        return out
    out = []
    for row in obj:
        try:
            d, value = row
            if value is None or value != value:  # None or NaN
                continue
            out.append((_as_date(d), float(value)))
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# The reader.
# ---------------------------------------------------------------------------
class EngineFactSetReader:
    """A :class:`~core.report.fact_set.FactSetReader` over the live engine sources.

    Constructed per report (one symbol, one ``as_of``). Every source is
    guarded: any failure degrades to an honest gap so a single cold/broken feed
    can never crash report generation. All seams are injectable for testing.
    """

    def __init__(
        self,
        as_of: Any,
        *,
        data_provider: Any = None,
        fundamentals_reader: Any = None,
        company_reader: Any = None,
        model_card_path: Optional[str] = None,
        lstm_reader: Any = None,
        news: Optional[Sequence[NewsItem]] = None,
        alt_signals: Optional[Mapping[str, Any]] = None,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        self._as_of = _as_date(as_of)
        self._end_dt = _end_dt(self._as_of)
        self._data_provider = data_provider  # lazy default (HistoricalDataProvider)
        self._fundamentals_reader = (
            fundamentals_reader
            if fundamentals_reader is not None
            else CachedFundamentalsReader()
        )
        # Mirrors the fundamentals seam exactly: the cached reader by default, so the
        # fact is WIRED rather than waiting for a caller that never comes (a verified
        # mapper with no caller is a dead feature — that shape shipped three times this
        # week). A cold cache degrades to None, an honest gap.
        self._company_reader = (
            company_reader if company_reader is not None else CachedCompanyReader()
        )
        self._model_card_path = model_card_path or _DEFAULT_MODEL_CARD_PATH
        self._lstm_reader = lstm_reader
        # ⚠ `if news else []` collapsed None into [] — i.e. 'nobody attached a feed'
        # became indistinguishable from 'the feed answered with nothing'. The report
        # then cited a feed it never called. None must survive.
        self._news: Optional[List[NewsItem]] = list(news) if news is not None else None
        self._alt_signals: dict = dict(alt_signals) if alt_signals else {}
        self._lookback_days = int(lookback_days)
        self._card_cache: Optional[dict] = None

    # -- lazy provider ------------------------------------------------------
    def _provider(self) -> Any:
        if self._data_provider is None:
            from core.data_provider import HistoricalDataProvider

            self._data_provider = HistoricalDataProvider()
        return self._data_provider

    # -- FactSetReader Protocol --------------------------------------------
    def get_bars(self, symbol: str) -> Sequence[_BarRow]:
        try:
            frame = self._provider().get_data(
                str(symbol).strip().upper(), self._end_dt, self._lookback_days
            )
        except Exception as exc:  # noqa: BLE001 — a data feed error is an honest gap
            logger.warning("engine_reader: bars unavailable for %s: %r", symbol, exc)
            return []
        return _rows_from_frame(frame)

    def get_lstm_series(self, symbol: str) -> Sequence[_ScoreRow]:
        if self._lstm_reader is None:
            return []
        try:
            return list(self._lstm_reader.series(symbol) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("engine_reader: lstm series gap for %s: %r", symbol, exc)
            return []

    def get_lstm_cross_section(self, as_of: date) -> Mapping[str, float]:
        if self._lstm_reader is None:
            return {}
        try:
            return dict(self._lstm_reader.cross_section(_as_date(as_of)) or {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("engine_reader: lstm cross-section gap: %r", exc)
            return {}

    def get_model_card(self) -> Mapping[str, Any]:
        if self._card_cache is not None:
            return self._card_cache
        try:
            with open(self._model_card_path, encoding="utf-8") as handle:
                self._card_cache = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("engine_reader: model card unavailable: %r", exc)
            self._card_cache = {}
        return self._card_cache

    def get_fundamentals(self, symbol: str, as_of: date) -> Optional[Mapping[str, Any]]:
        """PIT fundamentals in the schema §6 actually reads.

        ⚠ This used to be a bare pass-through, and that was the whole bug: the feed
        emits `revenue` / `gross_margin` / `eps_yoy`; §6 reads `revenue_bn` /
        `gross_margin_pct` / `eps_yoy_pct`. Only 4 of 18 keys ever overlapped, so §6
        rendered "n/a" on a perfectly derived filing — and filling the cache for 500
        symbols would have produced 500 reports of n/a. `to_report_fundamentals` is
        the translation, and this is the one seam both sides pass through.
        """
        try:
            derived = self._fundamentals_reader.get_fundamentals(symbol, as_of)
            return to_report_fundamentals(derived)
        except Exception as exc:  # noqa: BLE001
            logger.warning("engine_reader: fundamentals gap for %s: %r", symbol, exc)
            return None

    def get_news(self, symbol: str) -> Optional[Sequence[NewsItem]]:
        """None when no feed is attached — never an empty list standing in for one."""
        return list(self._news) if self._news is not None else None

    def get_company(self, symbol: str) -> Optional[Mapping[str, Any]]:
        """Company identity facts off the prefetch cache, or an honest gap.

        ⚠ ``None`` (cold cache / unreadable / reader blew up) and ``{}`` (the source
        answered and carries nothing for this ticker) are DIFFERENT claims and both
        survive this method unchanged. A ``or {}`` here would erase the distinction the
        whole feature rests on — the §0 renderer branches on exactly it.
        """
        try:
            return self._company_reader.get_company(symbol)
        except Exception as exc:  # noqa: BLE001 — a broken cache is an honest gap
            logger.warning("engine_reader: company gap for %s: %r", symbol, exc)
            return None

    def get_alt_signals(self, symbol: str) -> Mapping[str, Any]:
        return dict(self._alt_signals)


__all__ = ["EngineFactSetReader"]
