"""Map Polygon's ``/v2/reference/news`` articles into report ``NewsItem``s.

WHY THIS MODULE EXISTS
----------------------
``EngineFactSetReader.get_news()`` returned ``None`` for every symbol because no
production caller ever passed a feed. #2151 made that state *honest* — §5 says "keine
Nachrichtenquelle angebunden" instead of claiming "no relevant news" while citing a feed
nobody called. This module is the other half: it makes the feed *present*.

⚠ THE SHAPE TRAP — the fixture is not the producer
--------------------------------------------------
``tests/fixtures/report/nvda_news.json`` is ALREADY MAPPED: it carries exactly
``{title, published_utc, publisher, url}``, i.e. ``NewsItem``'s own fields. The live
endpoint does **not** look like that:

    live                                    NewsItem
    ────────────────────────────────────    ──────────────
    publisher: {"name": "Reuters", ...}  →  publisher: "Reuters"     (dict, not str!)
    article_url: "https://..."           →  url: "https://..."       (no `url` key!)
    published_utc: "...T17:27:00Z"       →  published_utc (Z-suffix, not "+00:00")

A mapper written against that fixture renders a Python dict where the publisher belongs
and drops every link. This is the same failure that shipped the Fundamentals/Valuation
agents dead earlier today: the consumer was written against an invented schema, and the
tests passed because the fixture agreed with the invention. So the mapping below is
pinned to the *live* response, verified against the real endpoint, and
``tests/unit/test_report_news_reader.py`` asserts each of the three divergences by name.

WHAT IS DROPPED, AND WHY
------------------------
An article without a title, a timestamp, or a link is dropped at this boundary rather
than rendered. §5 renders every headline as a sourced bullet the reader can open; an
item that cannot carry its provenance is not a catalyst but a rumour, and the auditor
would strike it anyway. Failing at the boundary beats failing in the report.

The publisher is the exception: it is *attribution*, not *provenance* — the URL carries
that — so a missing one degrades to "unbekannt" rather than killing the item.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from core.report.fact_set import NewsItem

logger = logging.getLogger(__name__)

# Attribution we could not resolve. Deliberately NOT "" and not a guess: the reader must
# be able to see that we do not know who published this, not silently assume we do.
_UNKNOWN_PUBLISHER = "unbekannt"


def _publisher_name(raw: Any) -> str:
    """Flatten Polygon's publisher.

    Live shape is ``{"name": ..., "homepage_url": ..., "logo_url": ...}``. Passing it
    straight through stringifies the whole dict into the report. Some caches (and our
    own fixtures) already store it flattened, so a plain string is accepted too.
    """
    if isinstance(raw, Mapping):
        name = raw.get("name")
        return (
            name.strip()
            if isinstance(name, str) and name.strip()
            else _UNKNOWN_PUBLISHER
        )
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return _UNKNOWN_PUBLISHER


def map_polygon_article(raw: Any) -> Optional[NewsItem]:
    """Return a provenanced ``NewsItem``, or ``None`` if the article cannot carry one.

    Never raises: a malformed article must not take down a 500-symbol report run. The
    caller filters ``None``s out.
    """
    if not isinstance(raw, Mapping):
        return None

    title = raw.get("title")
    published = raw.get("published_utc")
    # ⚠ `article_url` is the live field name. `url` is accepted only as a fallback for
    # already-mapped payloads (our fixtures, a warm cache) — the live API never sends it.
    url = raw.get("article_url") or raw.get("url")

    for field, value in (
        ("title", title),
        ("published_utc", published),
        ("article_url", url),
    ):
        if not isinstance(value, str) or not value.strip():
            logger.debug("news article dropped: %s missing or blank", field)
            return None

    return NewsItem(
        title=title.strip(),
        published_utc=published.strip(),
        publisher=_publisher_name(raw.get("publisher")),
        url=url.strip(),
    )
