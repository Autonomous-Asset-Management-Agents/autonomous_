# tests/unit/test_edgar_cik_snapshot.py
"""RQ-1 B1 (#1521): the committed cold-start snapshot must be real + parseable.

Guards against an empty/corrupt company_tickers_snapshot.json silently disabling offline
EDGAR resolution (every ticker would resolve to None -> degraded for the whole universe).
"""

from __future__ import annotations

import json

from core.specialist import edgar_cik


def test_snapshot_parses_and_has_common_tickers():
    raw = json.loads(edgar_cik._SNAPSHOT_PATH.read_text(encoding="utf-8"))
    m = edgar_cik._parse(raw)
    assert m, "bundled snapshot parsed empty"
    assert len(m) > 5000, "snapshot suspiciously small — expected the full ~10k SEC map"
    assert m.get("AAPL") == "0000320193"
    assert m.get("MSFT") and len(m["MSFT"]) == 10
