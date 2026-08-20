# core/report/fact_set.py
# RPT-1 (#2000, Epic #1998) — the deterministic FACT foundation of the auditable
# GS-quality research reports.
"""Deterministic, point-in-time FactSet builder.

This module is the *fact* layer of the auditable report pipeline (Epic #1998).
It turns raw, injected readers into a frozen :class:`FactSet` in which **every
derived field carries its own provenance** as a :class:`Fact` ``(value, src)``
pair — the substance a BaFin/MiFID audit trail is built on.

Design contract (BORA / dependency-injection seam):

* **Pure core.** No I/O, no persistence backend, no network, no LLM, no GPU, no
  ``pandas``, no clock (``datetime.now``), no randomness. Given the same input it
  returns a byte-identical FactSet. All data arrives through the injected
  :class:`FactSetReader` (``data_ctx``) — the module never opens a file or a
  socket itself. This keeps it edition-agnostic (no Redis / SQLite /
  ``DEPLOYMENT_MODE`` / edition special-casing).
* **Point-in-time (PIT), no lookahead.** News with ``published_utc`` after
  ``as_of`` are dropped; fundamentals with ``filing_date`` after ``as_of`` are
  dropped; the LSTM history is truncated at ``as_of``. The panel is daily, so the
  PIT rule is evaluated at *calendar-date* granularity (an item stamped on
  ``as_of`` itself is in-window).
* **Honest gaps, never fabrication.** A missing feed yields ``None`` / ``[]``
  with an explanatory ``src`` — never an invented number. Fundamentals are
  OPTIONAL: RPT-2 wires the feed later, so until then the field is honestly
  provenanced as "not wired".

Report-only and DORMANT: nothing here is read by the decision path;
``SPECIALIST_ALPHA_WEIGHT`` stays 0.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from math import sqrt
from typing import Any, List, Mapping, Optional, Protocol, Sequence, Tuple

from core.report.lstm_panel_store import cross_section_standing
from core.specialist.cards import build_pros_cons

# ---------------------------------------------------------------------------
# Tunables (display/heuristic — no order or capital path reads them).
# ---------------------------------------------------------------------------
_MA_WINDOWS: Tuple[int, ...] = (20, 50, 100, 200)
_RVOL_WINDOWS: Tuple[int, ...] = (20, 63)
_ANNUALIZATION = 252  # trading days / year (realized-vol annualisation)
_PULLBACK_LEN = 5  # closes shown in the pullback series
_TREND_LEN = 5  # LSTM scores shown in the score-trend
_WIN_1M = 20  # ~1 trading month
_WIN_52W = 252  # ~52 trading weeks

_BarRow = Tuple[date, float]
_ScoreRow = Tuple[date, float]


# ---------------------------------------------------------------------------
# Value objects (all frozen -> hashable, comparable, determinism-friendly).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Fact:
    """A single derived fact and its provenance.

    ``value`` may be ``None`` (an honest gap). ``src`` is always a non-empty,
    human-readable provenance string — the load-bearing part of the audit trail.
    """

    value: Any
    src: str


@dataclass(frozen=True)
class Extreme:
    """A windowed high/low: the price and the date it occurred on."""

    price: float
    on: date


@dataclass(frozen=True)
class NewsItem:
    """A single PIT-valid catalyst headline with full provenance."""

    title: str
    published_utc: str  # ISO-8601, e.g. "2026-04-29T21:00:00+00:00"
    publisher: str
    url: str


@dataclass(frozen=True)
class FactSet:
    """Frozen, fully-provenanced fact foundation for one symbol at one as-of.

    ``symbol`` / ``as_of`` are the identity; every other field is a
    :class:`Fact` carrying ``(value, src)``.
    """

    symbol: str
    as_of: date

    # --- company identity (ADR-RPT-COMPANY-01) ------------------------------
    # ⚠ NOT point-in-time, and deliberately so — see build_fact_set. Carries its own
    # fetch date so the renderer can date the figures instead of implying as-of.
    company: Fact  # dict | {} | None

    # --- price / technicals -------------------------------------------------
    close: Fact
    pullback_series: Fact  # List[float] — last N closes
    pullback_pct: Fact  # float — pct move across the pullback series
    ma20: Fact
    ma50: Fact
    ma100: Fact
    ma200: Fact
    high_52w: Fact  # Extreme
    low_52w: Fact  # Extreme
    high_1m: Fact  # Extreme
    low_1m: Fact  # Extreme
    rvol_20d: Fact  # float — annualised realized vol (fraction)
    rvol_63d: Fact

    # --- cross-sectional LSTM (the directional driver) ----------------------
    lstm_score: Fact
    lstm_own_percentile: Fact  # vs own history
    lstm_xsec_percentile: Fact  # vs universe on as-of
    lstm_xsec_rank: Fact  # rank from the top (1 = best)
    lstm_xsec_n: Fact  # universe size on as-of
    lstm_score_trend: Fact  # List[float] — last N scores
    lstm_hist_min: Fact
    lstm_hist_max: Fact
    lstm_hist_mean: Fact
    lstm_hist_std: Fact

    # --- static model card (walk-forward constants + honest caveats) --------
    model_card: Fact  # dict

    # --- fundamentals (OPTIONAL — RPT-2 wires the feed later) ---------------
    fundamentals: Fact  # None until wired

    # --- catalysts ----------------------------------------------------------
    news_items: Fact  # List[NewsItem]

    # --- deterministic derived cards (reuses core.specialist.cards) ---------
    pros: Fact  # List[str]
    cons: Fact  # List[str]


# ---------------------------------------------------------------------------
# Dependency-injection seam.
# ---------------------------------------------------------------------------
class FactSetReader(Protocol):
    """The injected data seam. Implementations do the I/O; this module does not.

    All readers return *raw* (possibly post-as-of) data — ``build_fact_set``
    applies the PIT filtering itself, so the filter is testable and lives in one
    place.
    """

    def get_bars(self, symbol: str) -> Sequence[_BarRow]:
        """Return ``(date, close)`` rows (any order)."""

    def get_lstm_series(self, symbol: str) -> Sequence[_ScoreRow]:
        """Return the symbol's ``(date, score)`` history (any order)."""

    def get_lstm_cross_section(self, as_of: date) -> Mapping[str, float]:
        """Return ``{symbol: score}`` for the whole universe on ``as_of``."""

    def get_model_card(self) -> Mapping[str, Any]:
        """Return the static universe walk-forward constants."""

    def get_fundamentals(self, symbol: str, as_of: date) -> Optional[Mapping[str, Any]]:
        """Return PIT fundamentals, or ``None`` when the feed is not wired."""

    def get_news(self, symbol: str) -> Optional[Sequence[NewsItem]]:
        """Return raw catalyst headlines (any order, unfiltered).

        ⚠ ``None`` means NO FEED IS ATTACHED — mirroring ``get_fundamentals``. An empty
        list means a feed WAS asked and nothing qualified. Collapsing the two is how the
        report came to state "no relevant news" while citing a feed it never called.
        """

    def get_company(self, symbol: str) -> Optional[Mapping[str, Any]]:
        """Return company identity facts, or ``None`` when no source is attached.

        ⚠ Three distinct return values, and they are three DIFFERENT claims:
        ``None`` = no company source attached (nobody looked); ``{}`` = a source WAS
        asked and carries nothing for this ticker; a dict = facts (individual fields may
        still be ``None``). Collapsing None and {} with a falsy check is how the report
        came to state a finding it never checked — see ``get_news``.

        Unlike every other reader here this is NOT point-in-time: the endpoint is
        current-state only. The dict therefore carries ``fetched_at`` so the report can
        date the figures rather than pass them off as as-of values.
        """

    def get_alt_signals(self, symbol: str) -> Mapping[str, Any]:
        """Return the ``gathered`` alt-signal dict for ``build_pros_cons``."""


@dataclass(frozen=True)
class InMemoryDataContext:
    """A simple in-memory :class:`FactSetReader` for tests and wiring.

    Holds already-loaded data for a single symbol (plus the universe cross
    section). Production wiring (RPT-5) supplies its own reader over the real
    parquet/GCS/Polygon backends — this class deliberately does no I/O.
    """

    bars: Sequence[_BarRow] = field(default_factory=list)
    lstm_series: Sequence[_ScoreRow] = field(default_factory=list)
    lstm_cross_section: Mapping[str, float] = field(default_factory=dict)
    model_card: Mapping[str, Any] = field(default_factory=dict)
    fundamentals: Optional[Mapping[str, Any]] = None
    # None = no feed attached (the default); [] = a feed answered with nothing.
    news: Optional[Sequence[NewsItem]] = None
    # None = no company source attached (the default); {} = a source answered with
    # nothing. The two must never be collapsed — they are different claims.
    company: Optional[Mapping[str, Any]] = None
    alt_signals: Mapping[str, Any] = field(default_factory=dict)

    def get_bars(self, symbol: str) -> Sequence[_BarRow]:
        return self.bars

    def get_lstm_series(self, symbol: str) -> Sequence[_ScoreRow]:
        return self.lstm_series

    def get_lstm_cross_section(self, as_of: date) -> Mapping[str, float]:
        return self.lstm_cross_section

    def get_model_card(self) -> Mapping[str, Any]:
        return self.model_card

    def get_fundamentals(self, symbol: str, as_of: date) -> Optional[Mapping[str, Any]]:
        return self.fundamentals

    def get_news(self, symbol: str) -> Sequence[NewsItem]:
        return self.news

    def get_company(self, symbol: str) -> Optional[Mapping[str, Any]]:
        return self.company

    def get_alt_signals(self, symbol: str) -> Mapping[str, Any]:
        return self.alt_signals


# ---------------------------------------------------------------------------
# Small deterministic helpers.
# ---------------------------------------------------------------------------
def _as_date(value: Any) -> date:
    """Normalise a ``date`` / ``datetime`` / ISO string to a calendar ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    raise TypeError(f"Unsupported as_of/date type: {type(value)!r}")


def _pit_bars(rows: Sequence[_BarRow], as_of: date) -> List[_BarRow]:
    """Keep bars on/before ``as_of``, sorted ascending by date."""
    kept = [(_as_date(d), float(c)) for d, c in rows if _as_date(d) <= as_of]
    kept.sort(key=lambda r: r[0])
    return kept


def _moving_average(closes: Sequence[float], window: int) -> Optional[float]:
    if len(closes) < window:
        return None
    tail = closes[-window:]
    return sum(tail) / window


def _realized_vol(closes: Sequence[float], window: int) -> Optional[float]:
    """Annualised realized vol from daily simple returns (sample std, ddof=1)."""
    if len(closes) < window + 1:
        return None
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    tail = rets[-window:]
    if len(tail) < 2:
        return None
    return statistics.stdev(tail) * sqrt(_ANNUALIZATION)


def _extreme(rows: Sequence[_BarRow], window: int, *, high: bool) -> Optional[Extreme]:
    """Windowed high/low over the last ``window`` bars (first occurrence wins)."""
    if not rows:
        return None
    win = rows[-window:]
    prices = [c for _, c in win]
    target = max(prices) if high else min(prices)
    idx = prices.index(target)  # first occurrence, matches pandas idxmax/idxmin
    return Extreme(price=target, on=win[idx][0])


def _news_pit(items: Sequence[NewsItem], as_of: date) -> List[NewsItem]:
    """Keep news published on/before ``as_of`` (calendar date), chronological.

    Items whose ``published_utc`` cannot be parsed are dropped (conservative: an
    unverifiable timestamp is treated as an honest gap, never surfaced).
    """
    kept: List[Tuple[datetime, NewsItem]] = []
    for it in items:
        try:
            ts = datetime.fromisoformat(it.published_utc)
        except (ValueError, TypeError):
            continue
        if ts.date() <= as_of:
            kept.append((ts, it))
    kept.sort(key=lambda pair: pair[0])
    return [it for _, it in kept]


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def build_fact_set(symbol: str, as_of: Any, data_ctx: FactSetReader) -> FactSet:
    """Build the deterministic, PIT-filtered :class:`FactSet` for ``symbol``.

    Parameters
    ----------
    symbol:
        Ticker, e.g. ``"NVDA"``.
    as_of:
        The point-in-time cutoff (``date`` / ``datetime`` / ISO string). No fact
        may depend on data after this calendar date.
    data_ctx:
        The injected :class:`FactSetReader` — the only source of data.
    """
    as_of_d = _as_date(as_of)

    # -- price / technicals --------------------------------------------------
    bars = _pit_bars(data_ctx.get_bars(symbol), as_of_d)
    closes = [c for _, c in bars]
    bars_src = f"bars[{symbol}] close, PIT <= {as_of_d.isoformat()}"

    close = Fact(closes[-1] if closes else None, f"{bars_src} (as-of close)")
    pull = closes[-_PULLBACK_LEN:] if len(closes) >= _PULLBACK_LEN else list(closes)
    pull_pct = (pull[-1] / pull[0] - 1.0) * 100.0 if len(pull) >= 2 else None
    pullback_series = Fact(pull, f"{bars_src} (last {_PULLBACK_LEN} closes)")
    pullback_pct = Fact(pull_pct, f"{bars_src} (pct move across the pullback series)")

    mas = {w: _moving_average(closes, w) for w in _MA_WINDOWS}
    ma_facts = {w: Fact(mas[w], f"{bars_src} (MA{w}, simple)") for w in _MA_WINDOWS}

    rvols = {w: _realized_vol(closes, w) for w in _RVOL_WINDOWS}
    rvol_src = f"{bars_src} (annualised realized vol, ddof=1)"

    hi52 = _extreme(bars, _WIN_52W, high=True)
    lo52 = _extreme(bars, _WIN_52W, high=False)
    hi1m = _extreme(bars, _WIN_1M, high=True)
    lo1m = _extreme(bars, _WIN_1M, high=False)

    # -- cross-sectional LSTM ------------------------------------------------
    series = [
        (_as_date(d), float(s))
        for d, s in data_ctx.get_lstm_series(symbol)
        if _as_date(d) <= as_of_d
    ]
    series.sort(key=lambda r: r[0])
    scores = [s for _, s in series]
    lstm_src = f"lstm_panel[{symbol}], PIT <= {as_of_d.isoformat()}"

    lstm_score_v = scores[-1] if scores else None
    own_pct = (
        100.0 * sum(1 for s in scores if s < scores[-1]) / len(scores)
        if scores
        else None
    )
    trend = scores[-_TREND_LEN:] if len(scores) >= _TREND_LEN else list(scores)
    hist_min = min(scores) if scores else None
    hist_max = max(scores) if scores else None
    hist_mean = statistics.fmean(scores) if scores else None
    hist_std = statistics.stdev(scores) if len(scores) >= 2 else None

    xsec = dict(data_ctx.get_lstm_cross_section(as_of_d) or {})
    xsec_src = f"lstm_panel cross-section @ {as_of_d.isoformat()}"
    # ONE definition of "rank", shared with the Round Table's LSTM vote, so the
    # report can never state a standing the board didn't vote on (lstm_panel_store).
    xsec_pct, xsec_rank, n = cross_section_standing(xsec, symbol)

    # -- static model card ---------------------------------------------------
    card = dict(data_ctx.get_model_card() or {})
    card_src = "core/report/model_card.json (static walk-forward constants)"

    # -- fundamentals (OPTIONAL — RPT-2) ------------------------------------
    fundamentals_v = data_ctx.get_fundamentals(symbol, as_of_d)
    if fundamentals_v is None:
        fundamentals = Fact(
            None, "fundamentals feed not wired (RPT-2 delivers it later)"
        )
    else:
        fund_dict = dict(fundamentals_v)
        fund_src = f"polygon vX financials, filing_date <= {as_of_d.isoformat()}"
        # ⚠ P/E COMPLETION SEAM. P/E = price / EPS, so it needs a close the feed
        # never had: the production CachedFundamentalsReader is built once in the
        # reader's __init__, BEFORE any per-symbol bar is fetched, so it computes
        # `pe=None` for every symbol — and valuation_vs_growth_pattern (which reads
        # `fund["pe"]`) could not fire even for NVDA. Here the as-of close and the
        # derived filing meet for the first time, so pe is completed at the honest
        # seam. ONLY when both close and trailing diluted EPS are strictly positive;
        # a missing / <=0 EPS stays an honest gap (no pe=0, no inf, no division by
        # zero) so the detector correctly stays silent. An existing pe (feed WAS
        # given a close via close_lookup) is never overwritten.
        close_v = close.value
        eps_v = fund_dict.get("eps_diluted")
        if (
            fund_dict.get("pe") is None
            and isinstance(close_v, (int, float))
            and not isinstance(close_v, bool)
            and close_v > 0
            and isinstance(eps_v, (int, float))
            and not isinstance(eps_v, bool)
            and eps_v > 0
        ):
            fund_dict["pe"] = close_v / eps_v
            fund_src += (
                f"; pe = as-of close {close_v:.2f} / trailing diluted EPS {eps_v:.2f}"
            )
        fundamentals = Fact(fund_dict, fund_src)

    # -- catalysts -----------------------------------------------------------
    # ⚠ The src used to be set UNCONDITIONALLY, eleven lines below a fundamentals branch
    # that gets this exactly right. So a report with no feed attached stated "Keine
    # relevanten Meldungen im Fenster" AND cited "news feed[NVDA], published_utc <= ..."
    # as its evidence AND passed the audit with 0 flags — for a stock that may well have
    # had five headlines that day. The auditor cannot catch it: it verifies that a [src:]
    # string EXISTS, never that the feed was asked.
    #
    # "Not wired yet" is a building site. "No news, and here is my source" is a false
    # statement wearing a provenance badge — worse, because it reads as diligence.
    #
    # None (no feed) and [] (feed answered, nothing qualified) are DIFFERENT claims and
    # now say so. Note the third case is already honest either way: a feed that returned
    # items which the PIT filter then dropped keeps the citation — that IS a sourced
    # finding about the window.
    news_raw = data_ctx.get_news(symbol)
    if news_raw is None:
        news = []
        news_src = "news feed not wired (no catalyst source attached to the report)"
    else:
        news = _news_pit(news_raw, as_of_d)
        news_src = f"news feed[{symbol}], published_utc <= {as_of_d.isoformat()}"

    # -- company identity (ADR-RPT-COMPANY-01) -------------------------------
    # The report printed "LYB" and went straight to a rank. It never said that
    # LyondellBasell produces petrochemicals. This is that sentence's fact.
    #
    # ⚠ NOT PIT, and said out loud rather than hidden: /v3/reference/tickers is
    # current-state only — no as-of dimension exists. Dropping the fact when it was
    # fetched after as_of would kill it for EVERY historical report (the cache is always
    # warmed now), so instead the fetch date rides along in the provenance and is
    # rendered next to the figures ("Stand <date>"). Name/description/sector barely
    # move; market_cap and employees do, and are dated for exactly that reason.
    #
    # ⚠ Three states, three DIFFERENT claims — never a falsy check. The None/[] collapse
    # shipped twice this week; `is None` is tested FIRST here so the emptiness check
    # below can only ever separate {} from a populated dict.
    company_raw = data_ctx.get_company(symbol)
    if company_raw is None:
        company = Fact(
            None, "company feed not wired (no company source attached to the report)"
        )
    elif len(company_raw) == 0:
        company = Fact(
            {},
            f"polygon reference/tickers[{symbol}] — asked, no company facts returned",
        )
    else:
        fetched = company_raw.get("fetched_at")
        stamp = fetched.isoformat() if isinstance(fetched, date) else "unknown date"
        company = Fact(
            dict(company_raw), f"polygon reference/tickers[{symbol}], fetched {stamp}"
        )

    # -- deterministic derived cards (reuse the #1264 component) -------------
    pros_list, cons_list = build_pros_cons(dict(data_ctx.get_alt_signals(symbol)))
    cards_src = "derived via core.specialist.cards.build_pros_cons (deterministic)"

    return FactSet(
        symbol=symbol,
        as_of=as_of_d,
        company=company,
        close=close,
        pullback_series=pullback_series,
        pullback_pct=pullback_pct,
        ma20=ma_facts[20],
        ma50=ma_facts[50],
        ma100=ma_facts[100],
        ma200=ma_facts[200],
        high_52w=Fact(hi52, f"{bars_src} (52w high over {_WIN_52W} bars)"),
        low_52w=Fact(lo52, f"{bars_src} (52w low over {_WIN_52W} bars)"),
        high_1m=Fact(hi1m, f"{bars_src} (1m high over {_WIN_1M} bars)"),
        low_1m=Fact(lo1m, f"{bars_src} (1m low over {_WIN_1M} bars)"),
        rvol_20d=Fact(rvols[20], rvol_src + " (20d)"),
        rvol_63d=Fact(rvols[63], rvol_src + " (63d)"),
        lstm_score=Fact(lstm_score_v, f"{lstm_src} (score at as-of)"),
        lstm_own_percentile=Fact(own_pct, f"{lstm_src} (percentile vs own history)"),
        lstm_xsec_percentile=Fact(xsec_pct, f"{xsec_src} (percentile vs universe)"),
        lstm_xsec_rank=Fact(xsec_rank, f"{xsec_src} (rank from top, 1=best)"),
        lstm_xsec_n=Fact(n, f"{xsec_src} (universe size)"),
        lstm_score_trend=Fact(trend, f"{lstm_src} (last {_TREND_LEN} scores)"),
        lstm_hist_min=Fact(hist_min, f"{lstm_src} (history min)"),
        lstm_hist_max=Fact(hist_max, f"{lstm_src} (history max)"),
        lstm_hist_mean=Fact(hist_mean, f"{lstm_src} (history mean)"),
        lstm_hist_std=Fact(hist_std, f"{lstm_src} (history std, ddof=1)"),
        model_card=Fact(card, card_src),
        fundamentals=fundamentals,
        news_items=Fact(news, news_src),
        pros=Fact(pros_list, cards_src),
        cons=Fact(cons_list, cards_src),
    )
