# core/specialist/form4_direction.py
"""RQ-1 B3b (#1536): derive insider buy/sell DIRECTION from a Form 4 document.

The EDGAR full-text index (what B1 queries) carries no transaction code, so the raw Form 4
filing document (form4.xml) must be fetched + parsed. We read the non-derivative transaction
codes: ``P`` (open-market purchase) is a bullish buy and ``S`` (sale) a bearish sell; ``A``
(grant), ``M`` (option exercise), ``G`` (gift), ``F`` (tax withholding) etc. are compensation
/ mechanics, NOT a market-direction signal. The net of P vs S shares gives the direction.

The parser is regex-based on a TARGETED extraction (no XML entity expansion -> no XXE on the
untrusted document). All call sites are flag-gated (SPECIALIST_FORM4_DIRECTION_ENABLED).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_TXN_BLOCK = re.compile(
    r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", re.S | re.I
)
_CODE = re.compile(r"<transactionCode>\s*([A-Za-z])\s*</transactionCode>", re.I)
_SHARES = re.compile(
    r"<transactionShares>\s*<value>\s*([\d,.]+)\s*</value>", re.S | re.I
)


def parse_form4_direction(xml: str) -> str:
    """Classify a Form 4 document as ``buy`` / ``sell`` / ``mixed`` / ``neutral`` from its
    non-derivative P (purchase) vs S (sale) transactions, weighted by shares."""
    buy = sell = 0.0
    for block in _TXN_BLOCK.findall(xml or ""):
        m_code = _CODE.search(block)
        if not m_code:
            continue
        code = m_code.group(1).upper()
        m_sh = _SHARES.search(block)
        try:
            shares = float(m_sh.group(1).replace(",", "")) if m_sh else 0.0
        except ValueError:
            shares = 0.0
        if code == "P":
            buy += shares
        elif code == "S":
            sell += shares
        # A/M/G/F/... are compensation/mechanics -> not a market-direction signal
    if buy == 0.0 and sell == 0.0:
        return "neutral"
    if buy > sell:
        return "buy"
    if sell > buy:
        return "sell"
    return "mixed"


async def classify_form4_direction(client: Any, url: str) -> str:
    """Fetch a single ``form4.xml`` and classify its direction. Returns ``neutral`` on ANY
    failure -- a best-effort enrichment must degrade to no-signal, never crash a research
    cycle. ``client`` is an open ``httpx.AsyncClient`` (caller owns its lifecycle)."""
    try:
        headers = {"User-Agent": "AI-Trading-Bot research@aaagents.de"}
        r = await client.get(url, headers=headers)
        if r.status_code != 200:
            return "neutral"
        return parse_form4_direction(r.text)
    except Exception as e:  # noqa: BLE001 -- direction is best-effort, never fatal
        logger.warning("form4 direction fetch failed (%s): %s", url, e)
        return "neutral"
