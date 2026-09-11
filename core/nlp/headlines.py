# core/nlp/headlines.py
# RTR-1 (#1948, Epic #1958) — FREE point-in-time news producer (Google-News-RSS).
#
# Fills SymbolEvalState.news_headlines for the NewsSentimentAgent's NLP path:
#   [{"title": str, "published_utc": ISO-8601 str, "source": str}, ...]
#
# Guarantees:
#   * Flag-FIRST: NEWS_SENTIMENT_NLP_ENABLED off → None WITHOUT any network I/O
#     (no httpx client constructed) — the dormant path is byte-neutral.
#     NOTE (#2632): the flag now defaults to **True** in both editions (config.py:1170 /
#     config.oss.py:1140) — this header said "off (default)" long after the rollout, which
#     is how the sim's live-network leak below stayed invisible in review.
#   * OFFLINE under SIM_MODE: a Sim-Day run returns [] without any network I/O, see
#     produce_news_headlines. NOT a change to live/paper behaviour — the feature stays on
#     there; SIM is the only mode with the extra constraint.
#   * Point-in-time: ONLY items with published_utc <= current_time survive; items
#     without a parseable pubDate are DROPPED (cannot prove point-in-time →
#     precision over recall, plan §12.3/§12.6 — no look-ahead leakage, ever).
#   * Free source ONLY: Google-News-RSS (mirrors stock_specialist._fetch_google_news,
#     RPAR T3 #1267). No paid news API is ever touched (feedback_no_paid_apis) —
#     pinned by the static guard in tests/unit/test_news_sentiment_nlp.py.
#   * Fail-transparent: any fetch/parse error degrades to [] at WARNING (§5.6)
#     — downstream the agent then abstains (weight 0.0, EXCLUDED from the
#     consensus), i.e. the signal fails CLOSED out of the vote; a news gap
#     never approves anything and never costs a cycle.
#
# Edition-neutral finance-core: config ONLY via config.get_config() (§2.10).

from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional
from urllib.parse import quote

import config

logger = logging.getLogger(__name__)

_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.DOTALL)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_PUBDATE_RE = re.compile(r"<pubDate>(.*?)</pubDate>", re.DOTALL)
_SOURCE_RE = re.compile(r"<source[^>]*>(.*?)</source>", re.DOTALL)

# ADR-NS-05 (#1948): per-symbol RSS TTL cache, 300s — the SAME 5-min posture as
# the agent's sentiment cache (agents.py _CACHE_TTL): protects the free RSS
# endpoint from once-per-cycle hammering (plan §12.2) while keeping news at most
# one cycle stale. Bounded like _LOCAL_SENTIMENT_CACHE (cap 512 ≈ S&P 500).
# The cache stores the PARSED, UNfiltered items; the point-in-time filter runs
# on EVERY read (current_time advances between cycles) so a cached fetch can
# never leak a future headline into an earlier evaluation. Annual review.
_RSS_CACHE_TTL_SEC = 300.0
_RSS_CACHE_MAXSIZE = 512
_HEADLINES_CACHE: Dict[str, tuple[float, List[Dict[str, str]]]] = {}

# ADR-NS-06 (#1948): the RSS query is "<SYMBOL> stock" (not the bare ticker) —
# raises symbol→article mapping precision for ambiguous tickers ("ALL", "ON",
# plan §12.3); URL-encoded so special-char tickers (BRK.A, BF-B) cannot corrupt
# the query string (RPAR T3 review #1312 F-03 precedent). Annual review.
_RSS_URL_TEMPLATE = (
    "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
)
_FETCH_TIMEOUT_SEC = 8.0  # mirrors stock_specialist._fetch_google_news


def _sim_mode() -> bool:
    """Offline Sim-Day run? Call-time, env-first, fail-safe to LIVE (#2632).

    Delegates to the single shared definition (``core.sim.is_sim_mode``) so this module
    cannot drift from the other SIM-gated components. Imported lazily and defensively:
    the news producer must never be the reason a cycle dies, and a missing/broken sim
    package means LIVE (fetch as before).
    """
    try:
        from core.sim import is_sim_mode

        return is_sim_mode()
    except Exception:  # noqa: BLE001 — fail-safe direction is LIVE
        return False


async def _fetch_rss(url: str) -> str:
    """Fetch the RSS feed body (isolated for testability — patched in tests)."""
    import httpx  # lazy: keep module import light for the engine hot path

    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SEC) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            raise OSError(f"Google news RSS non-200: {resp.status_code}")
        return resp.text


def parse_rss_items(xml_text: str) -> List[Dict[str, str]]:
    """Parse RSS <item> blocks into {title, published_utc, source} dicts.

    Defensive regex parse (no XML deps — the stock_specialist precedent).
    Items without a title OR without a parseable pubDate are dropped: a
    headline whose publication time cannot be proven must never enter a
    point-in-time evaluation (precision over recall).
    """
    items: List[Dict[str, str]] = []
    for block in _ITEM_RE.findall(xml_text):
        title_m = _TITLE_RE.search(block)
        pubdate_m = _PUBDATE_RE.search(block)
        if not title_m or not pubdate_m:
            continue
        title = html.unescape(title_m.group(1).strip())
        if not title:
            continue
        try:
            published = parsedate_to_datetime(pubdate_m.group(1).strip())
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        source_m = _SOURCE_RE.search(block)
        source = html.unescape(source_m.group(1).strip()) if source_m else "?"
        items.append(
            {
                "title": title,
                "published_utc": published.astimezone(timezone.utc).isoformat(),
                "source": source or "?",
            }
        )
    return items


def filter_point_in_time(
    items: List[Dict[str, str]], current_time: datetime
) -> List[Dict[str, str]]:
    """Keep ONLY items with published_utc <= current_time (no look-ahead)."""
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    kept: List[Dict[str, str]] = []
    for item in items:
        try:
            published = datetime.fromisoformat(item["published_utc"])
        except (KeyError, TypeError, ValueError):
            continue  # unprovable timestamp → drop (precision over recall)
        if published <= current_time:
            kept.append(item)
    return kept


def _dedup(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Case-insensitive title dedup, first occurrence wins (merge_headlines rule)."""
    seen: set = set()
    out: List[Dict[str, str]] = []
    for item in items:
        key = item["title"].strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


async def produce_news_headlines(
    symbol: str, current_time: datetime
) -> Optional[List[Dict[str, str]]]:
    """Point-in-time headlines for `symbol`, or None while the feature is OFF.

    Returns:
        None  — NEWS_SENTIMENT_NLP_ENABLED off (default): caller must NOT add
                the state key (dormant path byte-identical, no network I/O).
        []    — flag on but fetch failed / no point-in-time headlines: the
                agent sees the gap and abstains transparently (w=0 + WARNING).
        [...] — deduped, point-in-time-filtered, capped headline dicts.
    """
    cfg = config.get_config()
    if not getattr(cfg, "NEWS_SENTIMENT_NLP_ENABLED", False):
        return None

    # #2632 — SIM network isolation. Google-News-RSS is a LIVE endpoint, and this flag
    # defaults to True, so an offline Sim-Day run was firing one real HTTP request per
    # evaluated symbol (~500 on the full corpus). That breaks both sim contracts:
    #   1. the pinned parquet corpus must be the ONLY data source of a run;
    #   2. determinism — today's headlines for a REPLAYED past date are look-ahead by
    #      construction, and two arms of an A/B would read different news minutes apart,
    #      so the comparison could be decided by the news cycle instead of by the
    #      parameter under test.
    # Returns [] (not None): the channel exists but is empty, so NewsSentimentAgent
    # abstains transparently (w=0, EXCLUDED from consensus) exactly as on a fetch failure
    # — a documented sim-fidelity limitation, not a silent substitution. Replaying real
    # point-in-time news would need news snapshots IN the corpus (future increment).
    # LIVE/PAPER IS UNTOUCHED: this gate is SIM-only.
    if _sim_mode():
        logger.info(
            "news headlines: SIM_MODE → offline, no RSS fetch for %s "
            "(NewsSentiment abstains this cycle; corpus carries no news).",
            symbol,
        )
        return []

    try:
        max_n = int(getattr(cfg, "NEWS_SENTIMENT_MAX_HEADLINES", 8) or 8)
    except (TypeError, ValueError):
        max_n = 8

    now_mono = time.monotonic()
    cached = _HEADLINES_CACHE.get(symbol)
    if cached is not None and cached[0] > now_mono:
        items = cached[1]
    else:
        try:
            url = _RSS_URL_TEMPLATE.format(query=quote(f"{symbol} stock"))
            items = _dedup(parse_rss_items(await _fetch_rss(url)))
        except Exception as exc:  # noqa: BLE001 — news must never cost a cycle
            logger.warning(
                "news headlines: Google-News-RSS fetch failed for %s (%s: %.120s) "
                "→ [] (agent will abstain this cycle).",
                symbol,
                type(exc).__name__,
                exc,
            )
            return []
        # bounded cache write (purge expired, cap size — _LOCAL_SENTIMENT_CACHE idiom)
        for expired in [
            k for k, (exp, _) in _HEADLINES_CACHE.items() if exp <= now_mono
        ]:
            _HEADLINES_CACHE.pop(expired, None)
        if len(_HEADLINES_CACHE) >= _RSS_CACHE_MAXSIZE:
            soonest = min(_HEADLINES_CACHE, key=lambda k: _HEADLINES_CACHE[k][0])
            _HEADLINES_CACHE.pop(soonest, None)
        _HEADLINES_CACHE[symbol] = (now_mono + _RSS_CACHE_TTL_SEC, items)

    return filter_point_in_time(items, current_time)[:max_n]


def _reset_cache_for_tests() -> None:
    """Clear the module-level RSS cache (test isolation only)."""
    _HEADLINES_CACHE.clear()
