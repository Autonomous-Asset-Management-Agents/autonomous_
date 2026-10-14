"""#3145 (bundle) — ship a pre-derived fundamentals cache so the agents work
**offline by default**.

The nightly SEC producer warms ``data/financials_cache/<SYMBOL>.json``. To make the
desktop functional on first run (no network, no key), a single gzipped snapshot of
that cache is bundled and expanded once on a cold cache. The snapshot is a plain
mapping ``{SYMBOL: cache_payload}`` — the exact per-symbol payload the reader already
consumes — so seeding is a pure file expand, and a later nightly run supersedes it.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

# The read-only snapshot ships in the app bundle (CWD-relative — always present in
# every install, re-extracted on upgrade). The writable cache it seeds lives under
# AAA_USER_DATA_DIR (persistent) — see financials_feed._resolve_cache_dir.
DEFAULT_SNAPSHOT_PATH = os.path.join("data", "financials_snapshot.json.gz")


def _open_text(path: str, mode: str):
    return (
        gzip.open(path, mode, encoding="utf-8")
        if str(path).endswith(".gz")
        else open(path, mode, encoding="utf-8")
    )


def build_snapshot(cache_dir: str, out_path: str) -> int:
    """Pack every ``<SYMBOL>.json`` in ``cache_dir`` into one snapshot at ``out_path``.

    ``out_path`` ending in ``.gz`` is gzip-compressed. Returns the symbol count.
    """
    snap: Dict[str, Any] = {}
    if os.path.isdir(cache_dir):
        for name in sorted(os.listdir(cache_dir)):
            if not name.endswith(".json"):
                continue
            symbol = name[:-5].upper()
            try:
                with open(os.path.join(cache_dir, name), encoding="utf-8") as handle:
                    snap[symbol] = json.load(handle)
            except (OSError, ValueError) as exc:
                logger.warning("snapshot: skipping unreadable %s (%s).", name, exc)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with _open_text(out_path, "wt") as handle:
        json.dump(snap, handle)
    return len(snap)


def load_snapshot(path: str) -> Optional[Mapping[str, Any]]:
    if not path or not os.path.exists(path):
        return None
    try:
        with _open_text(path, "rt") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("snapshot: unreadable snapshot %s (%s).", path, exc)
        return None


def seed_cache_from_snapshot(snapshot_path: str, cache_dir: str) -> int:
    """Expand a bundled snapshot into the per-symbol cache — ONLY on a cold cache.

    Never overwrites an existing cache (a nightly refresh is always fresher than the
    bundled snapshot). Returns the number of symbols seeded (0 when the cache is
    already warm or no snapshot is present).
    """
    snap = load_snapshot(snapshot_path)
    if not snap:
        return 0
    os.makedirs(cache_dir, exist_ok=True)
    if any(name.endswith(".json") for name in os.listdir(cache_dir)):
        return 0  # cache already warm — leave it
    seeded = 0
    for symbol, payload in snap.items():
        path = os.path.join(cache_dir, f"{str(symbol).strip().upper()}.json")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            seeded += 1
        except OSError as exc:
            logger.warning("snapshot: could not seed %s (%s).", symbol, exc)
    logger.info(
        "snapshot: seeded %d symbols into a cold cache from %s.", seeded, snapshot_path
    )
    return seeded


def seed_bundled_fundamentals(
    cache_dir: Optional[str] = None, snapshot_path: Optional[str] = None
) -> int:
    """Boot convenience: seed the persistent cache from the bundled snapshot.

    Resolves the writable cache dir (AAA_USER_DATA_DIR on desktop) and the bundled
    snapshot path by default. Cold-cache-only + best-effort — a missing snapshot or
    an already-warm cache returns 0 and never raises.
    """
    if cache_dir is None:
        from core.report.financials_feed import DEFAULT_CACHE_DIR

        cache_dir = DEFAULT_CACHE_DIR
    return seed_cache_from_snapshot(snapshot_path or DEFAULT_SNAPSHOT_PATH, cache_dir)
